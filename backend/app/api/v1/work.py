"""Administration of background work: lanes, definitions, executors, failures.

The Background work page reads one overview and acts through these routes:
change a lane's concurrency at runtime (reset with ``null``), cancel every
queued Job of a definition, and re-derive a derivative kind ("missing" fills
gaps, "all" re-derives every Artifact of the kind). There is deliberately no
pause: withdrawing work is a cancel, and a cancel withdraws its intent.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.core.security import require_auth, require_superuser
from app.db.models import DerivativeKind, LaneName, User
from app.db.session import get_session
from app.modules.derivatives.activity import preview_activity
from app.modules.work import service as work_service
from app.schemas.jobs import (
    CancelQueued,
    DerivativeRegenerate,
    LaneUpdate,
    PreviewActivity,
    WorkOverview,
)

router = APIRouter(prefix="/admin/work", tags=["jobs"])


@router.get("/preview-activity", response_model=PreviewActivity)
def get_preview_activity(
    _user: User = Depends(require_superuser), session: Session = Depends(get_session)
) -> PreviewActivity:
    return preview_activity(session)


@router.get("", response_model=WorkOverview, summary="Background work overview")
def overview(_user: User = Depends(require_superuser)) -> WorkOverview:
    return work_service.overview()


@router.put(
    "/lanes/{lane}",
    response_model=WorkOverview,
    dependencies=[Depends(require_auth)],
    summary="Set (or reset) a lane's concurrency at runtime",
)
def update_lane(
    lane: LaneName, body: LaneUpdate, user: User = Depends(require_superuser)
) -> WorkOverview:
    work_service.set_lane_concurrency(lane, body.concurrency, actor=user)
    return work_service.overview()


@router.post(
    "/cancel-queued",
    dependencies=[Depends(require_auth)],
    summary="Cancel every queued Job of one definition",
)
def cancel_queued(body: CancelQueued, user: User = Depends(require_superuser)) -> dict:
    try:
        cancelled = work_service.cancel_queued(body.definition, actor=user)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="job_definition_not_found") from exc
    return {"cancelled": cancelled}


@router.post(
    "/derivatives/{kind}/regenerate",
    status_code=202,
    dependencies=[Depends(require_auth)],
    summary="Re-derive one derivative kind across the library",
)
def regenerate(
    kind: DerivativeKind,
    body: DerivativeRegenerate,
    user: User = Depends(require_superuser),
) -> dict:
    work_service.regenerate_derivatives(kind, mode=body.mode, actor=user)
    return {"kind": kind, "mode": body.mode}
