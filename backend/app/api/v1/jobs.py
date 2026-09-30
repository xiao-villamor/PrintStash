"""Background Jobs: list, inspect, cancel, retry; and the live events stream.

Every endpoint that accepts background work returns a ``job_id``; this is
where it is followed. A user sees and controls only the Jobs they own. An
administrator sees every user's Jobs, and system Jobs (derivatives, scans,
schedules) with ``include_system``.
"""

from __future__ import annotations

import asyncio

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from sqlmodel import Session
from starlette.websockets import WebSocketState

from app.core.security import require_auth, require_user
from app.db.models import CollectionRole, JobKind, Model, User
from app.db.session import get_session_factory
from app.modules.identity import rbac, ws_tickets
from app.modules.identity.auth import get_user_by_id
from app.modules.work import service as work_service
from app.modules.work.jobs import jobs
from app.schemas.jobs import JobStatus

router = APIRouter(prefix="/jobs", tags=["jobs"])
events_router = APIRouter(prefix="/events", tags=["jobs"])

_MAX_TRACKED = 50
_MAX_SUBSCRIPTIONS = 64
# Every subscribe request costs an authorization query, granted or not. A
# client following Models as a user browses stays far below this; one that
# exceeds it is probing, and is disconnected.
_MAX_SUBSCRIPTION_REQUESTS = 256


@router.get(
    "",
    response_model=list[JobStatus],
    summary="List active and recent Jobs",
)
def list_jobs(
    response: Response,
    terminal_limit: int = Query(20, ge=0, le=100),
    tracked_job_id: list[str] = Query(default=[]),
    kind: list[JobKind] = Query(default=[]),
    include_system: bool = False,
    current_user: User = Depends(require_user),
) -> list[JobStatus]:
    response.headers["Cache-Control"] = "no-store"
    if len(tracked_job_id) > _MAX_TRACKED:
        raise HTTPException(status_code=422, detail="too_many_tracked_job_ids")
    if include_system and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="admin_required")
    assert current_user.id is not None
    return jobs.list_for_user(
        current_user.id,
        is_superuser=current_user.is_superuser,
        terminal_limit=terminal_limit,
        tracked_job_ids=tuple(dict.fromkeys(tracked_job_id)),
        kinds=kind or None,
        include_system=include_system,
    )


def _visible(job_id: str, user: User) -> JobStatus:
    status = jobs.get(job_id)
    if status is None or not work_service.visible_to(status, user):
        raise HTTPException(status_code=404, detail="job_not_found")
    return status


@router.get("/{job_id}", response_model=JobStatus, summary="Get one Job")
def get_job(
    job_id: str,
    response: Response,
    current_user: User = Depends(require_user),
) -> JobStatus:
    response.headers["Cache-Control"] = "no-store"
    return _visible(job_id, current_user)


@router.post(
    "/{job_id}/cancel",
    response_model=JobStatus,
    dependencies=[Depends(require_auth)],
    summary="Cancel a queued or running Job",
    description=(
        "Cancelling withdraws the intent behind the Job (a staged upload is "
        "released, a derivative is marked cancelled) before stopping its "
        "execution, so the reconciler does not bring it back."
    ),
)
def cancel_job(job_id: str, current_user: User = Depends(require_user)) -> JobStatus:
    _visible(job_id, current_user)
    return work_service.cancel(job_id, actor=current_user)


@router.post(
    "/{job_id}/retry",
    response_model=JobStatus,
    dependencies=[Depends(require_auth)],
    summary="Retry a failed or cancelled Job",
)
def retry_job(job_id: str, current_user: User = Depends(require_user)) -> JobStatus:
    _visible(job_id, current_user)
    return work_service.retry(job_id, actor=current_user)


@router.post(
    "/{job_id}/discard-staging",
    status_code=204,
    dependencies=[Depends(require_auth)],
    summary="Discard retained input of a terminal ingest Job",
)
def discard_staging(
    job_id: str, current_user: User = Depends(require_user)
) -> Response:
    from app.modules.ingestion.staging_cleanup import discard

    discard(job_id, actor=current_user)
    return Response(status_code=204)


