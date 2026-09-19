"""Opt-in provider that contains ONNX failures under the existing render budget."""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import secrets
import selectors
import struct
import subprocess
import sys
import threading
import time
from contextlib import ExitStack
from pathlib import Path

from printstash_core.inference import EmbeddingError, EmbeddingInput, EmbeddingSpace
from printstash_core.inference.context import InferenceContext
from printstash_core.inference.vectors import normalize
from pydantic import ValidationError
from sqlmodel import Session

from app import __file__ as application_file
from app.core.config import settings
from app.db.session import SessionFactory
from app.modules.inference.manifest import (
    ModelManifest,
    manifest_identity,
    read_manifest,
    validate_space,
)
from app.modules.inference.model_cache import pin, safe_directory
from app.modules.inference.worker_pool import pool
from app.modules.inference.worker_protocol import (
    MAX_INPUT_BYTES,
    MAX_OUTPUT_BYTES,
    WorkerError,
    WorkerResult,
)
from app.modules.media import compute_slots

_admission_lock = threading.Lock()
_waiting_queries = 0


def acquire_slot(session: Session, token: str, context: InferenceContext):
    """Queries wait inside their deadline; background work yields to waiters.

    The durable media lease remains the single authority for compute capacity.
    This process-local hint orders admission without creating another queue.
    """
    global _waiting_queries
    interactive = context.priority == "interactive"
    with _admission_lock:
        if interactive:
            _waiting_queries += 1
        elif _waiting_queries:
            raise EmbeddingError("embedding_compute_busy")
    try:
        while True:
            context.remaining()
            slot = compute_slots.acquire(session, token)
            if slot is not None:
                return slot
            if not interactive:
                raise EmbeddingError("embedding_compute_busy")
            session.rollback()
            time.sleep(min(0.025, context.remaining()))
    finally:
        if interactive:
            with _admission_lock:
                _waiting_queries -= 1


