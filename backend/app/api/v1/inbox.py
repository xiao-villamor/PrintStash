from __future__ import annotations

from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.concurrency import run_in_threadpool
from sqlmodel import Session

from app.core.browser_device_auth import require_user_or_browser_import_user
from app.core.security import require_auth, require_user
from app.db.models import InboxItemState, User
from app.db.session import SessionFactory, get_session, get_session_factory
from app.schemas.inbox import (
    CaptureUploadSlotRead,
    CaptureUploadSlotsCreate,
    CaptureUploadSlotsRead,
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
)
async def capture(
    payload: InboxItemCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_user_or_browser_import_user),
    session: Session = Depends(get_session),
) -> InboxItemRead:
    try:
        row = inbox.create(session, current_user, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except inbox.importer.ImportError_ as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    assert row.id is not None
    if payload.capture_source is None:
        background_tasks.add_task(inbox.resolve, row.id)
    return inbox.read(row, session)


@router.post(
    "/capture-upload-slots",
    response_model=CaptureUploadSlotsRead,
    status_code=status.HTTP_201_CREATED,
)
def create_capture_upload_slots(
    payload: CaptureUploadSlotsCreate,
    current_user: User = Depends(require_user_or_browser_import_user),
    session: Session = Depends(get_session),
) -> CaptureUploadSlotsRead:
    try:
        row, slots = inbox.create_capture_upload_slots(session, current_user, payload)
    except inbox.staging_leases.StagingCapacityExceeded as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc
    except (ValueError, inbox.importer.ImportError_) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CaptureUploadSlotsRead(
        item=inbox.read(row, session), slots=[inbox.slot_read(slot) for slot in slots]
    )


@router.put("/capture-upload-slots/{slot_id}", response_model=CaptureUploadSlotRead)
async def put_capture_upload_slot(
    slot_id: str,
    request: Request,
    current_user: User = Depends(require_user_or_browser_import_user),
    session: Session = Depends(get_session),
) -> CaptureUploadSlotRead:
    slot = inbox.require_capture_slot(session, current_user, slot_id)
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="invalid_content_length"
            ) from exc
        if declared_length > inbox.settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="upload_too_large")
    staged_path: Path | None = None
    try:
        # Commit the lease-owned placeholder inode before consuming request
        # bytes. A process kill now leaves a deterministic, identity-bound
        # partial for startup reconciliation rather than an anonymous temp.
        staged_path = await run_in_threadpool(
            inbox.staging_leases.prepare_capture_slot_staging,
            session,
            slot_id=slot.id,
        )
        received = 0
        with inbox.staging_leases.open_capture_slot_staging(
            session, slot_id=slot.id
        ) as target:
            async for chunk in request.stream():
                received += len(chunk)
                if received > inbox.settings.max_upload_bytes:
                    raise HTTPException(status_code=413, detail="upload_too_large")
                target.write(chunk)
        uploaded = await run_in_threadpool(
            inbox.upload_capture_slot,
            session,
            slot,
            stream=iter(()),
            media_type=request.headers.get("content-type"),
            staged_path=staged_path,
        )
    except inbox.storage.UploadTooLarge as exc:
        raise HTTPException(status_code=413, detail="upload_too_large") from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409
            if str(exc) == "capture_upload_slot_not_uploadable"
            else 400,
            detail=str(exc),
        ) from exc
    except inbox.staging_leases.StagingLeaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        if staged_path is not None:
            try:
                if inbox.staging_leases.remove_capture_slot_staging(
                    session, slot_id=slot.id
                ):
                    session.commit()
            except Exception:
                session.rollback()
    return inbox.slot_read(uploaded)


@router.post("/{item_id}/capture-upload-finalize", response_model=InboxItemRead)
def finalize_capture_upload(
    item_id: int,
    current_user: User = Depends(require_user_or_browser_import_user),
    session: Session = Depends(get_session),
) -> InboxItemRead:
    return inbox.read(
        inbox.finalize_capture_upload(session, current_user, item_id), session
    )


