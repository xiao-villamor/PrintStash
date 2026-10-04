"""Process-shared model residency outside the geometric memory allowance."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from printstash_core.inference import EmbeddingError

from app.core.config import settings
from app.modules.media.native_process import memory_limit_bytes
from app.runtime.native_admission import LocalResourcePool, NativePermit, Resources
from app.runtime.native_runtime import PERMIT_ENV, PERMIT_PATH_ENV, current_permit

_pool: LocalResourcePool | None = None


def bind_pool(pool: LocalResourcePool | None) -> LocalResourcePool | None:
    """Bind before serving work; return the previous pool for teardown."""
    global _pool
    previous, _pool = _pool, pool
    return previous


def capacity() -> Resources:
    """Partition detected physical memory while retaining host headroom."""
    physical = memory_limit_bytes()
    if physical is None or physical <= 0:
        raise EmbeddingError("embedding_memory_capacity_unavailable")
    models = settings.embedding_memory_budget_fraction
    geometry = settings.mesh_memory_budget_fraction or 0.5
    if (
        not math.isfinite(models)
        or not 0 < models < 1
        or not math.isfinite(geometry)
        or not 0 < geometry < 1
        or geometry + models >= 1
    ):
        raise EmbeddingError("embedding_memory_budget_invalid")
    amount = int(physical * models)
    if amount <= 0:
        raise EmbeddingError("embedding_memory_budget_invalid")
    return Resources(settings.embedding_resident_workers, amount)


def _configured_worker_memory_bytes(allowance: Resources) -> int:
    return min(settings.embedding_worker_memory_mb * 1024**2, allowance.bytes)


def worker_memory_budget_bytes(*, permit: NativePermit | None = None) -> int:
    """Return an immutable claimed ceiling or the parent's configured policy.

    Child launch always uses its claimed bytes; bootstrap verifies that exact
    hard ceiling. Parent polling may use policy under the installation's fixed
    quota profile, which changes only after all processes have stopped.
    """
    if permit is not None:
        return permit.resources.bytes
    return _configured_worker_memory_bytes(capacity())


@contextmanager
def reserve(*, checkpoint: Callable[[], None]) -> Iterator[NativePermit]:
    """Hold a direct lease until spawn transfers its descriptor to the child.

    The parent does not bind an execution context: cached children can outlive
    the launching thread. The bootstrap and its guardian retain inherited credit
    until the entire native tree has terminated.
    """
    pool = _pool
    if pool is None:
        raise RuntimeError("inference resource pool is not bound by process bootstrap")
    allowance = capacity()
    claim = Resources(1, _configured_worker_memory_bytes(allowance))
    with pool.reserve(claim, allowance, checkpoint=checkpoint) as permit:
        yield permit


def _no_checkpoint() -> None:
    pass


def has_pressure(*, checkpoint: Callable[[], None] = _no_checkpoint) -> bool:
    """A child discovers pressure through its inherited reservation namespace."""
    permit = current_permit()
    if permit is None:
        raise RuntimeError("model pressure requires an inherited residency permit")
    return LocalResourcePool(permit.path.parent).has_waiters(checkpoint=checkpoint)


@dataclass(frozen=True)
class ResidencyLaunchResources:
    """Only model credit crosses a warm-worker process boundary."""

    descriptors: tuple[int, ...]
    environment: dict[str, str]


def launch_resources(permit: NativePermit) -> ResidencyLaunchResources:
    """Exclude prepared Artifact credits from the cached model lifetime."""
    return ResidencyLaunchResources(
        (permit.fileno,),
        {PERMIT_ENV: str(permit.fileno), PERMIT_PATH_ENV: str(permit.path)},
    )
