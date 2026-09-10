"""Opt-in similarity analysis and explicit review; never modify source bytes."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from app.core.security import require_auth, require_superuser, require_user
from app.db.models import FileType, User
from app.db.session import get_session
from app.modules.inference.search import SearchRequest
from app.modules.similarity import candidates, configuration, review, runs, service
from app.runtime.work_wakeup import WorkNotice

router = APIRouter(tags=["similarity"])
PathId = Annotated[int, Path(ge=1, le=2**63 - 1)]


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: runs.Scope = "library"
    ids: list[Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)]] = Field(
        default_factory=list, max_length=1000
    )


async def _wake(request: Request, run_id: int) -> None:
    wakeup = getattr(request.app.state, "similarity_wakeup", None)
    if wakeup is not None:
        await wakeup.notify(
            WorkNotice(job_id=str(run_id), kind="similarity", payload={})
        )


@router.get("/similarity/status")
def similarity_status(
    actor: User = Depends(require_user), session: Session = Depends(get_session)
):
    return service.status(session, actor)


@router.patch("/similarity/settings", dependencies=[Depends(require_auth)])
def similarity_settings(
    payload: dict[str, object],
    actor: User = Depends(require_superuser),
    session: Session = Depends(get_session),
):
    return configuration.update_settings(session, actor, payload)


@router.post("/similarity/selection-preview", dependencies=[Depends(require_auth)])
def preview_selection(
    payload: configuration.CandidateSelection,
    actor: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    return service.preview_selection(session, actor, payload)


@router.post("/similarity/runs", status_code=202, dependencies=[Depends(require_auth)])
async def start_run(
    payload: RunCreate,
    request: Request,
    actor: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    run = runs.start(session, actor, scope=payload.scope, ids=payload.ids)
    await _wake(request, run.id)
    return service.project_run(run)


@router.get("/similarity/runs")
def list_runs(
    before_id: int | None = Query(None, ge=1, le=2**63 - 1),
    limit: int = Query(50, ge=1, le=100),
    actor: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    return service.list_runs(session, actor, before_id=before_id, limit=limit)


@router.get("/similarity/runs/{run_id}")
def read_run(
    run_id: PathId,
    actor: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    return service.project_run(runs.require(session, actor, run_id))


@router.post("/similarity/runs/{run_id}/cancel", dependencies=[Depends(require_auth)])
async def cancel_run(
    run_id: PathId,
    request: Request,
    actor: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    run = runs.cancel(session, actor, run_id)
    await _wake(request, run_id)
    return service.project_run(run)


@router.get("/similarity/candidates")
def list_candidates(
    limit: int = Query(100, ge=1, le=1000),
    cursor: str | None = Query(None, max_length=128),
    evidence_class: str | None = Query(None, max_length=32),
    review_state: Literal["open", "confirmed", "rejected", "later"] | None = None,
    freshness: Literal["current", "stale"] | None = None,
    minimum_confidence: float | None = Query(None, ge=0, le=1),
    model_id: int | None = Query(None, ge=1, le=2**63 - 1),
    collection_id: int | None = Query(None, ge=1, le=2**63 - 1),
    file_type: FileType | None = None,
    source: Literal["vault", "external"] | None = None,
    known_good: bool | None = None,
    actor: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    return asdict(
        candidates.list_visible(
            session,
            actor,
            limit=limit,
            cursor=cursor,
            evidence_class=evidence_class,
            review_state=review_state,
            freshness=freshness,
            model_id=model_id,
            minimum_confidence=minimum_confidence,
            collection_id=collection_id,
            file_type=file_type,
            source=source,
            known_good=known_good,
        )
    )


@router.get("/similarity/candidates/{candidate_id}")
def read_candidate(
    candidate_id: PathId,
    actor: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    return candidates.project(
        session, candidates.require(session, actor, candidate_id), detail=True
    )


@router.post(
    "/similarity/candidates/{candidate_id}/decision",
    dependencies=[Depends(require_auth)],
)
def decide_candidate(
    candidate_id: PathId,
    payload: review.DecisionRequest,
    actor: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    decision = review.decide(session, actor, candidate_id, payload)
    return {
        "decision_id": decision.id,
        "resolution_kind": decision.resolution_kind,
        "target_id": decision.target_id,
        "candidate": candidates.project(
            session, candidates.require(session, actor, candidate_id), detail=True
        ),
    }


@router.get("/models/{model_id}/similar")
def model_similar(
    model_id: PathId,
    limit: int = Query(100, ge=1, le=1000),
    cursor: str | None = Query(None, max_length=128),
    actor: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    runs.normalize_scope(session, actor, "models", [model_id])
    return asdict(
        candidates.list_visible(
            session, actor, model_id=model_id, limit=limit, cursor=cursor
        )
    )


@router.post("/models/{model_id}/similar/query", dependencies=[Depends(require_auth)])
async def query_model(
    model_id: PathId,
    request: Request,
    actor: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    result = service.query_model(session, actor, model_id)
    await _wake(request, result["run"]["id"])
    return result


@router.post("/similarity/search", dependencies=[Depends(require_auth)])
def search_similar(
    payload: "SearchRequest",
    actor: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    from app.core.errors import ErrorKind, OperationError
    from app.db.session import get_session_factory
    from app.modules.inference.search import search
    from app.runtime.maintenance import begin_mutating_operation, end_mutating_operation

    if not begin_mutating_operation():
        raise OperationError("restore_maintenance", kind=ErrorKind.UNAVAILABLE)
    try:
        return search(session, get_session_factory(), actor, payload)
    finally:
        end_mutating_operation()
