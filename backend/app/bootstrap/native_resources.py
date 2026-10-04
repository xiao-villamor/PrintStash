"""Compose the host-local native resource pool before accepting work."""

from pathlib import Path

from app.runtime.inference_resources import bind_pool as bind_inference_pool
from app.runtime.native_admission import LocalResourcePool
from app.runtime.native_runtime import bind_pool
from app.runtime.preparation_runtime import bind_pools, make_pools


def configure(root: Path) -> None:
    """Processes sharing a local data root share descriptor-owned ledgers."""
    # Kernel locks cannot survive a reboot. Closed records are reclaimed before
    # admitting new work, so the ledger does not need Linux boot discovery.
    bind_pool(LocalResourcePool(root / "runtime" / "native"))
    bind_inference_pool(LocalResourcePool(root / "runtime" / "inference-models"))
    # Disk usage survives a reboot. Keep its descriptor-based recovery domain
    # stable so the first new reservation reclaims old workspaces before reuse.
    bind_pools(
        make_pools(
            root / "runtime" / "prepared",
            root / "runtime" / "source-io",
        )
    )
