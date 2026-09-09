"""Construction and explicit binding of the process storage adapter."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from app.core.config import settings
from app.core.logging import get_logger

from .contracts import StorageBackend
from .local import LocalStorageBackend
from .s3 import S3StorageBackend

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level backend singleton
# ---------------------------------------------------------------------------

_backend: StorageBackend | None = None
_read_backend: ContextVar[StorageBackend | None] = ContextVar(
    "vault_backend", default=None
)


def get_backend() -> StorageBackend:
    pinned = _read_backend.get()
    return pinned if pinned is not None else get_bound_backend()


@contextmanager
def use_read_backend(backend: StorageBackend):
    """Pin adapter resolution to the read generation selected by admission."""
    token = _read_backend.set(backend)
    try:
        yield
    finally:
        _read_backend.reset(token)


def get_bound_backend() -> StorageBackend:
    """Return the backend selected by the application's composition root.

    Storage access is intentionally not constructed on demand here: doing so
    lets the first arbitrary caller choose process-wide infrastructure from
    mutable runtime settings.  Startup (or a test fixture) must construct,
    validate, and bind an adapter before any consumer accesses storage.
    """
    if _backend is None:
        raise RuntimeError(
            "storage_backend_not_bound: bind a configured backend before use"
        )
    return _backend


def create_backend(backend_name: str) -> StorageBackend:
    """Construct the adapter selected by *backend_name* without binding it.

    The caller owns setup validation and the lifetime of the process-wide
    binding.  Unknown values retain the historical local-storage fallback.
    """
    if backend_name == "s3":
        logger.info("constructing S3 storage backend (bucket=%s)", settings.s3_bucket)
        return S3StorageBackend()
    logger.info("constructing local storage backend")
    return LocalStorageBackend()


def bind_backend(backend: StorageBackend) -> StorageBackend:
    """Bind one already-configured backend for compatibility callers."""
    global _backend
    _backend = backend
    return backend


def init_backend() -> StorageBackend:
    """Legacy composition helper for tests and compatibility entrypoints.

    New application startup constructs the adapter explicitly, validates it,
    then calls :func:`bind_backend`.
    """
    backend = create_backend(settings.storage_backend)
    backend.ensure_setup()
    return bind_backend(backend)
