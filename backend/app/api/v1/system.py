"""Administrator operations for the running PrintStash process."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from app.core.config import settings
from app.core.restart import request_restart
from app.core.security import require_superuser
from app.runtime.compute.contracts import ComputeStatus

router = APIRouter(
    prefix="/system",
    tags=["system"],
    dependencies=[Depends(require_superuser)],
)


@router.post(
    "/restart",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Restart PrintStash",
)
def restart(background_tasks: BackgroundTasks) -> dict[str, str]:
    """Return first, then gracefully stop for an external supervisor to relaunch."""
    if not settings.restart_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="restart_not_enabled",
        )
    background_tasks.add_task(request_restart)
    return {"status": "restart_requested"}


@router.get("/compute", response_model=ComputeStatus)
def compute_status() -> ComputeStatus:
    """Expose compute capability and residency without model paths or user inputs."""
    from app.runtime.compute.client import status

    return status()
