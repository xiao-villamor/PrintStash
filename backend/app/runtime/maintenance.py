"""Process-local maintenance exclusion and draining of mutating operations."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Callable, Iterator, ParamSpec, TypeVar

from app.core.errors import ErrorKind, OperationError
from app.core.logging import get_logger

logger = get_logger(__name__)


# ponytail: process-wide gate, single-process/single-worker only. A
# multi-worker deployment needs a DB-backed lock instead of this in-memory
# Event — not built here.
_restore_gate = threading.Event()

_RESTORE_DRAIN_TIMEOUT_S = 30.0

backup_operation_lock = threading.RLock()

_mutation_condition = threading.Condition()

_active_mutations = 0
_mutation_observer: Callable[[], None] | None = None


def observe_mutations(observer: Callable[[], None] | None) -> None:
    """Composition hook for a durable activation owner's first-write marker."""
    global _mutation_observer
    _mutation_observer = observer


_P = ParamSpec("_P")

_R = TypeVar("_R")


class RestoreConflictError(Exception):
    """Raised when a restore is refused because ingestion work is in flight."""


def restore_in_progress() -> bool:
    return _restore_gate.is_set()


def begin_mutating_operation() -> bool:
    """Register a write-capable operation unless restore maintenance is active."""
    global _active_mutations
    with _mutation_condition:
        if _restore_gate.is_set():
            return False
        _active_mutations += 1
    try:
        if _mutation_observer is not None:
            _mutation_observer()
    except Exception:
        end_mutating_operation()
        raise
    return True


def end_mutating_operation() -> None:
    global _active_mutations
    with _mutation_condition:
        if _active_mutations <= 0:
            raise RuntimeError("unbalanced_mutating_operation")
        _active_mutations -= 1
        if _active_mutations == 0:
            _mutation_condition.notify_all()


def begin_restore_maintenance() -> None:
    """Block new mutations and wait for already-admitted ones to drain."""
    deadline = time.monotonic() + _RESTORE_DRAIN_TIMEOUT_S
    with _mutation_condition:
        _restore_gate.set()
        while _active_mutations:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _restore_gate.clear()
                _mutation_condition.notify_all()
                raise RestoreConflictError(
                    f"{_active_mutations} write operation(s) still active; retry later"
                )
            _mutation_condition.wait(timeout=remaining)


def end_restore_maintenance() -> None:
    with _mutation_condition:
        _restore_gate.clear()
        _mutation_condition.notify_all()


def exclusive_backup_operation(func: Callable[_P, _R]) -> Callable[_P, _R]:
    """Prevent overlapping backup/restore operations in this process."""

    @wraps(func)
    def serialized(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        with backup_operation_lock:
            return func(*args, **kwargs)

    return serialized


def hold_restore_maintenance() -> None:
    """Keep mutations gated while durable recovery evidence remains unresolved."""
    with _mutation_condition:
        _restore_gate.set()


# Snapshot/export leases exclude destructive storage operations without stopping
# ordinary create-only ingestion. Acquire before the database snapshot begins.
_retention_condition = threading.Condition(threading.RLock())
_storage_retentions = 0
_active_destructive = 0
_activating_configuration: ContextVar[bool] = ContextVar(
    "vault_configuration_activation", default=False
)
_database_connections_fenced = threading.Event()


@contextmanager
def retain_storage_objects() -> Iterator[None]:
    """Keep all captured object generations alive until an archive/copy finishes."""
    global _storage_retentions
    with _retention_condition:
        if _active_destructive:
            raise OperationError("storage_cleanup_in_progress", kind=ErrorKind.BUSY)
        _storage_retentions += 1
    try:
        yield
    finally:
        with _retention_condition:
            _storage_retentions -= 1
            _retention_condition.notify_all()


_retained_destination: ContextVar[object | None] = ContextVar(
    "retained_migration_destination", default=None
)


@contextmanager
def allow_retained_destination_destruction(
    *, source: object, destination: object
) -> Iterator[None]:
    """Permit exact-receipt migration cleanup on this isolated candidate only."""
    if source is destination:
        raise ValueError("migration_destination_must_be_isolated")
    token = _retained_destination.set(destination)
    try:
        yield
    finally:
        _retained_destination.reset(token)


def begin_destructive_operation(*, _backend: object | None = None) -> bool:
    """Atomically admit a delete, move or replacement when no capture is pinned."""
    global _active_destructive
    with _retention_condition:
        if _storage_retentions and (
            _backend is None or _retained_destination.get() is not _backend
        ):
            return False
        _active_destructive += 1
        return True


def end_destructive_operation() -> None:
    global _active_destructive
    with _retention_condition:
        if _active_destructive <= 0:
            raise RuntimeError("unbalanced_destructive_operation")
        _active_destructive -= 1
        _retention_condition.notify_all()


@contextmanager
def destructive_operation(*, _backend: object | None = None) -> Iterator[None]:
    if not begin_destructive_operation(_backend=_backend):
        raise OperationError("storage_snapshot_retained", kind=ErrorKind.BUSY)
    try:
        yield
    finally:
        end_destructive_operation()


def guarded_storage_destruction(func: Callable[_P, _R]) -> Callable[_P, _R]:
    """Cover the whole storage mutation, including its identity check."""

    @wraps(func)
    def guarded(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        with destructive_operation(_backend=args[0] if args else None):
            return func(*args, **kwargs)

    return guarded


def guarded_destructive_operation(func: Callable[_P, _R]) -> Callable[_P, _R]:
    """Exclude the logical transaction too, before claims or rows are changed."""

    @wraps(func)
    def guarded(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        with destructive_operation():
            return func(*args, **kwargs)

    return guarded


@contextmanager
def activating_storage_configuration() -> Iterator[None]:
    """Internal atomic-activation scope; never exposed by configuration routes."""
    token = _activating_configuration.set(True)
    try:
        yield
    finally:
        _activating_configuration.reset(token)


def guarded_storage_configuration(func: Callable[_P, _R]) -> Callable[_P, _R]:
    fields = {
        "provider",
        "storage_backend",
        "data_dir",
        "thumb_dir",
        "s3_bucket",
        "s3_endpoint_url",
        "s3_region",
        "s3_access_key",
        "s3_secret_key",
    }

    @wraps(func)
    def guarded(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        if _activating_configuration.get() or not any(
            kwargs.get(field) is not None for field in fields
        ):
            return func(*args, **kwargs)
        with destructive_operation():
            return func(*args, **kwargs)

    return guarded


def fence_database_connections() -> None:
    """Reject fresh application sessions while database names are activated."""
    _database_connections_fenced.set()


def release_database_connections() -> None:
    _database_connections_fenced.clear()


def require_database_connection_admission() -> None:
    if _database_connections_fenced.is_set():
        raise OperationError("database_activation_in_progress", kind=ErrorKind.BUSY)
