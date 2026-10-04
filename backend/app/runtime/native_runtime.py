"""Explicit process binding and nested lifetime for native resource admission.

The application bootstrap binds a local pool. Native children borrow a passed
file descriptor; importing this module never discovers or creates infrastructure.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from app.runtime.native_admission import (
    AdmissionTooLarge,
    LocalResourcePool,
    NativePermit,
    Resources,
)

PERMIT_ENV = "PRINTSTASH_NATIVE_PERMIT_FD"
PERMIT_PATH_ENV = "PRINTSTASH_NATIVE_PERMIT_PATH"
_pool: LocalResourcePool | None = None
_local = threading.local()


def bind_pool(pool: LocalResourcePool | None) -> LocalResourcePool | None:
    """Compose before accepting work; return the previous binding for teardown."""
    global _pool
    previous, _pool = _pool, pool
    return previous


def current_permit() -> NativePermit | None:
    return getattr(_local, "permit", None)


@contextmanager
def inherit(descriptor: int, path: Path) -> Iterator[NativePermit]:
    """The bootstrap borrows before importing native application code."""
    if current_permit() is not None:
        raise RuntimeError("native worker already owns a resource permit")
    with NativePermit.inherit(descriptor, path) as permit:
        _local.permit = permit
        try:
            yield permit
        finally:
            del _local.permit


@contextmanager
def admit(
    request: Resources,
    capacity: Resources,
    *,
    checkpoint: Callable[[], None],
) -> Iterator[NativePermit]:
    """Nested synchronous work consumes its existing permit, never a new share."""
    active = current_permit()
    if active is not None:
        if not request.fits(active.resources):
            raise AdmissionTooLarge("nested work exceeds its native resource permit")
        checkpoint()
        yield active
        return
    pool = _pool
    if pool is None:
        raise RuntimeError("native resource pool is not bound by process bootstrap")
    with pool.reserve(request, capacity, checkpoint=checkpoint) as permit:
        _local.permit = permit
        try:
            yield permit
        finally:
            del _local.permit
