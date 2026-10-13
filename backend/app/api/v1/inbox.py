from __future__ import annotations

from contextlib import AbstractContextManager, asynccontextmanager
from io import BytesIO
from pathlib import Path
from typing import AsyncIterator, BinaryIO

from anyio import CancelScope, CapacityLimiter, to_thread
from fastapi import (
    APIRouter,
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
from sqlmodel import Session

from app.api.command_actor import (
    CommandActor,
    command_session,
    require_capture_actor,
    require_command_writer,
)
from app.api.command_execution import bounded_command, run_command
from app.core.config import settings
from app.core.security import require_auth, require_user
from app.db.models import (
    InboxItemState,
    InboxSourceKind,
    JobKind,
    User,
)
from app.db.session import get_session, get_session_factory
from app.modules.ingestion import importer, inbox, staging_leases
from app.modules.storage import storage
from app.modules.work import nudge
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

router = APIRouter(prefix="/inbox", tags=["pending imports"])


@router.post(
    "",
    response_model=InboxItemRead,
    status_code=status.HTTP_202_ACCEPTED,
)
@bounded_command
def capture(
    payload: InboxItemCreate,
    actor: CommandActor = Depends(require_capture_actor),
) -> InboxItemRead:
    with command_session(actor) as (session, current_user):
        try:
            row = inbox.create(session, current_user, payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except importer.ImportError_ as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        assert row.id is not None
        if row.state == InboxItemState.CAPTURED:
            nudge(JobKind.INGESTION_INBOX_RESOLVE)
        return inbox.read(row, session)


def _start_import(session: Session, row, selected_ids: list[str]) -> None:
    if inbox.begin_import(session, row, selected_ids) is not None:
        nudge(JobKind.INGESTION_INBOX_IMPORT)
    session.refresh(row)


@router.post(
    "/capture-upload-slots",
    response_model=CaptureUploadSlotsRead,
    status_code=status.HTTP_201_CREATED,
)
@bounded_command
def create_capture_upload_slots(
    payload: CaptureUploadSlotsCreate,
    actor: CommandActor = Depends(require_capture_actor),
) -> CaptureUploadSlotsRead:
    with command_session(actor) as (session, current_user):
        try:
            row, slots = inbox.create_capture_upload_slots(
                session, current_user, payload
            )
        except staging_leases.StagingCapacityExceeded as exc:
            raise HTTPException(status_code=507, detail=str(exc)) from exc
        except (ValueError, importer.ImportError_) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return CaptureUploadSlotsRead(
            item=inbox.read(row, session),
            slots=[inbox.slot_read(slot) for slot in slots],
        )


@router.put("/capture-upload-slots/{slot_id}", response_model=CaptureUploadSlotRead)
async def put_capture_upload_slot(
    slot_id: str,
    request: Request,
    actor: CommandActor = Depends(require_capture_actor),
) -> CaptureUploadSlotRead:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="invalid_content_length"
            ) from exc
        if declared_length > settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="upload_too_large")
    staged_path: Path | None = None
    try:
        staged_path = await run_command(_prepare_capture_upload, actor, slot_id)
        received = 0
        # No SQL Session or thread is retained while waiting for the sender.
        async with _capture_slot_writer(slot_id) as target:
            async for chunk in request.stream():
                received += len(chunk)
                if received > settings.max_upload_bytes:
                    raise HTTPException(status_code=413, detail="upload_too_large")
                await run_command(target.write, chunk)
        uploaded = await run_command(
            _publish_capture_slot,
            actor,
            slot_id,
            media_type=request.headers.get("content-type"),
            staged_path=staged_path,
        )
    except storage.UploadTooLarge as exc:
        raise HTTPException(status_code=413, detail="upload_too_large") from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409
            if str(exc) == "capture_upload_slot_not_uploadable"
            else 400,
            detail=str(exc),
        ) from exc
    except staging_leases.StagingLeaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        if staged_path is not None:
            with CancelScope(shield=True):
                await to_thread.run_sync(
                    _cleanup_capture_slot_staging, slot_id, limiter=CapacityLimiter(1)
                )
    return uploaded


def _prepare_capture_upload(actor: CommandActor, slot_id: str) -> Path:
    with command_session(actor) as (session, user):
        inbox.require_capture_slot(session, user, slot_id)
        return staging_leases.prepare_capture_slot_staging(session, slot_id=slot_id)


def _open_capture_spool(
    slot_id: str,
) -> tuple[AbstractContextManager[BinaryIO], BinaryIO]:
    with get_session_factory().scoped_session() as session:
        context = staging_leases.open_capture_slot_staging(session, slot_id=slot_id)
        target = context.__enter__()
        # The context retains an open, identity-checked descriptor. Its exit only
        # flushes and validates that descriptor/path; it does not access SQL.
        return context, target


@asynccontextmanager
async def _capture_slot_writer(slot_id: str) -> AsyncIterator[BinaryIO]:
    exit_limiter = CapacityLimiter(1)
    with CancelScope(shield=True):
        context, target = await run_command(_open_capture_spool, slot_id)
    try:
        yield target
    except BaseException as exc:
        with CancelScope(shield=True):
            await to_thread.run_sync(
                context.__exit__,
                type(exc),
                exc,
                exc.__traceback__,
                limiter=exit_limiter,
            )
        raise
    else:
        with CancelScope(shield=True):
            await to_thread.run_sync(
                context.__exit__, None, None, None, limiter=exit_limiter
            )


