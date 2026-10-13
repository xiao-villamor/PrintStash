"""Shared source-preparation budgets, acquired before native CPU/RAM permits."""

import shutil
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import local

from app.core.work_priority import current_priority
from app.runtime.native_admission import LocalResourcePool, NativePermit, Resources
from app.runtime.native_runtime import current_permit as current_native_permit

HOST_PERMIT_ENV = "PRINTSTASH_THUMBNAIL_HOST_PERMIT_FD"
HOST_PATH_ENV = "PRINTSTASH_THUMBNAIL_HOST_PERMIT_PATH"
PERMIT_ENV = "PRINTSTASH_PREPARATION_PERMIT_FD"
PERMIT_PATH_ENV = "PRINTSTASH_PREPARATION_PERMIT_PATH"


@dataclass(frozen=True)
class PreparationPools:
    prepared: LocalResourcePool
    io: LocalResourcePool
    host: LocalResourcePool
    codec: LocalResourcePool


def make_pools(directory: Path, io_directory: Path) -> PreparationPools:
    def reclaim(name: str) -> None:
        if len(name) != 32 or any(c not in "0123456789abcdef" for c in name):
            raise ValueError("invalid preparation workspace identity")
        workspace = directory / "sources" / name
        try:
            shutil.rmtree(workspace)
        except FileNotFoundError:
            if workspace.exists():
                raise

    return PreparationPools(
        LocalResourcePool(directory, reclaim=reclaim),
        LocalResourcePool(io_directory),
        LocalResourcePool(directory.parent / "thumbnail-host"),
        LocalResourcePool(directory.parent / "thumbnail-codec"),
    )


def workspace(permit: NativePermit) -> Path:
    if current_permit() is not permit:
        raise RuntimeError("preparation workspace requires its active permit")
    return _bound().prepared.directory / "sources" / permit.identity


_pools: PreparationPools | None = None
_local = local()


def bind_pools(pools: PreparationPools | None) -> PreparationPools | None:
    global _pools
    previous, _pools = _pools, pools
    return previous


def current_permit() -> NativePermit | None:
    return getattr(_local, "permit", None)


def _bound() -> PreparationPools:
    if _pools is None:
        raise RuntimeError(
            "source preparation pools are not bound by process bootstrap"
        )
    return _pools


@contextmanager
def reserve(
    request: Resources, capacity: Resources, *, checkpoint: Callable[[], None]
) -> Iterator[NativePermit]:
    """Reserve a whole input batch atomically; nested preparation would deadlock."""
    if current_native_permit() is not None or current_permit() is not None:
        raise RuntimeError("prepare all sources before acquiring native resources")
    with _bound().prepared.reserve(
        request, capacity, checkpoint=checkpoint, priority=current_priority()
    ) as permit:
        _local.permit = permit
        try:
            workspace(permit).mkdir(mode=0o700, parents=True, exist_ok=False)
            yield permit
        finally:
            del _local.permit


@contextmanager
def io_slot(slots: int, *, checkpoint: Callable[[], None]) -> Iterator[None]:
    """Only active copying consumes I/O slots; waiting native work retains bytes."""
    if current_permit() is None or current_native_permit() is not None:
        raise RuntimeError("source I/O requires preparation before native admission")
    with _bound().io.reserve(
        Resources(1, 1),
        Resources(slots, slots),
        checkpoint=checkpoint,
        priority=current_priority(),
    ):
        yield


def current_host_permit() -> NativePermit | None:
    return getattr(_local, "host_permit", None)


@contextmanager
def reserve_host(
    request: Resources, capacity: Resources, *, checkpoint: Callable[[], None]
) -> Iterator[NativePermit]:
    """Source custody precedes the host window, which precedes native work."""
    if current_permit() is None or current_native_permit() is not None:
        raise RuntimeError("thumbnail host memory requires prepared source custody")
    if current_host_permit() is not None:
        raise RuntimeError("thumbnail host memory already reserved")
    with _bound().host.reserve(
        request, capacity, checkpoint=checkpoint, priority=current_priority()
    ) as permit:
        _local.host_permit = permit
        try:
            yield permit
        finally:
            del _local.host_permit


@contextmanager
def reserve_codec(*, checkpoint: Callable[[], None]) -> Iterator[None]:
    """Finish one retained frame without queueing behind parser memory claims.

    This single process-shared CPU slot is additional to the parser slots. All
    codec RAM is already charged to the host permit, which survives encoding.
    No parser credit may be held: the bounded completion lane cannot admit source
    parsing or recursively wait for itself.
    """
    if current_host_permit() is None or current_native_permit() is not None:
        raise RuntimeError("codec requires host custody after parser release")
    if getattr(_local, "codec_active", False):
        raise RuntimeError("codec admission cannot be nested")
    amount = Resources(1, 1)
    with _bound().codec.reserve(
        amount, amount, checkpoint=checkpoint, priority=current_priority()
    ):
        _local.codec_active = True
        try:
            yield
        finally:
            del _local.codec_active
