from __future__ import annotations

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Response,
    status,
)
from sqlmodel import Session

from app.core.security import require_auth, require_user
from app.db.models import InboxItemState, User
from app.db.session import SessionFactory, get_session, get_session_factory
from app.schemas.inbox import (
    InboxBatchRequest,
    InboxImportRequest,
    InboxItemCreate,
    InboxItemRead,
    InboxItemUpdate,
)
from app.services import inbox

router = APIRouter(prefix="/inbox", tags=["pending imports"])


@router.post(
    "",
    response_model=InboxItemRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_auth)],
)
async def capture(
    payload: InboxItemCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> InboxItemRead:
    try:
        row = inbox.create(session, current_user, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except inbox.importer.ImportError_ as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    background_tasks.add_task(inbox.resolve, row.id)
    return inbox.read(row)


@router.get("", response_model=list[InboxItemRead])
def list_items(
    include_completed: bool = Query(True),
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> list[InboxItemRead]:
    return inbox.list_visible(session, current_user, include_completed=include_completed)


@router.post("/batch", response_model=list[InboxItemRead], dependencies=[Depends(require_auth)])
def batch_items(
    payload: InboxBatchRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
    session_factory: SessionFactory = Depends(get_session_factory),
) -> list[InboxItemRead]:
    output: list[InboxItemRead] = []
    for item_id in dict.fromkeys(payload.item_ids):
        row = inbox.require_visible(session, current_user, item_id)
        if payload.action == "set_collection":
            row = inbox.update(
                session,
                current_user,
                row,
                InboxItemUpdate(collection_id=payload.collection_id),
            )
        elif payload.action == "add_tags":
            tags = list(dict.fromkeys([*inbox.requested_tags(row.requested_tags_json), *payload.tags]))
            row = inbox.update(session, current_user, row, InboxItemUpdate(tags=tags))
        elif payload.action == "retry":
            row = inbox.retry(session, row)
            if row.state == InboxItemState.CAPTURED:
                background_tasks.add_task(inbox.resolve, row.id)
        elif payload.action == "import":
            if row.state != InboxItemState.REVIEW:
                continue
            background_tasks.add_task(inbox.run_import, row.id, [], session_factory)
        else:
            inbox.dismiss(session, row)
        session.refresh(row)
        output.append(inbox.read(row))
    return output


@router.get("/{item_id}", response_model=InboxItemRead)
def get_item(
    item_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> InboxItemRead:
    return inbox.read(inbox.require_visible(session, current_user, item_id))


@router.patch("/{item_id}", response_model=InboxItemRead, dependencies=[Depends(require_auth)])
def update_item(
    item_id: int,
    payload: InboxItemUpdate,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> InboxItemRead:
    row = inbox.require_visible(session, current_user, item_id)
    return inbox.read(inbox.update(session, current_user, row, payload))


@router.post("/{item_id}/resolve", response_model=InboxItemRead, dependencies=[Depends(require_auth)])
def resolve_item(
    item_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> InboxItemRead:
    row = inbox.require_visible(session, current_user, item_id)
    if row.state not in {InboxItemState.CAPTURED, InboxItemState.FAILED}:
        raise HTTPException(status_code=409, detail="pending_import_not_resolvable")
    background_tasks.add_task(inbox.resolve, row.id)
    return inbox.read(row)


@router.post("/{item_id}/import", response_model=InboxItemRead, dependencies=[Depends(require_auth)])
def import_item(
    item_id: int,
    payload: InboxImportRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
    session_factory: SessionFactory = Depends(get_session_factory),
) -> InboxItemRead:
    row = inbox.require_visible(session, current_user, item_id)
    if row.state != InboxItemState.REVIEW:
        raise HTTPException(status_code=409, detail="pending_import_not_ready")
    background_tasks.add_task(inbox.run_import, row.id, payload.selected_ids, session_factory)
    return inbox.read(row)


@router.post("/{item_id}/retry", response_model=InboxItemRead, dependencies=[Depends(require_auth)])
def retry_item(
    item_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> InboxItemRead:
    row = inbox.retry(session, inbox.require_visible(session, current_user, item_id))
    if row.state == InboxItemState.CAPTURED:
        background_tasks.add_task(inbox.resolve, row.id)
    return inbox.read(row)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_auth)])
def dismiss_item(
    item_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> Response:
    inbox.dismiss(session, inbox.require_visible(session, current_user, item_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
