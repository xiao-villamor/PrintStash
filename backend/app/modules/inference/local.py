"""Opt-in ONNX provider with bounded, process-shared warm-model residency."""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import selectors
import struct
import subprocess
import threading
import time
from contextlib import ExitStack
from pathlib import Path

from printstash_core.inference import EmbeddingError, EmbeddingInput, EmbeddingSpace
from printstash_core.inference.context import InferenceContext
from printstash_core.inference.vectors import normalize
from pydantic import ValidationError

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
    RETIREMENT_EXIT,
    WorkerError,
    WorkerResult,
)
from app.modules.media.native_process import process_tree_rss_bytes
from app.modules.media.worker_bootstrap import RESOURCE_EXIT, command
from app.runtime import inference_resources
from app.runtime.preparation_runtime import PERMIT_ENV as PREPARATION_ENV
from app.runtime.preparation_runtime import PERMIT_PATH_ENV as PREPARATION_PATH_ENV

_admission = threading.Condition()
_waiting_queries = 0
_running = 0


def _limit() -> int:
    return max(int(settings.max_render_jobs), 1)


def acquire_slot(context: InferenceContext) -> None:
    """Admit one inference in this process; queries go before background work.

    Background inference already runs inside a Job whose lane bounds it across
    the deployment; this bounds what one process runs at once (queries run in
    requests, outside any lane) and lets a waiting query go first. A query
    waits inside its deadline; background work that would wait yields with
    ``embedding_compute_busy`` and its Job retries later. Pair with
    ``release_slot``.
    """
    global _waiting_queries, _running
    interactive = context.priority == "interactive"
    with _admission:
        if not interactive:
            if _waiting_queries or _running >= _limit():
                raise EmbeddingError("embedding_compute_busy")
            _running += 1
            return
        _waiting_queries += 1
        try:
            while _running >= _limit():
                _admission.wait(min(0.025, context.remaining()))
                context.remaining()
            _running += 1
        finally:
            _waiting_queries -= 1


def release_slot() -> None:
    global _running
    with _admission:
        _running -= 1
        _admission.notify_all()