def _publish_capture_slot(
    actor: CommandActor,
    slot_id: str,
    *,
    media_type: str | None,
    staged_path: Path,
) -> CaptureUploadSlotRead:
    with command_session(actor) as (session, user), BytesIO() as empty_stream:
        # Browser authentication flushes last_used_at. Finish that audit write
        # before the durable storage reservation needs its own SQLite connection;
        # slot ownership is then checked in the publication command's transaction.
        session.commit()
        slot = inbox.require_capture_slot(session, user, slot_id)
        uploaded = inbox.upload_capture_slot(
            session,
            slot,
            stream=empty_stream,
            media_type=media_type,
            staged_path=staged_path,
        )
        return inbox.slot_read(uploaded)


def _cleanup_capture_slot_staging(slot_id: str) -> None:
    with get_session_factory().scoped_session() as session:
        try:
            if staging_leases.remove_capture_slot_staging(session, slot_id=slot_id):
                session.commit()
        except Exception:
            session.rollback()


@router.post("/{item_id}/capture-upload-finalize", response_model=InboxItemRead)
@bounded_command
def finalize_capture_upload(
    item_id: int,
    actor: CommandActor = Depends(require_capture_actor),
) -> InboxItemRead:
    with command_session(actor) as (session, current_user):
        return inbox.read(
            inbox.finalize_capture_upload(session, current_user, item_id), session
        )


@router.delete("/{item_id}/capture-upload", status_code=status.HTTP_204_NO_CONTENT)
@bounded_command
def cancel_capture_upload(
    item_id: int,
    actor: CommandActor = Depends(require_capture_actor),
) -> Response:
    """Release an unfinished capture's exact slot leases after extension failure."""
    with command_session(actor) as (session, current_user):
        row = inbox.require_visible(session, current_user, item_id)
        if row.owner_user_id != current_user.id:
            raise HTTPException(status_code=404, detail="pending_import_not_found")
        if (
            row.source_kind != InboxSourceKind.BROWSER
            or row.state != InboxItemState.CAPTURED
        ):
            raise HTTPException(status_code=409, detail="capture_not_pending")
        inbox.dismiss(session, row)
        return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/browser-upload",
    response_model=InboxItemRead,
    status_code=status.HTTP_201_CREATED,
)
@bounded_command
def capture_browser_upload(
    file: UploadFile = File(...),
    source_url: str = Form(..., min_length=1, max_length=2048),
    title: str | None = Form(None, max_length=255),
    capture_source: str | None = Form(None, max_length=262144),
    actor: CommandActor = Depends(require_capture_actor),
) -> InboxItemRead:
    """Accept browser-selected model bytes and optional bounded provenance."""
    with command_session(actor) as (session, current_user):
        if not file.filename:
            raise HTTPException(status_code=400, detail="filename_required")
        try:
            row = inbox.create_browser_upload(
                session,
                current_user,
                source_url=source_url,
                title=title,
                capture_source=capture_source,
                filename=file.filename,
                stream=file.file,
            )
        except storage.UploadTooLarge as exc:
            raise HTTPException(status_code=413, detail="upload_too_large") from exc
        except staging_leases.StagingCapacityExceeded as exc:
            raise HTTPException(status_code=507, detail=str(exc)) from exc
        except (ValueError, importer.ImportError_) as exc:
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


@router.post("/batch", response_model=list[InboxItemRead])
@bounded_command
def batch_items(
    payload: InboxBatchRequest,
    actor: CommandActor = Depends(require_command_writer),
) -> list[InboxItemRead]:
    with command_session(actor) as (session, current_user):
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
                row = inbox.update(
                    session, current_user, row, InboxItemUpdate(tags=tags)
                )
            elif payload.action == "retry":
                row = inbox.retry(session, row)
                assert row.id is not None
                if row.state == InboxItemState.CAPTURED:
                    nudge(JobKind.INGESTION_INBOX_RESOLVE)
            elif payload.action == "import":
                if row.state != InboxItemState.REVIEW:
                    continue
                _start_import(session, row, [])
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
)
@bounded_command
def resolve_item(
    item_id: int,
    actor: CommandActor = Depends(require_command_writer),
) -> InboxItemRead:
    with command_session(actor) as (session, current_user):
        row = inbox.require_visible(session, current_user, item_id)
        if row.state not in {InboxItemState.CAPTURED, InboxItemState.FAILED}:
            raise HTTPException(status_code=409, detail="pending_import_not_resolvable")
        assert row.id is not None
        if row.state == InboxItemState.FAILED:
            # Re-resolving is intent: the item becomes CAPTURED, which is exactly
            # what the resolve source looks for.
            row.state = InboxItemState.CAPTURED
            row.error_code = None
            session.add(row)
            session.commit()
            session.refresh(row)
        nudge(JobKind.INGESTION_INBOX_RESOLVE)
        return inbox.read(row, session)


@router.post(
    "/{item_id}/import",
    response_model=InboxItemRead,
)
@bounded_command
def import_item(
    item_id: int,
    payload: InboxImportRequest,
    actor: CommandActor = Depends(require_command_writer),
) -> InboxItemRead:
    with command_session(actor) as (session, current_user):
        row = inbox.require_visible(session, current_user, item_id)
        if row.state != InboxItemState.REVIEW:
            raise HTTPException(status_code=409, detail="pending_import_not_ready")
        assert row.id is not None
        _start_import(session, row, payload.selected_ids)
        return inbox.read(row, session)


@router.post(
    "/{item_id}/retry",
    response_model=InboxItemRead,
)
@bounded_command
def retry_item(
    item_id: int,
    actor: CommandActor = Depends(require_command_writer),
) -> InboxItemRead:
    with command_session(actor) as (session, current_user):
        row = inbox.retry(
            session, inbox.require_visible(session, current_user, item_id)
        )
        assert row.id is not None
        if row.state == InboxItemState.CAPTURED:
            nudge(JobKind.INGESTION_INBOX_RESOLVE)
        elif row.state == InboxItemState.REVIEW:
            _start_import(session, row, inbox.selected_ids(row.manifest_json))
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