@events_router.post(
    "/ticket",
    dependencies=[Depends(require_auth)],
    summary="Issue a one-use ticket for the events WebSocket",
)
def create_events_ticket(current_user: User = Depends(require_user)) -> dict:
    assert current_user.id is not None
    return {
        "ticket": ws_tickets.issue(current_user.id, ws_tickets.EVENTS_SCOPE),
        "expires_in": ws_tickets.TTL_SECONDS,
    }


def _may_subscribe(session: Session, user: User, channel: str) -> bool:
    """Which channels a user may follow; every other channel is refused."""
    if channel == f"jobs:{user.id}":
        return True
    if channel == "work:admin":
        return user.is_superuser
    prefix, _, value = channel.partition(":")
    if prefix == "model" and value.isdigit():
        model = session.get(Model, int(value))
        if model is None or model.deleted_at is not None:
            return False
        if user.is_superuser:
            return True
        return rbac.role_allows(
            rbac.effective_collection_role(session, user, model.collection_id),
            CollectionRole.VIEW,
        )
    return False


@events_router.websocket("/ws")
async def events_ws(websocket: WebSocket) -> None:
    """Notices about Jobs and derivatives; refetch through the REST endpoints.

    The client is subscribed to its own Jobs (and, for an administrator, to
    every Job) on connect, and may send ``{"subscribe": "model:<id>"}`` or
    ``{"unsubscribe": ...}`` for the Models it is viewing; each accepted
    subscription is acknowledged with ``{"type": "subscribed", "channel": ...}``
    so the client can refetch what changed before it took effect. Messages are
    notices (``{"type": "job", "job_id": ...}``); ``{"type": "resync"}`` asks
    the client to refetch everything it shows.
    """
    ticket = websocket.query_params.get("ticket")
    user_id = ws_tickets.consume(ticket, ws_tickets.EVENTS_SCOPE) if ticket else None
    with get_session_factory().scoped_session() as session:
        user = get_user_by_id(session, user_id) if user_id is not None else None
        if user is None or not user.is_active:
            await websocket.close(code=1008)
            return
        session.expunge(user)
    bus = getattr(websocket.app.state, "event_bus", None)
    if bus is None:
        await websocket.close(code=1011)
        return
    await websocket.accept()
    sink = websocket.send_json
    channels = {f"jobs:{user.id}", "derivatives:policy"} | (
        {"work:admin"} if user.is_superuser else set()
    )
    for channel in channels:
        await bus.subscribe(channel, sink)
    await sink({"type": "resync"})
    requests = 0
    try:
        # A notice written from another task can find the client gone; the
        # socket is then disconnected, and reading from it would raise.
        while websocket.application_state == WebSocketState.CONNECTED:
            message = await websocket.receive_json()
            if not isinstance(message, dict):
                continue
            wanted = message.get("subscribe")
            unwanted = message.get("unsubscribe")
            if isinstance(wanted, str):
                requests += 1
                if requests > _MAX_SUBSCRIPTION_REQUESTS:
                    await websocket.close(code=1008)
                    return
            if isinstance(wanted, str) and len(channels) < _MAX_SUBSCRIPTIONS:
                allowed = await asyncio.to_thread(_subscription_allowed, user, wanted)
                if allowed:
                    channels.add(wanted)
                    await bus.subscribe(wanted, sink)
                    # Anything that changed before this point was announced to
                    # nobody on this socket; the client refetches on the ack.
                    await sink({"type": "subscribed", "channel": wanted})
            if isinstance(unwanted, str) and unwanted in channels:
                channels.discard(unwanted)
                await bus.unsubscribe(unwanted, sink)
    except (WebSocketDisconnect, ValueError):
        pass
    finally:
        for channel in channels:
            await bus.unsubscribe(channel, sink)


def _subscription_allowed(user: User, channel: str) -> bool:
    with get_session_factory().scoped_session() as session:
        fresh = session.get(User, user.id)
        return (
            fresh is not None
            and fresh.is_active
            and _may_subscribe(session, fresh, channel)
        )