class _WorkerRetired(Exception):
    """The child voluntarily released residency before any reply was accepted."""


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
            admission_context = context or InferenceContext.bounded(
                120, priority="background"
            )
            acquire_slot(admission_context)
            cleanup.callback(release_slot)
            if len(payload) > MAX_INPUT_BYTES:
                raise EmbeddingError("embedding_input_budget")
            key = self._worker_key()
            exchange_deadline: float | None = None
            for attempt in range(2):
                admission_context.remaining()
                if (
                    exchange_deadline is not None
                    and time.monotonic() >= exchange_deadline
                ):
                    raise EmbeddingError("embedding_timeout")
                try:
                    with pool.acquire(
                        key,
                        self.directory,
                        lambda deadline=exchange_deadline: self._spawn(
                            context=admission_context, deadline=deadline
                        ),
                        admission_context,
                        deadline=exchange_deadline,
                    ) as process:
                        if exchange_deadline is None:
                            exchange_deadline = time.monotonic() + min(
                                settings.mesh_step_timeout_seconds, 90
                            )
                        output = self._exchange(
                            process,
                            payload,
                            admission_context,
                            deadline=exchange_deadline,
                        )
                except _WorkerRetired:
                    if attempt == 1:
                        raise EmbeddingError("embedding_compute_busy") from None
                    continue
                except EmbeddingError:
                    raise
                except OSError, ValueError:
                    raise EmbeddingError("embedding_inference_failed") from None
                if self.directory.parent == settings.embedding_cache_dir.absolute():
                    try:
                        os.utime(self.directory, None)
                    except OSError:
                        pass  # Read-only offline mounts still support inference.
                return output
            raise RuntimeError("inference attempts exhausted without an outcome")

    def _spawn(
        self, *, context: InferenceContext | None = None, deadline: float | None = None
    ) -> subprocess.Popen:
        context = context or InferenceContext.bounded(120, priority="background")
        env = os.environ.copy()
        env.pop(PREPARATION_ENV, None)
        env.pop(PREPARATION_PATH_ENV, None)
        env.update(
            OMP_NUM_THREADS=str(self.threads),
            OPENBLAS_NUM_THREADS=str(self.threads),
            TOKENIZERS_PARALLELISM="false",
            HF_HUB_OFFLINE="1",
            TRANSFORMERS_OFFLINE="1",
        )

        def checkpoint() -> None:
            context.remaining()
            if deadline is not None and time.monotonic() >= deadline:
                raise EmbeddingError("embedding_timeout")

        with inference_resources.reserve(checkpoint=checkpoint) as permit:
            resources = inference_resources.launch_resources(permit)
            env.update(resources.environment)
            return subprocess.Popen(
                command(
                    "app.modules.inference.worker",
                    [
                        str(self.directory),
                        self.model_key,
                        str(self.threads),
                        "--persistent",
                    ],
                    permit.resources.bytes,
                ),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
                cwd=Path(application_file).resolve().parent.parent,
                pass_fds=resources.descriptors,
                start_new_session=True,
            )

    @staticmethod
    def _exchange(
        process: subprocess.Popen,
        payload: bytes,
        context: InferenceContext | None,
        *,
        deadline: float | None = None,
    ) -> bytes:
        """Drain replies before classifying exit; partial replies are never replayed."""
        assert process.stdin is not None and process.stdout is not None
        exchange_limit = (
            deadline
            if deadline is not None
            else time.monotonic() + min(settings.mesh_step_timeout_seconds, 90)
        )
        budget = inference_resources.worker_memory_budget_bytes()
        result = bytearray()
        framed = struct.pack("!I", len(payload)) + payload
        offset = 0
        expected: int | None = None

        def receive() -> bytes | None:
            nonlocal expected
            assert process.stdout is not None
            try:
                chunk = os.read(process.stdout.fileno(), 65536)
            except BlockingIOError:
                return None
            if not chunk:
                raise _worker_failure(process, has_response=bool(result))
            result.extend(chunk)
            if len(result) >= 4 and expected is None:
                expected = struct.unpack("!I", result[:4])[0]
                if expected > MAX_OUTPUT_BYTES:
                    raise EmbeddingError("embedding_output_budget")
            if expected is not None and len(result) >= expected + 4:
                if len(result) != expected + 4:
                    raise EmbeddingError("embedding_output_invalid")
                return bytes(result[4:])
            return None

        with selectors.DefaultSelector() as selector:
            os.set_blocking(process.stdin.fileno(), False)
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE)
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                if context is not None:
                    context.remaining()
                if (process_tree_rss_bytes(process.pid) or 0) > budget:
                    raise EmbeddingError("embedding_worker_oom")
                if time.monotonic() >= exchange_limit:
                    raise EmbeddingError("embedding_timeout")
                # A retiring worker may already have a complete reply buffered.
                # Always consume readable output before writing or judging exit.
                events = selector.select(0.05)
                for key, _ in sorted(
                    events, key=lambda item: item[0].fileobj is process.stdin
                ):
                    if key.fileobj is process.stdout:
                        reply = receive()
                        if reply is not None:
                            return reply
                    else:
                        try:
                            offset += os.write(key.fd, framed[offset : offset + 65536])
                        except BrokenPipeError:
                            selector.unregister(process.stdin)
                            continue
                        if offset == len(framed):
                            selector.unregister(process.stdin)
                if process.poll() is not None:
                    while True:
                        before = len(result)
                        reply = receive()
                        if reply is not None:
                            return reply
                        if len(result) == before:
                            raise _worker_failure(process, has_response=bool(result))


def _worker_failure(
    process: subprocess.Popen, *, has_response: bool
) -> EmbeddingError | _WorkerRetired:
    """Only classified retirement without accepted response bytes permits replay."""
    try:
        status = process.wait(timeout=0.1)
    except subprocess.TimeoutExpired:
        status = None
    if status == RETIREMENT_EXIT and not has_response:
        return _WorkerRetired()
    return EmbeddingError(
        "embedding_worker_oom"
        if status in (-9, RESOURCE_EXIT)
        else "embedding_inference_failed"
    )


def configured_provider(sessions: SessionFactory) -> LocalEmbeddingProvider:
    if not settings.embedding_local_model_dir or not settings.embedding_model_key:
        raise EmbeddingError("embedding_not_configured")
    return LocalEmbeddingProvider(
        sessions,
        Path(settings.embedding_local_model_dir),
        settings.embedding_model_key,
        settings.embedding_onnx_threads,
    )
