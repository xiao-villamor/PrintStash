"""Opt-in provider that contains ONNX failures under the existing render budget."""

from __future__ import annotations

import importlib.util
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path

from printstash_core.inference import EmbeddingError, EmbeddingInput, EmbeddingSpace
from printstash_core.inference.vectors import normalize
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlmodel import Session

from app import __file__ as application_file
from app.core.config import settings
from app.db.session import SessionFactory
from app.modules.inference.manifest import LocalModelManifest, read_manifest
from app.modules.media import compute_slots
from app.modules.storage.capacity import CapacityManager, CapacityResource


class WorkerResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config_hash: str
    vectors: list[list[float]] = Field(max_length=8)


class WorkerError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^embedding_[a-z_]{1,64}$")


class LocalEmbeddingProvider:
    def __init__(
        self, sessions: SessionFactory, directory: Path, model_key: str, threads: int
    ):
        if not 1 <= threads <= 4:
            raise EmbeddingError("embedding_thread_budget_invalid")
        self.sessions = sessions
        self.directory = directory.resolve()
        self.model_key = model_key
        self.threads = threads
        self.manifest = read_manifest(self.directory, model_key)
        self.space = self.manifest.space()

    def validate(self) -> LocalModelManifest:
        self._execute(())
        return self.manifest

    def embed(
        self, inputs: tuple[EmbeddingInput, ...], space: EmbeddingSpace
    ) -> tuple[tuple[float, ...], ...]:
        if space != self.space:
            raise EmbeddingError("embedding_space_mismatch")
        if not 1 <= len(inputs) <= 8:
            raise EmbeddingError("embedding_batch_budget")
        if space.modality == "image" and any(
            item.modality == "text" for item in inputs
        ):
            raise EmbeddingError("embedding_text_unavailable")
        return self._execute(inputs)

    @staticmethod
    def _release_slot(session: Session, slot_id: int | None, token: str) -> None:
        compute_slots.release(session, slot_id, token)
        session.commit()

    def _execute(
        self, inputs: tuple[EmbeddingInput, ...]
    ) -> tuple[tuple[float, ...], ...]:
        if importlib.util.find_spec("onnxruntime") is None:
            raise EmbeddingError("embedding_runtime_unavailable")
        with ExitStack() as cleanup:
            session = cleanup.enter_context(self.sessions.scoped_session())
            token = "embedding:" + secrets.token_hex(20)
            slot = compute_slots.acquire(session, token)
            if slot is None:
                raise EmbeddingError("embedding_compute_busy")
            cleanup.callback(self._release_slot, session, slot.id, token)
            temporary = Path(
                cleanup.enter_context(
                    tempfile.TemporaryDirectory(prefix="printstash-embedding-")
                )
            )
            reservation = CapacityManager(self.sessions).reserve(
                token,
                [
                    CapacityResource.for_path(
                        temporary, 32 * 1024**2, role="local embedding"
                    )
                ],
            )
            cleanup.callback(reservation.release)
            request_inputs = []
            for index, item in enumerate(inputs):
                request_inputs.append(
                    {
                        "modality": item.modality,
                        "text": item.text,
                        "width": item.width,
                        "height": item.height,
                    }
                )
                if item.rgb is not None:
                    (temporary / f"{index}.rgb").write_bytes(item.rgb)
            (temporary / "request.json").write_text(
                json.dumps(
                    {"config_hash": self.space.config_hash, "inputs": request_inputs}
                )
            )
            env = os.environ.copy()
            env.update(
                OMP_NUM_THREADS=str(self.threads),
                OPENBLAS_NUM_THREADS=str(self.threads),
                TOKENIZERS_PARALLELISM="false",
                HF_HUB_OFFLINE="1",
                TRANSFORMERS_OFFLINE="1",
            )
            process = subprocess.Popen(  # nosec B603 - fixed Python module, no shell
                [
                    sys.executable,
                    "-m",
                    "app.modules.inference.worker",
                    str(temporary),
                    str(self.directory),
                    self.model_key,
                    str(self.threads),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                cwd=Path(application_file).resolve().parent.parent,
            )
            failure = None
            deadline = time.monotonic() + min(settings.mesh_step_timeout_seconds, 90)
            budget = compute_slots.native_memory_budget_bytes()
            try:
                while process.poll() is None:
                    rss = compute_slots.native_process_rss_bytes(process.pid)
                    if rss is not None and rss > budget:
                        failure = "embedding_worker_oom"
                        break
                    if time.monotonic() >= deadline:
                        failure = "embedding_timeout"
                        break
                    time.sleep(0.05)
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
            if failure is not None:
                raise EmbeddingError(failure)
            if process.returncode != 0:
                error_path = temporary / "error.json"
                if error_path.is_file() and error_path.stat().st_size <= 1024:
                    try:
                        error = WorkerError.model_validate_json(error_path.read_bytes())
                    except ValidationError as exc:
                        raise EmbeddingError("embedding_inference_failed") from exc
                    raise EmbeddingError(error.code)
                raise EmbeddingError(
                    "embedding_worker_oom"
                    if process.returncode == -9
                    else "embedding_inference_failed"
                )
            result_path = temporary / "result.json"
            try:
                if result_path.stat().st_size > 1024**2:
                    raise EmbeddingError("embedding_output_budget")
                result = WorkerResult.model_validate_json(result_path.read_bytes())
            except (OSError, ValidationError) as exc:
                raise EmbeddingError("embedding_output_invalid") from exc
            if result.config_hash != self.space.config_hash or len(
                result.vectors
            ) != len(inputs):
                raise EmbeddingError("embedding_output_mismatch")
            for vector in result.vectors:
                normalize(vector, self.space.dimension)
            return tuple(tuple(vector) for vector in result.vectors)


def configured_provider(sessions: SessionFactory) -> LocalEmbeddingProvider:
    if not settings.embedding_local_model_dir or not settings.embedding_model_key:
        raise EmbeddingError("embedding_not_configured")
    return LocalEmbeddingProvider(
        sessions,
        Path(settings.embedding_local_model_dir),
        settings.embedding_model_key,
        settings.embedding_onnx_threads,
    )
