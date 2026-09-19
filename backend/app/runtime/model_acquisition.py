"""A bounded local task uses the common durable job-status contract."""

import importlib.util
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import Event, RLock

from printstash_core.inference import EmbeddingError
from sqlmodel import Session

from app.core.errors import ErrorKind, OperationError
from app.db.models import User
from app.db.session import SessionFactory, get_session_factory
from app.modules.inference.model_acquisition import Acquisition
from app.modules.inference.model_registry import require
from app.modules.search.configuration import settings
from app.runtime.jobs import registry

_lock = RLock()
_active: dict[str, Event] = {}
_executor: ThreadPoolExecutor | None = None


def start(session: Session, actor: User, key: str) -> str:
    global _executor
    flags = settings(session)
    if not actor.is_superuser or not actor.is_active:
        raise OperationError("admin_required", kind=ErrorKind.FORBIDDEN)
    if (
        not flags.enabled
        or not flags.local_models_enabled
        or not flags.download_enabled
    ):
        raise OperationError("embedding_download_disabled", kind=ErrorKind.CONFLICT)
    entry = require(key)
    if not all(
        importlib.util.find_spec(name) is not None
        for name in ("onnxruntime", "onnx", "tokenizers")
    ):
        raise OperationError("embedding_runtime_unavailable", kind=ErrorKind.CONFLICT)
    with _lock:
        if _active:
            raise OperationError("embedding_download_busy", kind=ErrorKind.CONFLICT)
        job_id = registry.create(actor.id, kind="model_download", session=session)
        session.commit()
        event = Event()
        _active[job_id] = event
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="model-download"
            )
        _executor.submit(
            copy_context().run,
            _run,
            get_session_factory(),
            actor.id,
            job_id,
            entry.id,
            event,
        )
        return job_id


def _run(
    sessions: SessionFactory, actor_id: int, job_id: str, identity: str, event: Event
):
    checked, allowed, uploaded, reported = 0.0, False, 0, 0.0
    entry = require(identity)

    def enabled():
        nonlocal checked, allowed
        if time.monotonic() - checked > 0.25:
            checked = time.monotonic()
            with sessions.scoped_session() as session:
                actor = session.get(User, actor_id)
                value = settings(session)
                allowed = bool(
                    actor
                    and actor.is_active
                    and actor.is_superuser
                    and value.enabled
                    and value.local_models_enabled
                    and value.download_enabled
                )
        return allowed

    def progress(size: int):
        nonlocal uploaded, reported
        uploaded += size
        if time.monotonic() - reported > 0.5:
            reported = time.monotonic()
            registry.update(
                job_id,
                processed=uploaded,
                total=entry.size,
                progress=min(99, 100 * uploaded / entry.size),
            )

    try:
        registry.update(
            job_id,
            state="running",
            label="Downloading model",
            total=entry.size,
            result={"model_id": identity},
        )
        Acquisition(sessions).install(
            identity, enabled=enabled, cancelled=event.is_set, progress=progress
        )
        registry.update(
            job_id,
            state="completed",
            progress=100,
            processed=entry.size,
            result={"model_id": identity},
        )
    except Exception as exc:
        code = (
            exc.code if isinstance(exc, EmbeddingError) else "embedding_download_failed"
        )
        registry.update(
            job_id,
            state="failed",
            error=code,
            result={"model_id": identity, "error_code": code},
        )
    finally:
        with _lock:
            _active.pop(job_id, None)


def cancel(job_id: str):
    with _lock:
        event = _active.get(job_id)
        if event is None:
            raise OperationError(
                "embedding_download_not_running", kind=ErrorKind.NOT_FOUND
            )
        event.set()


def close():
    global _executor
    with _lock:
        for event in _active.values():
            event.set()
        executor, _executor = _executor, None
    if executor is not None:
        executor.shutdown(wait=True, cancel_futures=False)