class LocalEmbeddingProvider:
    def __init__(
        self,
        sessions: SessionFactory,
        directory: Path,
        model_key: str,
        threads: int,
        *,
        space: EmbeddingSpace | None = None,
    ):
        if not 1 <= threads <= 4:
            raise EmbeddingError("embedding_thread_budget_invalid")
        self.sessions = sessions
        self.directory = safe_directory(directory)
        self.model_key = model_key
        self.threads = threads
        self.manifest = read_manifest(self.directory, model_key)
        self.space = space or self.manifest.space()
        validate_space(self.manifest, self.space)
        self._last_batch = threading.local()

    @property
    def last_truncations(self) -> tuple[bool, ...]:
        return getattr(self._last_batch, "truncations", ())

    def validate(self, *, context: InferenceContext | None = None) -> ModelManifest:
        self._execute((), context=context)
        return self.manifest

    @property
    def is_warm(self) -> bool:
        return pool.is_warm(self._worker_key())

    def prepare_query(self) -> None:
        if not self.is_warm:
            from app.modules.inference.warmup import requests

            requests.request(manifest_identity(self.manifest))
            raise EmbeddingError("embedding_model_warming")

    def _worker_key(self) -> tuple:
        try:
            fingerprints = tuple(
                (asset.filename, stat.st_ino, stat.st_mtime_ns, stat.st_size)
                for asset in self.manifest.assets()
                for stat in ((self.directory / asset.filename).stat(),)
            )
        except OSError:
            raise EmbeddingError("embedding_asset_unavailable") from None
        return (
            str(self.directory),
            manifest_identity(self.manifest),
            self.threads,
            fingerprints,
        )

    def embed(
        self,
        inputs: tuple[EmbeddingInput, ...],
        space: EmbeddingSpace,
        *,
        context: InferenceContext | None = None,
    ) -> tuple[tuple[float, ...], ...]:
        if space != self.space:
            raise EmbeddingError("embedding_space_mismatch")
        if not 1 <= len(inputs) <= 8:
            raise EmbeddingError("embedding_batch_budget")
        if space.modality == "image" and any(
            item.modality == "text" for item in inputs
        ):
            raise EmbeddingError("embedding_text_unavailable")
        return self._execute(inputs, context=context)

    @staticmethod
    def _release_slot(session: Session, slot_id: int | None, token: str) -> None:
        compute_slots.release(session, slot_id, token)
        session.commit()

    def _execute(
        self,
        inputs: tuple[EmbeddingInput, ...],
        *,
        context: InferenceContext | None = None,
    ) -> tuple[tuple[float, ...], ...]:
        request_inputs = [
            {
                "modality": item.modality,
                "text": item.text,
                "width": item.width,
                "height": item.height,
                "rgb_base64": base64.b64encode(item.rgb).decode("ascii")
                if item.rgb is not None
                else None,
                "points_base64": base64.b64encode(item.points).decode("ascii")
                if item.points is not None
                else None,
            }
            for item in inputs
        ]
        payload = json.dumps(
            {
                "config_hash": self.space.config_hash,
                "space_json": json.dumps(self.space.__dict__),
                "inputs": request_inputs,
            }
        ).encode()
        output = self._request(payload, context=context)
        try:
            result = WorkerResult.model_validate_json(output)
        except ValidationError:
            try:
                error = WorkerError.model_validate_json(output)
            except ValidationError:
                raise EmbeddingError("embedding_output_invalid") from None
            raise EmbeddingError(error.code) from None
        if result.config_hash != self.space.config_hash or len(result.vectors) != len(
            inputs
        ):
            raise EmbeddingError("embedding_output_mismatch")
        if len(result.truncated) != len(inputs):
            raise EmbeddingError("embedding_output_mismatch")
        self._last_batch.truncations = tuple(result.truncated)
        for vector in result.vectors:
            normalize(vector, self.space.dimension)
        pool.mark_warm(self._worker_key())
        return tuple(tuple(vector) for vector in result.vectors)

    def _request(
        self, payload: bytes, *, context: InferenceContext | None = None
    ) -> bytes:
        if context is not None:
            context.remaining()
        if importlib.util.find_spec("onnxruntime") is None:
            raise EmbeddingError("embedding_runtime_unavailable")
        with ExitStack() as cleanup:
            cleanup.enter_context(pin(self.directory, context=context))
            session = cleanup.enter_context(self.sessions.scoped_session())
            token = "embedding:" + secrets.token_hex(20)
            admission_context = context or InferenceContext.bounded(
                120, priority="background"
            )
            slot = acquire_slot(session, token, admission_context)
            cleanup.callback(self._release_slot, session, slot.id, token)
            if len(payload) > MAX_INPUT_BYTES:
                raise EmbeddingError("embedding_input_budget")
            key = self._worker_key()
            with pool.acquire(
                key, self.directory, self._spawn, admission_context
            ) as process:
                try:
                    output = self._exchange(process, payload, context)
                except EmbeddingError:
                    raise
                except (OSError, ValueError):
                    raise EmbeddingError("embedding_inference_failed") from None
                if self.directory.parent == settings.embedding_cache_dir.absolute():
                    try:
                        os.utime(self.directory, None)
                    except OSError:
                        pass  # Read-only offline mounts still support inference.
                return output

    def _spawn(self) -> subprocess.Popen:
        env = os.environ.copy()
        env.update(
            OMP_NUM_THREADS=str(self.threads),
            OPENBLAS_NUM_THREADS=str(self.threads),
            TOKENIZERS_PARALLELISM="false",
            HF_HUB_OFFLINE="1",
            TRANSFORMERS_OFFLINE="1",
        )
        return subprocess.Popen(
            [
                sys.executable,
                "-m",
                "app.modules.inference.worker",
                str(self.directory),
                self.model_key,
                str(self.threads),
                "--persistent",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            cwd=Path(application_file).resolve().parent.parent,
        )

    @staticmethod
    def _exchange(
        process: subprocess.Popen, payload: bytes, context: InferenceContext | None
    ) -> bytes:
        """Monitor both pipe directions without blocking on a stalled child."""
        assert process.stdin is not None and process.stdout is not None
        deadline = time.monotonic() + min(settings.mesh_step_timeout_seconds, 90)
        budget = compute_slots.native_memory_budget_bytes()
        result = bytearray()
        framed = struct.pack("!I", len(payload)) + payload
        offset = 0
        expected = None
        with selectors.DefaultSelector() as selector:
            os.set_blocking(process.stdin.fileno(), False)
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE)
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                if context is not None:
                    context.remaining()
                pool.enforce_memory_budget(
                    process, budget, compute_slots.native_process_rss_bytes
                )
                if time.monotonic() >= deadline:
                    raise EmbeddingError("embedding_timeout")
                if process.poll() is not None:
                    raise EmbeddingError(
                        "embedding_worker_oom"
                        if process.returncode == -9
                        else "embedding_inference_failed"
                    )
                for key, _ in selector.select(0.05):
                    if key.fileobj is process.stdin:
                        try:
                            offset += os.write(key.fd, framed[offset : offset + 65536])
                        except BrokenPipeError:
                            raise EmbeddingError("embedding_inference_failed") from None
                        if offset == len(framed):
                            selector.unregister(process.stdin)
                    else:
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            raise EmbeddingError("embedding_inference_failed")
                        result.extend(chunk)
                        if len(result) >= 4 and expected is None:
                            expected = struct.unpack("!I", result[:4])[0]
                            if expected > MAX_OUTPUT_BYTES:
                                raise EmbeddingError("embedding_output_budget")
                        if expected is not None and len(result) >= expected + 4:
                            if len(result) != expected + 4:
                                raise EmbeddingError("embedding_output_invalid")
                            return bytes(result[4:])


def configured_provider(sessions: SessionFactory) -> LocalEmbeddingProvider:
    if not settings.embedding_local_model_dir or not settings.embedding_model_key:
        raise EmbeddingError("embedding_not_configured")
    return LocalEmbeddingProvider(
        sessions,
        Path(settings.embedding_local_model_dir),
        settings.embedding_model_key,
        settings.embedding_onnx_threads,
    )