@router.post(
    "/browser-upload",
    response_model=InboxItemRead,
    status_code=status.HTTP_201_CREATED,
)
async def capture_browser_upload(
    file: UploadFile = File(...),
    source_url: str = Form(..., min_length=1, max_length=2048),
    title: str | None = Form(None, max_length=255),
    capture_source: str | None = Form(None, max_length=262144),
    current_user: User = Depends(require_user_or_browser_import_user),
    session: Session = Depends(get_session),
) -> InboxItemRead:
    """Accept browser-selected model bytes and optional bounded provenance."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename_required")
    try:
        row = await run_in_threadpool(
            inbox.create_browser_upload,
            session,
            current_user,
            source_url=source_url,
            title=title,
            capture_source=capture_source,
            filename=file.filename,
            stream=file.file,
        )
    except inbox.storage.UploadTooLarge as exc:
        raise HTTPException(status_code=413, detail="upload_too_large") from exc
    except inbox.staging_leases.StagingCapacityExceeded as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc
    except (ValueError, inbox.importer.ImportError_) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return inbox.read(row, session)


@router.get("", response_model=list[InboxItemRead])
def list_items(
    include_completed: bool = Query(True),
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> list[InboxItemRead]:
    return inbox.list_visible(
        session, current_user, include_completed=include_completed
    )


@router.post(
    "/batch", response_model=list[InboxItemRead], dependencies=[Depends(require_auth)]
)
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
        assert row.id is not None
        if payload.action == "set_collection":
            row = inbox.update(
                session,
                current_user,
                row,
                InboxItemUpdate(collection_id=payload.collection_id),
            )
        elif payload.action == "add_tags":
            tags = list(
                dict.fromkeys(
                    [*inbox.requested_tags(row.requested_tags_json), *payload.tags]
                )
            )
            row = inbox.update(session, current_user, row, InboxItemUpdate(tags=tags))
        elif payload.action == "retry":
            row = inbox.retry(session, row)
            assert row.id is not None
            if row.state == InboxItemState.CAPTURED:
                background_tasks.add_task(inbox.resolve, row.id)
        elif payload.action == "import":
            if row.state != InboxItemState.REVIEW:
                continue
            background_tasks.add_task(inbox.run_import, row.id, [], session_factory)
        else:
            inbox.dismiss(session, row)
        session.refresh(row)
        output.append(inbox.read(row, session))
    return output


@router.get("/{item_id}", response_model=InboxItemRead)
def get_item(
    item_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> InboxItemRead:
    return inbox.read(inbox.require_visible(session, current_user, item_id), session)


@router.patch(
    "/{item_id}", response_model=InboxItemRead, dependencies=[Depends(require_auth)]
)
def update_item(
    item_id: int,
    payload: InboxItemUpdate,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> InboxItemRead:
    row = inbox.require_visible(session, current_user, item_id)
    return inbox.read(inbox.update(session, current_user, row, payload), session)


@router.post(
    "/{item_id}/resolve",
    response_model=InboxItemRead,
    dependencies=[Depends(require_auth)],
)
def resolve_item(
    item_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> InboxItemRead:
    row = inbox.require_visible(session, current_user, item_id)
    if row.state not in {InboxItemState.CAPTURED, InboxItemState.FAILED}:
        raise HTTPException(status_code=409, detail="pending_import_not_resolvable")
    assert row.id is not None
    background_tasks.add_task(inbox.resolve, row.id)
    return inbox.read(row, session)


@router.post(
    "/{item_id}/import",
    response_model=InboxItemRead,
    dependencies=[Depends(require_auth)],
)
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
    assert row.id is not None
    inbox.validate_import_selection(row, payload.selected_ids)
    background_tasks.add_task(
        inbox.run_import, row.id, payload.selected_ids, session_factory
    )
    return inbox.read(row, session)


@router.post(
    "/{item_id}/retry",
    response_model=InboxItemRead,
    dependencies=[Depends(require_auth)],
)
def retry_item(
    item_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
    session_factory: SessionFactory = Depends(get_session_factory),
) -> InboxItemRead:
    row = inbox.retry(session, inbox.require_visible(session, current_user, item_id))
    assert row.id is not None
    if row.state == InboxItemState.CAPTURED:
        background_tasks.add_task(inbox.resolve, row.id)
    elif row.state == InboxItemState.REVIEW:
        selected = inbox.selected_ids(row.manifest_json)
        inbox.validate_import_selection(row, selected)
        background_tasks.add_task(
            inbox.run_import,
            row.id,
            selected,
            session_factory,
        )
    return inbox.read(row, session)


@router.delete(
    "/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_auth)],
)
def dismiss_item(
    item_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> Response:
    inbox.dismiss(session, inbox.require_visible(session, current_user, item_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
