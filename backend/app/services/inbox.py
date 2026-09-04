from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
import zipfile
from datetime import timedelta
from pathlib import Path
from typing import Any, BinaryIO, Literal, cast
from urllib.parse import urlsplit

from fastapi import HTTPException
from printstash_core.imports import (
    CaptureContractError,
    CaptureManifestV2,
    CaptureSource,
    ResolvedAsset,
    StagedAsset,
    canonicalize_provider_url,
)
from printstash_core.imports.contracts import MAX_MANIFEST_BYTES
from pydantic import TypeAdapter
from sqlalchemy import update as sql_update
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.metrics import record_capture_operation
from app.core.time import utcnow
from app.db.models import (
    SUFFIX_TO_FILE_TYPE,
    ArtifactProvenanceLink,
    BackgroundJob,
    CaptureUploadSlot,
    CaptureUploadSlotState,
    Collection,
    CollectionRole,
    InboxItem,
    InboxItemCompletion,
    InboxItemResult,
    InboxItemState,
    InboxSourceKind,
    ModelProvenanceSource,
    StagingLease,
    StorageDeleteIntent,
    User,
)
from app.db.session import SessionFactory, get_session_factory
from app.schemas.inbox import (
    CaptureSourceDraft,
    CaptureUploadSlotRead,
    CaptureUploadSlotsCreate,
    InboxItemCreate,
    InboxItemRead,
    InboxItemResultRead,
    InboxItemUpdate,
    InboxManifestRead,
)
from app.services import (
    import_resolvers,
    importer,
    rbac,
    source_covers,
    staging_leases,
    storage,
)
from app.services.hashing import sha256_file
from app.services.jobs import registry, safe_error, safe_item
from app.services.provider_redaction import redact_url
from app.services.source_cover_processing import process_source_cover_upload
from app.services.storage_backend import (
    CreationReceipt,
    StorageBackend,
    StorageCollisionError,
    get_backend,
)
from app.services.storage_deletion import enqueue_creation_receipt
from app.services.storage_ownership import publish_file
from app.services.storage_paths import unlink_managed_file

_inbox_manifest_adapter = TypeAdapter(InboxManifestRead)


def sanitize_source_url(value: str) -> str:
    try:
        raw_parts = urlsplit(value.strip())
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("url_invalid") from exc
    if raw_parts.username is not None or raw_parts.password is not None:
        # Userinfo is never a valid source URL credential.  Check the raw URL
        # before redaction so the durable boundary cannot silently turn
        # ``https://user:secret@host`` into a different, credential-free URL.
        raise ValueError("url_invalid")
    sanitized = redact_url(value)
    if sanitized == "[redacted-url]":
        raise ValueError("url_invalid")
    parts = urlsplit(sanitized)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("url_invalid")
    return sanitized


def _canonical_capture_source_url(
    provider: str, value: str, source_item_id: str | None
) -> str:
    """Canonicalize submitted and captured page evidence identically."""
    try:
        raw = urlsplit(value)
        if raw.username is not None or raw.password is not None:
            raise ValueError("credentials")
        if raw.query or raw.fragment:
            raise ValueError("query_or_fragment")
        return canonicalize_provider_url(provider, value, source_item_id)
    except (CaptureContractError, TypeError, ValueError) as exc:
        raise importer.ImportError_("capture_source_url_not_canonical") from exc


def _json_dict(value: str) -> dict:
    try:
        result = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


def requested_tags(value: str) -> list[str]:
    try:
        result = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return [str(item) for item in result] if isinstance(result, list) else []


def selected_ids(value: str) -> list[str]:
    selected = _json_dict(value).get("selected_ids", [])
    return [str(item) for item in selected] if isinstance(selected, list) else []


def validate_import_selection(row: InboxItem, requested: list[str]) -> list[str]:
    """Resolve and validate an Inbox import selection without permissive fallback.

    Legacy manifests retain their historical selection behavior.  V2 manifests
    are different: once a caller supplies a non-empty selection, every ID must
    identify a file in that manifest.  A bad or mixed selection is rejected as
    one request so downstream staging cannot silently fall back to all files.
    Empty requests continue to use the manifest's documented default.
    """
    manifest = _json_dict(row.manifest_json)
    selected_value: Any = requested or manifest.get("selected_ids") or []
    if manifest.get("schema_version") == 2 and not selected_value:
        files = manifest.get("files")
        selected_value = (
            [
                item["id"]
                for item in files
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            ]
            if isinstance(files, list)
            else []
        )
    if not isinstance(selected_value, list):
        selected_value = []
    selected = list(selected_value)
    if manifest.get("schema_version") != 2 or not selected:
        return selected

    files = manifest.get("files")
    valid_ids = (
        {
            item.get("id")
            for item in files
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        if isinstance(files, list)
        else set()
    )
    string_selection = [item for item in selected if isinstance(item, str)]
    if (
        not valid_ids
        or len(string_selection) != len(selected)
        or len(string_selection) != len(set(string_selection))
        or any(not item or item not in valid_ids for item in string_selection)
    ):
        raise HTTPException(status_code=422, detail="file_selection_invalid")
    return selected


def read(
    row: InboxItem,
    session: Session | None = None,
    results: list[InboxItemResult] | None = None,
) -> InboxItemRead:
    assert row.id is not None
    if results is None and session is not None and row.id is not None:
        results = session.exec(
            select(InboxItemResult)
            .where(InboxItemResult.inbox_item_id == row.id)
            .order_by(InboxItemResult.id)  # type: ignore[attr-defined]
        ).all()
    result_reads = [InboxItemResultRead.model_validate(item) for item in results or []]
    return InboxItemRead(
        id=row.id,
        owner_user_id=row.owner_user_id,
        source_kind=row.source_kind,
        source_url=row.source_url,
        display_title=row.display_title,
        source_hostname=row.source_hostname,
        state=row.state,
        manifest=_inbox_manifest_adapter.validate_python(_json_dict(row.manifest_json)),
        target_collection_id=row.target_collection_id,
        requested_tags=requested_tags(row.requested_tags_json),
        background_job_id=row.background_job_id,
        resulting_model_id=row.resulting_model_id,
        results=result_reads,
        error_code=row.error_code,
        retryable=row.retryable,
        attempt_count=row.attempt_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
        completion=row.completion,
    )


def require_visible(session: Session, user: User, item_id: int) -> InboxItem:
    row = session.get(InboxItem, item_id)
    if row is None or (not user.is_superuser and row.owner_user_id != user.id):
        raise HTTPException(status_code=404, detail="pending_import_not_found")
    return row


def list_visible(
    session: Session, user: User, *, include_completed: bool = True
) -> list[InboxItemRead]:
    stmt = select(InboxItem)
    stmt = stmt.where(InboxItem.state != InboxItemState.DISMISSED)
    if not user.is_superuser:
        stmt = stmt.where(InboxItem.owner_user_id == user.id)
    if not include_completed:
        stmt = stmt.where(InboxItem.state != InboxItemState.COMPLETED)
    rows = session.exec(
        stmt.order_by(InboxItem.updated_at.desc(), InboxItem.id.desc())  # type: ignore[attr-defined]
    ).all()
    item_ids = [row.id for row in rows if row.id is not None]
    all_results = (
        session.exec(
            select(InboxItemResult)
            .where(InboxItemResult.inbox_item_id.in_(item_ids))  # type: ignore[attr-defined]
            .order_by(InboxItemResult.id)  # type: ignore[attr-defined]
        ).all()
        if item_ids
        else []
    )
    by_item: dict[int, list[InboxItemResult]] = {}
    for result in all_results:
        by_item.setdefault(result.inbox_item_id, []).append(result)
    return [read(row, results=by_item.get(row.id or -1, [])) for row in rows]


def prune_history(retention_days: int = 30) -> int:
    """Bound terminal capture history without recursively touching staging."""
    cutoff = utcnow() - timedelta(days=retention_days)
    with get_session_factory().scoped_session() as session:
        rows = session.exec(
            select(InboxItem).where(
                col(InboxItem.state).in_(
                    (InboxItemState.COMPLETED, InboxItemState.DISMISSED)
                ),
                InboxItem.updated_at < cutoff,
            )
        ).all()
        for row in rows:
            session.delete(row)
        session.commit()
        return len(rows)


def prune_expired_browser_leases() -> int:
    """Expire browser review items only after their exact lease is released."""
    from app.services.storage_deletion import process_storage_delete_intents

    with get_session_factory().scoped_session() as session:
        expired = session.exec(
            select(StagingLease).where(StagingLease.expires_at <= utcnow())
        ).all()
        browser_lease_ids = {
            lease.id: lease.inbox_item_id
            for lease in expired
            if lease.inbox_item_id is not None
        }
        expired_slot_item_ids: dict[int, bool] = {}
        for lease in expired:
            slot_id = (
                lease.capture_upload_slot_id or lease.capture_upload_slot_origin_id
            )
            if slot_id is None:
                continue
            slot = session.get(CaptureUploadSlot, slot_id)
            if slot is None:
                continue
            expired_slot_item_ids[slot.inbox_item_id] = (
                expired_slot_item_ids.get(slot.inbox_item_id, False)
                or lease.background_job_id is not None
            )
        expired_items = 0
        for inbox_item_id, job_owned in expired_slot_item_ids.items():
            row = session.get(InboxItem, inbox_item_id)
            if row is None or row.state in {
                InboxItemState.COMPLETED,
                InboxItemState.DISMISSED,
            }:
                continue
            # Enqueue first: source rows/leases disappear only with a durable
            # receipt-backed intent in this same transaction.
            if not _cleanup_capture_slots(session, row):
                continue
            row.state = InboxItemState.FAILED
            row.error_code = "staging_expired"
            # A browser-review expiry is terminal. A job-owned lease means an
            # interrupted import and remains retryable after exact cleanup.
            row.retryable = job_owned
            row.staging_key = None
            row.updated_at = utcnow()
            session.add(row)
            expired_items += 1
        staging_leases.prune_expired(session)
        for lease_id, inbox_item_id in browser_lease_ids.items():
            if session.get(StagingLease, lease_id) is not None:
                # An unlink failure deliberately retains its capacity charge.
                continue
            row = session.get(InboxItem, inbox_item_id)
            if row is None or row.state != InboxItemState.REVIEW:
                continue
            row.state = InboxItemState.FAILED
            row.error_code = "staging_expired"
            row.retryable = False
            row.staging_key = None
            row.updated_at = utcnow()
            session.add(row)
            expired_items += 1
        session.commit()
    # The processor runs only after the enqueue transaction committed.
    process_storage_delete_intents()
    return expired_items


def _require_target(session: Session, user: User, collection_id: int | None) -> None:
    if collection_id is None:
        if not user.is_superuser:
            raise HTTPException(
                status_code=403, detail="root_collection_admin_required"
            )
        return
    rbac.require_collection_role(session, user, collection_id, CollectionRole.EDIT)


def create(session: Session, user: User, payload: InboxItemCreate) -> InboxItem:
    if user.id is None:
        raise ValueError("not_authenticated")
    if payload.capture_source is not None:
        raise importer.ImportError_("user_file_required")
    source_url = sanitize_source_url(payload.url)
    source = _capture_source_draft(payload.capture_source, source_url)
    importer.validate_public_url(source_url)
    if payload.collection_id is not None:
        _require_target(session, user, payload.collection_id)
    title = safe_item(payload.title) if payload.title else None
    row = InboxItem(
        owner_user_id=user.id,
        source_kind=payload.source_kind,
        source_url=source_url,
        source_hostname=urlsplit(source_url).hostname,
        display_title=title,
        target_collection_id=payload.collection_id,
        requested_tags_json=json.dumps(payload.tags[:100]),
        manifest_json=json.dumps(
            {"kind": "capture_upload_pending", "source": source.to_dict()}
            if source is not None
            else {},
            separators=(",", ":"),
        ),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _capture_source_draft(
    value: CaptureSourceDraft | None, source_url: str
) -> CaptureSource | None:
    if value is None:
        return None
    raw = value.model_dump(mode="json")
    encoded = json.dumps(raw, separators=(",", ":")).encode()
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise importer.ImportError_("capture_source_too_large")
    try:
        submitted = _canonical_capture_source_url(
            value.provider, source_url, value.source_item_id
        )
        captured = _canonical_capture_source_url(
            value.provider, value.canonical_url, value.source_item_id
        )
    except (KeyError, TypeError, ValueError, importer.ImportError_) as exc:
        raise importer.ImportError_("capture_source_url_mismatch") from exc
    if submitted != captured:
        raise importer.ImportError_("capture_source_url_mismatch")
    try:
        source = CaptureSource.from_dict(raw)
    except (ValueError, CaptureContractError) as exc:
        raise importer.ImportError_("capture_source_invalid") from exc
    try:
        submitted = _canonical_capture_source_url(
            source.provider, source_url, source.source_item_id
        )
        captured = _canonical_capture_source_url(
            source.provider, raw["canonical_url"], source.source_item_id
        )
    except (KeyError, TypeError) as exc:
        raise importer.ImportError_("capture_source_url_not_canonical") from exc
    if source.canonical_url not in {submitted, captured} or submitted != captured:
        raise importer.ImportError_("capture_source_url_mismatch")
    return source


def slot_read(slot: CaptureUploadSlot) -> CaptureUploadSlotRead:
    return CaptureUploadSlotRead(
        id=slot.id,
        role=cast(Literal["file", "cover"], slot.role),
        source_file_id=slot.source_file_id,
        filename=slot.filename,
        media_type=slot.media_type,
        size_bytes=slot.size_bytes,
        sha256=slot.sha256,
        state=slot.state.value,
    )


def create_capture_upload_slots(
    session: Session, user: User, payload: CaptureUploadSlotsCreate
) -> tuple[InboxItem, list[CaptureUploadSlot]]:
    if user.id is None:
        raise ValueError("not_authenticated")
    source_url = sanitize_source_url(payload.source_url)
    source = _capture_source_draft(payload.capture_source, source_url)
    assert source is not None
    if payload.collection_id is not None:
        _require_target(session, user, payload.collection_id)
    files = [
        {
            "id": item.id,
            "name": Path(item.filename.replace("\\", "/")).name,
            "file_type": Path(item.filename).suffix.lower().lstrip("."),
            "size": item.size_bytes,
        }
        for item in payload.files
    ]
    try:
        manifest = CaptureManifestV2.from_dict(
            {
                "schema_version": 2,
                "kind": "model_files",
                "source": source.to_dict(),
                "files": files,
                "selected_ids": [item["id"] for item in files],
            }
        ).to_dict()
    except CaptureContractError as exc:
        raise importer.ImportError_("capture_manifest_invalid") from exc
    row = InboxItem(
        owner_user_id=user.id,
        source_kind=InboxSourceKind.BROWSER,
        source_url=source_url,
        source_hostname=urlsplit(source_url).hostname,
        display_title=safe_item(payload.title) if payload.title else None,
        state=InboxItemState.CAPTURED,
        manifest_json=json.dumps(manifest, separators=(",", ":")),
        target_collection_id=payload.collection_id,
        requested_tags_json=json.dumps(payload.tags[:100]),
    )
    session.add(row)
    session.flush()
    assert row.id is not None
    slots: list[CaptureUploadSlot] = []
    backend = get_backend()
    declarations: list[tuple[Literal["file", "cover"], Any, str | None]] = [
        ("file", item, item.id) for item in payload.files
    ]
    if payload.cover is not None:
        declarations.append(("cover", payload.cover, None))
    for role, item, source_file_id in declarations:
        filename = Path(item.filename.replace("\\", "/")).name
        if not filename:
            raise importer.ImportError_("filename_required")
        slot = CaptureUploadSlot(
            id=uuid.uuid4().hex,
            inbox_item_id=row.id,
            role=role,
            source_file_id=source_file_id,
            filename=filename,
            media_type=item.media_type.lower(),
            size_bytes=item.size_bytes,
            sha256=item.sha256,
            storage_key=backend.capture_upload_slot_key(uuid.uuid4().hex),
        )
        session.add(slot)
        session.flush()
        assert slot.storage_key is not None
        staging_leases.create_capture_slot_lease(
            session,
            slot_id=slot.id,
            owner_user_id=user.id,
            destination_key=slot.storage_key,
            size_bytes=slot.size_bytes,
            sha256=slot.sha256,
        )
        slots.append(slot)
    session.commit()
    session.refresh(row)
    return row, slots


def require_capture_slot(
    session: Session, user: User, slot_id: str
) -> CaptureUploadSlot:
    slot = session.get(CaptureUploadSlot, slot_id)
    if slot is None:
        raise HTTPException(status_code=404, detail="capture_upload_slot_not_found")
    row = session.get(InboxItem, slot.inbox_item_id)
    if row is None or (not user.is_superuser and row.owner_user_id != user.id):
        raise HTTPException(status_code=404, detail="capture_upload_slot_not_found")
    return slot


def upload_capture_slot(
    session: Session,
    slot: CaptureUploadSlot,
    *,
    stream: BinaryIO,
    media_type: str | None,
    staged_path: Path | None = None,
) -> CaptureUploadSlot:
    """Validate the lease-owned spool before publishing its receipt.

    Direct service callers stream into the same deterministic, inode-bound
    spool used by the HTTP request handler.  There is deliberately no
    anonymous request/service temporary file for capture slots.
    """
    item = session.get(InboxItem, slot.inbox_item_id)
    if item is None or item.state != InboxItemState.CAPTURED:
        raise ValueError("capture_upload_slot_not_uploadable")
    if media_type is None or media_type.split(";", 1)[0].lower() != slot.media_type:
        raise ValueError("capture_upload_media_type_mismatch")
    temporary: Path | None = staged_path
    try:
        if temporary is None:
            temporary = staging_leases.prepare_capture_slot_staging(
                session, slot_id=slot.id
            )
            temporary, size, actual_sha256 = staging_leases.stage_capture_slot_stream(
                session,
                slot_id=slot.id,
                stream=stream,
                max_bytes=settings.max_upload_bytes,
            )
        else:
            expected = staging_leases.capture_slot_staging_path(slot.id)
            if temporary != expected:
                raise ValueError("capture_upload_staging_path_invalid")
            # The request boundary has already consumed the stream. Validate
            # the exact lease-owned inode and hash it once before publication.
            lease = staging_leases._capture_slot_lease(session, slot.id)
            if staging_leases._matching_capture_staging_path(lease) is None:
                raise ValueError("capture_upload_staging_collision")
            digest = hashlib.sha256()
            size = 0
            with temporary.open("rb") as incoming:
                while chunk := incoming.read(1024 * 1024):
                    size += len(chunk)
                    digest.update(chunk)
            actual_sha256 = digest.hexdigest()
        if size != slot.size_bytes:
            raise ValueError("capture_upload_size_mismatch")
        if actual_sha256 != slot.sha256:
            raise ValueError("capture_upload_sha256_mismatch")
        if slot.role == "cover":
            if (
                slot.media_type not in {"image/jpeg", "image/png", "image/webp"}
                or size > 15 * 1024 * 1024
            ):
                raise ValueError("capture_cover_invalid")
            # Validate dimensions/decoder safety now. Final WebP normalization is
            # deferred until the provenance source exists after artifact import.
            process_source_cover_upload(temporary.read_bytes(), slot.media_type)
        if slot.state == CaptureUploadSlotState.UPLOADED:
            return slot
        if slot.state != CaptureUploadSlotState.PENDING or not slot.storage_key:
            raise ValueError("capture_upload_slot_not_uploadable")
        backend = get_backend()
        # A prior process may have published the exact object and died before it
        # could commit the slot receipt. Reconcile that durable intent before a
        # create-only retry; a collision is never resolved by deleting blindly.
        if staging_leases.reconcile_capture_slot(session, backend, slot):
            session.commit()
            session.refresh(slot)
            return slot
        try:
            receipt = publish_file(
                session,
                backend,
                slot.storage_key,
                temporary,
                object_kind="capture_upload_slot",
                sha256=slot.sha256,
            )
        except StorageCollisionError:
            # The only object eligible for adoption is this slot's exact
            # declared key and content.  A foreign/mismatched collision
            # remains untouched and the pending lease remains retryable.
            if not staging_leases.reconcile_capture_slot(session, backend, slot):
                raise
            session.commit()
            session.refresh(slot)
            return slot
        if receipt.size != slot.size_bytes:
            backend.rollback_create(receipt)
            raise ValueError("capture_upload_size_mismatch")
        try:
            slot.state = CaptureUploadSlotState.UPLOADED
            slot.receipt_json = _receipt_json(receipt)
            slot.uploaded_at = utcnow()
            slot.updated_at = utcnow()
            session.add(slot)
            staging_leases.record_capture_slot_receipt(
                session, slot_id=slot.id, receipt=receipt
            )
            # Publication and the spool ownership transition are one durable
            # caller transaction. The exact inode is removed before commit;
            # a failed commit rolls the rows back and the backend receipt is
            # rolled back by the exception path.
            if not staging_leases.remove_capture_slot_staging(session, slot_id=slot.id):
                raise ValueError("capture_upload_staging_cleanup_failed")
            session.commit()
            session.refresh(slot)
            item = session.get(InboxItem, slot.inbox_item_id)
            manifest = _json_dict(item.manifest_json) if item is not None else {}
            source = manifest.get("source")
            provider = source.get("provider") if isinstance(source, dict) else "unknown"
            record_capture_operation(
                str(provider), "upload_slots", "success", 0.0, uploaded_bytes=size
            )
            return slot
        except Exception:
            session.rollback()
            backend.rollback_create(receipt)
            raise
    finally:
        # Error paths are also responsible for their exact owned spool. A
        # foreign replacement/collision makes this a no-op by design.
        if temporary is not None:
            try:
                if staging_leases.remove_capture_slot_staging(session, slot_id=slot.id):
                    session.commit()
            except Exception:
                session.rollback()


def _receipt_json(receipt: CreationReceipt) -> str:
    return json.dumps(
        {
            "key": receipt.key,
            "size": receipt.size,
            "token": receipt.token,
            "backend": receipt.backend,
            "namespace": receipt.namespace,
            "etag": receipt.etag,
            "version_id": receipt.version_id,
            "device": receipt.device,
            "inode": receipt.inode,
            "ctime_ns": receipt.ctime_ns,
            # The receipt is the binding captured at publication time. Never
            # derive this from the currently configured backend: a provider
            # switch between upload and cleanup must fail closed, not relabel
            # the object as belonging to its replacement.
            "provider_ref": receipt.provider_ref,
        },
        sort_keys=True,
    )


def _receipt_from_json(value: str | None) -> CreationReceipt | None:
    try:
        return CreationReceipt(**json.loads(value or ""))
    except (TypeError, ValueError):
        return None


def _cleanup_capture_slots(session: Session, row: InboxItem) -> bool:
    """Release exact slot receipts only after the whole inbox job is terminal."""
    if row.id is None:
        return False
    slots = list(
        session.exec(
            select(CaptureUploadSlot).where(CaptureUploadSlot.inbox_item_id == row.id)
        )
    )
    backend = get_backend()
    existing_intent_ids = {
        intent.id for intent in session.exec(select(StorageDeleteIntent)).all()
    }
    created_intents: list[StorageDeleteIntent] = []
    try:
        # Do not relinquish any slot ownership until every deletion intent is
        # durable. Compensate intents made in this caller transaction on a
        # later failure, rather than using a SQLite SAVEPOINT (whose release
        # can commit an otherwise rollbackable implicit transaction).
        for slot in slots:
            lease = session.exec(
                select(StagingLease).where(
                    (StagingLease.capture_upload_slot_id == slot.id)
                    | (StagingLease.capture_upload_slot_origin_id == slot.id)
                )
            ).first()
            if lease is None:
                raise ValueError("capture upload slot lease missing")
            if slot.state != CaptureUploadSlotState.UPLOADED:
                # A crash can leave bytes published while both receipt columns
                # are still empty. Reconcile before considering the slot safe
                # to remove. If no object exists, the durable intent is clean;
                # if an object exists but cannot be adopted, keep ownership.
                recovered = staging_leases.reconcile_capture_slot(
                    session, backend, slot
                )
                if (
                    not recovered
                    and slot.storage_key
                    and backend.object_info(slot.storage_key) is not None
                ):
                    raise ValueError("capture upload receipt missing")
            # A pending request can die after writing the deterministic spool
            # but before publication. Release that exact inode before its
            # lease/slot rows are removed; replacements remain untouched.
            # The lease may already belong to the background job, in which
            # case capture_upload_slot_id is intentionally NULL and the
            # origin column is the only durable slot identity left. Passing
            # the exact row avoids looking it up through the pre-import owner.
            if not staging_leases.remove_capture_slot_staging(session, lease=lease):
                raise ValueError("capture upload staging cleanup failed")
            receipt = _receipt_from_json(slot.receipt_json)
            if slot.state != CaptureUploadSlotState.UPLOADED:
                continue
            if receipt is None:
                raise ValueError("capture upload receipt missing")
            intent = enqueue_creation_receipt(
                session,
                backend,
                receipt,
                resource_kind="capture_upload_slot",
                resource_id=slot.id,
            )
            if intent.id not in existing_intent_ids:
                created_intents.append(intent)
        for slot in slots:
            lease = session.exec(
                select(StagingLease).where(
                    (StagingLease.capture_upload_slot_id == slot.id)
                    | (StagingLease.capture_upload_slot_origin_id == slot.id)
                )
            ).first()
            if lease is not None:
                session.delete(lease)
            session.delete(slot)
        session.flush()
        # Keep caller-held slot objects usable after the commit. SQLAlchemy
        # expires persistent instances on commit; an object whose row was
        # intentionally deleted would otherwise raise ObjectDeletedError when
        # a request/test reads its already-known id for a postcondition.
        for slot in slots:
            session.expunge(slot)
    except Exception:
        for intent in created_intents:
            session.delete(intent)
        session.flush()
        return False
    return True


def _attach_capture_cover(
    session: Session, row: InboxItem
) -> source_covers.SourceCoverWrite | bool | None:
    """Normalize a durable optional cover only after provenance has been attached."""
    if row.id is None or row.resulting_model_id is None:
        return True
    cover = session.exec(
        select(CaptureUploadSlot).where(
            CaptureUploadSlot.inbox_item_id == row.id,
            CaptureUploadSlot.role == "cover",
            CaptureUploadSlot.state == CaptureUploadSlotState.UPLOADED,
        )
    ).first()
    if cover is None or not cover.storage_key:
        return True
    manifest = _json_dict(row.manifest_json)
    source_raw = manifest.get("source")
    provider = source_raw.get("provider") if isinstance(source_raw, dict) else None
    source_item_id = (
        source_raw.get("source_item_id") if isinstance(source_raw, dict) else None
    )
    canonical_url = (
        source_raw.get("canonical_url") if isinstance(source_raw, dict) else None
    )
    if (
        provider is not None
        and not isinstance(provider, str)
        or source_item_id is not None
        and not isinstance(source_item_id, str)
        or not isinstance(canonical_url, str)
    ):
        return False
    query = select(ModelProvenanceSource).where(
        ModelProvenanceSource.model_id == row.resulting_model_id,
        ModelProvenanceSource.canonical_url == canonical_url,
    )
    if isinstance(provider, str):
        query = query.where(ModelProvenanceSource.provider == provider)
    if source_item_id is not None:
        exact = session.exec(
            query.where(ModelProvenanceSource.source_item_id == source_item_id)
        ).all()
        # Older provenance rows may predate source-item IDs. The canonical URL
        # remains a safe fallback only when it identifies exactly one source.
        sources = exact or session.exec(query).all()
    else:
        sources = session.exec(query).all()
    if len(sources) != 1 or sources[0].id is None:
        return False
    source = sources[0]
    backend = get_backend()
    data = backend.read_bytes(cover.storage_key)
    result = source_covers.put(
        session,
        backend,
        provenance_source_id=source.id,
        actor_id=row.owner_user_id,
        data=data,
        content_type=cover.media_type,
    )
    # Test doubles and legacy adapters may acknowledge without returning the
    # ownership write; production publication always returns it.
    return result if isinstance(result, source_covers.SourceCoverWrite) else True


def finalize_capture_upload(session: Session, user: User, item_id: int) -> InboxItem:
    row = require_visible(session, user, item_id)
    if (
        row.state != InboxItemState.CAPTURED
        or row.source_kind != InboxSourceKind.BROWSER
    ):
        raise HTTPException(status_code=409, detail="capture_upload_not_finalizable")
    slots = list(
        session.exec(
            select(CaptureUploadSlot).where(CaptureUploadSlot.inbox_item_id == row.id)
        )
    )
    if not slots or any(
        slot.role == "file" and slot.state != CaptureUploadSlotState.UPLOADED
        for slot in slots
    ):
        raise HTTPException(status_code=409, detail="capture_upload_slots_incomplete")
    row.state = InboxItemState.REVIEW
    row.updated_at = utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def create_browser_upload(
    session: Session,
    user: User,
    *,
    source_url: str,
    title: str | None,
    capture_source: str | None,
    filename: str,
    stream: BinaryIO,
) -> InboxItem:
    """Durably stage browser-selected bytes without reusing browser credentials.

    The source URL is metadata only: the server deliberately never resolves or
    fetches MakerWorld. The extension transfers the selected bytes while its
    browser tab owns the site session.
    """
    if user.id is None:
        raise ValueError("not_authenticated")
    clean_url = sanitize_source_url(source_url)
    source = _parse_capture_source(capture_source, clean_url)
    provider = import_resolvers.classify_page(clean_url)
    if source is None and provider != "makerworld":
        raise importer.ImportError_("makerworld_model_page_required")
    if source is not None and provider != source.provider:
        raise importer.ImportError_("capture_source_provider_url_mismatch")
    clean_name = Path(filename.replace("\\", "/")).name
    suffix = Path(clean_name).suffix.lower()
    if not clean_name:
        raise importer.ImportError_("filename_required")
    if suffix != ".zip" and suffix not in SUFFIX_TO_FILE_TYPE:
        raise importer.ImportError_("unsupported_file_type")
    if source is not None and source.provider == "thingiverse" and suffix == ".zip":
        raise importer.ImportError_("thingiverse_manual_file_required")

    row = InboxItem(
        owner_user_id=user.id,
        source_kind=InboxSourceKind.BROWSER,
        source_url=clean_url,
        source_hostname=urlsplit(clean_url).hostname,
        display_title=safe_item(title) if title else None,
        state=InboxItemState.RESOLVING,
    )
    session.add(row)
    session.flush()
    assert row.id is not None
    directory = settings.incoming_dir / "inbox" / str(row.id)
    directory.mkdir(parents=True, exist_ok=True)
    managed = directory / f"source{suffix}"
    try:
        digest = hashlib.sha256()
        size = storage.stream_to_path(
            stream, managed, max_bytes=settings.max_upload_bytes, digest=digest
        )
        if suffix == ".zip":
            entries = importer.inspect_archive(managed)
            legacy_manifest = {
                "kind": "archive",
                "title": safe_item(clean_name),
                "entries": [
                    {
                        "id": entry.name,
                        "name": entry.name,
                        "size": entry.size_bytes,
                        "file_type": entry.file_type,
                    }
                    for entry in entries
                    if entry.file_type
                ],
            }
        else:
            legacy_manifest = {
                "kind": "browser_file",
                "title": safe_item(clean_name),
                "filename": safe_item(clean_name),
                "size": size,
            }
        if source is not None:
            manifest = _local_capture_manifest(
                source, clean_name, size, suffix, entries if suffix == ".zip" else None
            )
        else:
            manifest = legacy_manifest
        row.manifest_json = json.dumps(manifest, separators=(",", ":"))
        row.staging_key = str(managed)
        row.display_title = row.display_title or legacy_manifest["title"]
        row.state = InboxItemState.REVIEW
        row.updated_at = utcnow()
        session.add(row)
        staging_leases.create_review_lease(
            session,
            inbox_item_id=row.id,
            owner_user_id=user.id,
            path=managed,
            size_bytes=size,
            sha256=digest.hexdigest(),
        )
        session.commit()
        session.refresh(row)
        record_capture_operation(
            source.provider if source is not None else "unknown",
            "browser_upload",
            "success",
            0.0,
            uploaded_bytes=size,
        )
        return row
    except Exception:
        session.rollback()
        managed.unlink(missing_ok=True)
        try:
            directory.rmdir()
        except OSError:
            pass
        raise


def _parse_capture_source(value: str | None, source_url: str) -> CaptureSource | None:
    """Validate the extension's bounded source object before staging any bytes."""
    if value is None:
        return None
    if len(value.encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise importer.ImportError_("capture_source_too_large")
    try:
        raw = json.loads(value)
        if isinstance(raw, dict) and "tags" not in raw:
            # Legacy browser-upload payloads predate structured source tags.
            raw = {**raw, "tags": []}
        if isinstance(raw, dict):
            raw_provider = raw.get("provider")
            raw_item_id = raw.get("source_item_id")
            raw_url = raw.get("canonical_url")
            if all(
                isinstance(item, str) for item in (raw_provider, raw_item_id, raw_url)
            ):
                try:
                    submitted = _canonical_capture_source_url(
                        raw_provider, source_url, raw_item_id
                    )
                    captured = _canonical_capture_source_url(
                        raw_provider, raw_url, raw_item_id
                    )
                except (KeyError, TypeError, ValueError, importer.ImportError_) as exc:
                    raise importer.ImportError_("capture_source_url_mismatch") from exc
                if submitted != captured:
                    raise importer.ImportError_("capture_source_url_mismatch")
        source = CaptureSource.from_dict(raw)
    except importer.ImportError_:
        raise
    except (TypeError, ValueError, CaptureContractError) as exc:
        raise importer.ImportError_("capture_source_invalid") from exc
    if source.provider not in {"makerworld", "printables", "thingiverse"}:
        raise importer.ImportError_("capture_source_provider_invalid")
    # CaptureSource deliberately removes query/fragment data.  Reject rather
    # than silently drop it at this trust boundary, especially signed URLs.
    if not isinstance(raw, dict) or not isinstance(raw.get("canonical_url"), str):
        raise importer.ImportError_("capture_source_url_not_canonical")
    try:
        submitted = _canonical_capture_source_url(
            source.provider, source_url, source.source_item_id
        )
        captured = _canonical_capture_source_url(
            source.provider, raw["canonical_url"], source.source_item_id
        )
    except (KeyError, TypeError) as exc:
        raise importer.ImportError_("capture_source_url_not_canonical") from exc
    if source.canonical_url not in {submitted, captured} or submitted != captured:
        raise importer.ImportError_("capture_source_url_mismatch")
    return source


def _local_capture_manifest(
    source: CaptureSource,
    filename: str,
    size: int,
    suffix: str,
    entries: list[Any] | None,
) -> dict[str, Any]:
    """Combine a trusted source draft with the files actually inspected locally."""
    if entries is None:
        files = [
            {"id": filename, "name": filename, "file_type": suffix[1:], "size": size}
        ]
    else:
        files = [
            {
                "id": entry.name,
                "name": entry.name,
                "file_type": entry.file_type,
                "size": entry.size_bytes,
            }
            for entry in entries
            if entry.file_type
        ]
    try:
        return CaptureManifestV2.from_dict(
            {
                "schema_version": 2,
                "kind": "model_files",
                "source": source.to_dict(),
                "files": files,
                "selected_ids": [item["id"] for item in files],
            }
        ).to_dict()
    except CaptureContractError as exc:
        raise importer.ImportError_("capture_manifest_invalid") from exc


def update(
    session: Session, user: User, row: InboxItem, payload: InboxItemUpdate
) -> InboxItem:
    if row.state in {
        InboxItemState.IMPORTING,
        InboxItemState.COMPLETED,
        InboxItemState.DISMISSED,
    }:
        raise HTTPException(status_code=409, detail="pending_import_not_editable")
    resolved_selection: list[str] | None = None
    if payload.selected_ids is not None:
        resolved_selection = validate_import_selection(row, payload.selected_ids)
    if payload.title is not None:
        row.display_title = safe_item(payload.title)
    if "collection_id" in payload.model_fields_set:
        _require_target(session, user, payload.collection_id)
        row.target_collection_id = payload.collection_id
    if payload.tags is not None:
        row.requested_tags_json = json.dumps(payload.tags[:100])
    if resolved_selection is not None:
        manifest = _json_dict(row.manifest_json)
        # An empty V2 request means the manifest default (all files), as it
        # does at import time.  Persist that resolved selection instead of
        # serializing ``[]``, which the strict V2 contract rejects.
        manifest["selected_ids"] = resolved_selection[:500]
        row.manifest_json = json.dumps(manifest, separators=(",", ":"))
    row.updated_at = utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _managed_staging(item_id: int, source: Path) -> Path:
    directory = settings.incoming_dir / "inbox" / str(item_id)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"source{source.suffix.lower()}"
    with source.open("rb") as incoming:
        storage.stream_to_path(incoming, target)
    try:
        source.unlink()
    except OSError:
        # The managed copy is complete and create-only. Preserve a duplicate
        # source rather than losing the durable staging reference.
        pass
    return target


def _begin_resolve(item_id: int) -> tuple[bool, str | None, int | None]:
    with get_session_factory().scoped_session() as session:
        row = session.get(InboxItem, item_id)
        if row is None or row.state not in {
            InboxItemState.CAPTURED,
            InboxItemState.FAILED,
        }:
            return False, None, None
        row.state = InboxItemState.RESOLVING
        row.error_code = None
        row.retryable = False
        row.attempt_count += 1
        row.updated_at = utcnow()
        session.add(row)
        session.commit()
        return True, row.source_url, row.owner_user_id


def _finish_resolve(
    item_id: int, manifest: dict[str, Any], managed: Path | None
) -> None:
    with get_session_factory().scoped_session() as session:
        row = session.get(InboxItem, item_id)
        if row is None:
            _discard_resolve_staging(session, item_id, managed)
            session.commit()
            return

        title = manifest.get("title")
        source = manifest.get("source")
        if isinstance(source, dict):
            fields = source.get("fields")
            if isinstance(fields, dict) and isinstance(fields.get("title"), dict):
                title = fields["title"].get("value")

        values: dict[str, Any] = {
            "manifest_json": json.dumps(manifest, separators=(",", ":")),
            "display_title": row.display_title or title,
            "state": InboxItemState.REVIEW,
            "updated_at": utcnow(),
        }
        if manifest.get("kind") == "archive" and managed is not None:
            values["staging_key"] = str(managed)

        # Resolution runs outside the request transaction. The state predicate
        # prevents a stale resolver from resurrecting an item dismissed while
        # its network work was in flight.
        result = session.exec(
            sql_update(InboxItem)
            .where(
                InboxItem.id == item_id,
                InboxItem.state == InboxItemState.RESOLVING,
            )
            .values(**values)
        )
        if result.rowcount != 1:  # type: ignore[attr-defined]
            session.rollback()
            _discard_resolve_staging(session, item_id, managed)
        session.commit()


def _discard_resolve_staging(
    session: Session, item_id: int, managed: Path | None
) -> None:
    """Release bytes produced by a resolver that lost its state race.

    A resolver normally owns no lease until review publication. If a caller
    supplied a lease for the exact managed path, use the lease's identity-aware
    cleanup instead of unlinking blindly; otherwise remove only the managed
    scratch file beneath the configured incoming root.
    """
    if managed is None:
        return
    lease = session.exec(
        select(StagingLease).where(
            StagingLease.inbox_item_id == item_id,
            StagingLease.path == str(managed),
        )
    ).first()
    if lease is not None:
        try:
            if not staging_leases.dismiss_review_lease(session, inbox_item_id=item_id):
                return
        except staging_leases.StagingLeaseError:
            # A concurrent ownership transfer keeps the lease as the durable
            # record of the bytes; never delete an object without its proof.
            return
    else:
        unlink_managed_file(managed, settings.incoming_dir)
    try:
        managed.parent.rmdir()
    except OSError:
        pass


def _fail_item(item_id: int, exc: Exception, fallback: str) -> None:
    with get_session_factory().scoped_session() as session:
        row = session.get(InboxItem, item_id)
        if row is not None:
            row.state = InboxItemState.FAILED
            row.error_code = safe_error(str(exc)) or fallback
            row.retryable = True
            row.updated_at = utcnow()
            session.add(row)
            session.commit()


def _prepare_archive(item_id: int, staged: Path, filename: str) -> tuple[dict, Path]:
    entries = importer.inspect_archive(staged)
    managed = _managed_staging(item_id, staged)
    return (
        {
            "kind": "archive",
            "title": safe_item(filename),
            "entries": [
                {
                    "id": entry.name,
                    "name": entry.name,
                    "size": entry.size_bytes,
                    "file_type": entry.file_type,
                }
                for entry in entries
                if entry.file_type
            ],
        },
        managed,
    )


async def resolve(item_id: int) -> None:
    started, source_url, owner_user_id = await asyncio.to_thread(
        _begin_resolve, item_id
    )
    if not started:
        return

    managed: Path | None = None
    try:
        if not source_url:
            raise importer.ImportError_("url_required")
        if import_resolvers.classify_collection(source_url):
            resolved = await import_resolvers.resolve_collection_url(source_url)
            if not resolved:
                raise importer.ImportError_("collection_resolve_failed")
            title, members = resolved
            manifest = {
                "kind": "collection",
                "title": safe_item(title),
                "members": [
                    {
                        "id": member.source_id,
                        "title": safe_item(member.title),
                        "page_url": sanitize_source_url(member.page_url),
                    }
                    for member in members
                ],
            }
        else:
            capture = await import_resolvers.resolve_capture_manifest(source_url)
            if capture is None and owner_user_id is not None:
                capture = await import_resolvers.resolve_connected_provider_capture(
                    source_url,
                    import_resolvers.ProviderResolutionContext(
                        owner_user_id=owner_user_id,
                        session_factory=get_session_factory(),
                    ),
                )
            if capture is not None:
                manifest = capture.to_dict()
            else:
                listing = await import_resolvers.list_model_files(source_url)
                if listing is not None:
                    title, files = listing
                    manifest = {
                        "kind": "model_files",
                        "title": safe_item(title),
                        "files": [
                            {
                                "id": item.file_id,
                                "name": safe_item(item.name),
                                "file_type": item.file_type,
                                "size": item.size,
                            }
                            for item in files
                        ],
                    }
                else:
                    download_url = (
                        await import_resolvers.resolve_page_url(source_url)
                        or source_url
                    )
                    staged, filename = await importer.download_to_staging(download_url)
                    suffix = Path(filename).suffix.lower()
                    if suffix == ".zip" or (
                        zipfile.is_zipfile(staged) and suffix != ".3mf"
                    ):
                        manifest, managed = await asyncio.to_thread(
                            _prepare_archive, item_id, staged, filename
                        )
                    else:
                        staged.unlink(missing_ok=True)
                        managed = None
                        manifest = {"kind": "direct", "title": safe_item(filename)}
        await asyncio.to_thread(_finish_resolve, item_id, manifest, managed)
    except Exception as exc:
        await asyncio.to_thread(_fail_item, item_id, exc, "resolve_failed")


async def _download_assets(url: str) -> list[tuple[Path, str]]:
    download_url = await import_resolvers.resolve_page_url(url) or url
    staged, name = await importer.download_to_staging(download_url)
    suffix = Path(name).suffix.lower()
    if suffix == ".zip" or (zipfile.is_zipfile(staged) and suffix != ".3mf"):
        entries = await asyncio.to_thread(importer.inspect_archive, staged)
        selected = [entry.name for entry in entries if entry.file_type]
        extracted = await asyncio.to_thread(importer.extract_selected, staged, selected)
        staged.unlink(missing_ok=True)
        return extracted
    return [(staged, name)]


async def _download_resolved_asset(resolved: ResolvedAsset) -> list[StagedAsset]:
    """Stage one V2 selection while retaining its identity through ZIP expansion."""
    staged, name = await importer.download_to_staging(resolved.download_url)
    suffix = Path(name).suffix.lower()
    if suffix == ".zip" or (zipfile.is_zipfile(staged) and suffix != ".3mf"):
        entries = await asyncio.to_thread(importer.inspect_archive, staged)
        extracted = await asyncio.to_thread(
            importer.extract_selected,
            staged,
            [entry.name for entry in entries if entry.file_type],
        )
        staged.unlink(missing_ok=True)
        return [
            StagedAsset(
                resolved=resolved,
                staged_path=entry_path,
                result_key=_zip_result_key(resolved.source_selection_id, entry_name),
                blob_sha256=sha256_file(entry_path),
                container_entry_path=entry_name,
            )
            for entry_path, entry_name in extracted
        ]
    return [
        StagedAsset(
            resolved=resolved,
            staged_path=staged,
            result_key="self",
            blob_sha256=sha256_file(staged),
        )
    ]


async def _stage_local_capture_assets(
    source: Path, manifest: CaptureManifestV2, wanted: list[str]
) -> list[StagedAsset]:
    """Turn browser-owned staging into V2 assets; never perform a server download."""
    files = {file.id: file for file in manifest.files}
    selected = [item for item in wanted if item in files] or list(files)

    def resolved(file_id: str) -> ResolvedAsset:
        file = files[file_id]
        return ResolvedAsset(
            manifest=manifest,
            source_selection_id=file.id,
            source_file_id=file.id,
            source_filename=file.name,
            # This descriptor is transport-free in this local path and is never fetched.
            download_url=manifest.source.canonical_url,
            source_item_id=manifest.source.source_item_id
            or manifest.source.canonical_url,
        )

    if source.suffix.lower() == ".zip":
        extracted = await asyncio.to_thread(importer.extract_selected, source, selected)
        assets: list[StagedAsset] = []
        for path, entry_name in extracted:
            if entry_name not in files:
                path.unlink(missing_ok=True)
                continue
            item = resolved(entry_name)
            assets.append(
                StagedAsset(
                    resolved=item,
                    staged_path=path,
                    result_key=_zip_result_key(item.source_selection_id, entry_name),
                    blob_sha256=sha256_file(path),
                    container_entry_path=entry_name,
                )
            )
        return assets

    # Browser staging stays owned by the inbox item until a successful import.
    item = resolved(selected[0])
    copy = settings.incoming_dir / f"browser-{uuid.uuid4().hex}{source.suffix}"
    with source.open("rb") as incoming:
        storage.stream_to_path(incoming, copy, max_bytes=settings.max_upload_bytes)
    return [
        StagedAsset(
            resolved=item,
            staged_path=copy,
            result_key="self",
            blob_sha256=sha256_file(copy),
        )
    ]


def _stage_capture_upload_slot_assets(
    manifest: CaptureManifestV2, wanted: list[str], slot_keys: dict[str, str]
) -> list[StagedAsset]:
    """Copy durable slot objects to disposable ingestion staging through StorageBackend."""
    files = {file.id: file for file in manifest.files}
    selected = [item for item in wanted if item in files] or list(files)
    backend = get_backend()
    output: list[StagedAsset] = []
    for file_id in selected:
        key = slot_keys.get(file_id)
        if key is None:
            raise importer.ImportError_("capture_upload_slots_incomplete")
        file = files[file_id]
        target = (
            settings.incoming_dir
            / f"capture-import-{uuid.uuid4().hex}{Path(file.name).suffix}"
        )
        with backend.local_path(key) as source, source.open("rb") as incoming:
            storage.stream_to_path(
                incoming, target, max_bytes=settings.max_upload_bytes
            )
        resolved = ResolvedAsset(
            manifest=manifest,
            source_selection_id=file.id,
            source_file_id=file.id,
            source_filename=file.name,
            download_url=manifest.source.canonical_url,
            source_item_id=manifest.source.source_item_id
            or manifest.source.canonical_url,
        )
        output.append(
            StagedAsset(
                resolved=resolved,
                staged_path=target,
                result_key="self",
                blob_sha256=sha256_file(target),
            )
        )
    return output


def _zip_result_key(source_selection_id: str, entry_name: str) -> str:
    normalized = entry_name.replace("\\", "/").lstrip("/")
    raw = f"{source_selection_id}\x00{normalized}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


async def run_import(
    item_id: int, selected_ids: list[str], session_factory: SessionFactory
) -> None:
    context = await asyncio.to_thread(
        _begin_import, item_id, selected_ids, session_factory
    )
    if context is None:
        return
    manifest = context["manifest"]
    selected = context["selected"]
    source_url = context["source_url"]
    staging_key = context["staging_key"]
    job_id = context["job_id"]

    try:
        assets: list[tuple[Path, str] | StagedAsset] = []
        v2_manifest: CaptureManifestV2 | None = None
        kind = manifest.get("kind")
        if manifest.get("schema_version") == 2 and context.get("slot_storage"):
            try:
                v2_manifest = CaptureManifestV2.from_dict(manifest)
            except ValueError as exc:
                raise importer.ImportError_("capture_manifest_invalid") from exc
            assets.extend(
                await asyncio.to_thread(
                    _stage_capture_upload_slot_assets,
                    v2_manifest,
                    selected,
                    context["slot_storage"],
                )
            )
        elif manifest.get("schema_version") == 2 and staging_key:
            try:
                v2_manifest = CaptureManifestV2.from_dict(manifest)
            except ValueError as exc:
                raise importer.ImportError_("capture_manifest_invalid") from exc
            staged = Path(staging_key)
            if not staged.exists():
                raise importer.ImportError_("staging_expired")
            assets.extend(
                await _stage_local_capture_assets(staged, v2_manifest, selected)
            )
        elif kind == "archive":
            entries = [entry.get("id") for entry in manifest.get("entries", [])]
            wanted = [item for item in selected if item in entries] or entries
            if not staging_key or not Path(staging_key).exists():
                raise importer.ImportError_("staging_expired")
            assets.extend(
                await asyncio.to_thread(
                    importer.extract_selected, Path(staging_key), wanted
                )
            )
        elif kind == "browser_file":
            filename = manifest.get("filename")
            if (
                not staging_key
                or not Path(staging_key).exists()
                or not isinstance(filename, str)
            ):
                raise importer.ImportError_("staging_expired")
            source = Path(staging_key)
            copy = (
                settings.incoming_dir
                / f"browser-{item_id}-{uuid.uuid4().hex}{source.suffix}"
            )
            with source.open("rb") as incoming:
                storage.stream_to_path(
                    incoming, copy, max_bytes=settings.max_upload_bytes
                )
            assets = [(copy, filename)]
        elif kind == "model_files":
            files_by_id = {item["id"]: item for item in manifest.get("files", [])}
            wanted = [item for item in selected if item in files_by_id] or list(
                files_by_id
            )
            chosen = [
                import_resolvers.ModelFile(
                    file_id=item_id_value,
                    name=files_by_id[item_id_value]["name"],
                    file_type=files_by_id[item_id_value]["file_type"],
                    size=files_by_id[item_id_value].get("size"),
                )
                for item_id_value in wanted
            ]
            if manifest.get("schema_version") == 2:
                try:
                    v2_manifest = CaptureManifestV2.from_dict(manifest)
                except ValueError as exc:
                    raise importer.ImportError_("capture_manifest_invalid") from exc
                resolved_assets = await import_resolvers.resolve_selected_assets(
                    source_url,
                    v2_manifest,
                    wanted,
                    import_resolvers.ProviderResolutionContext(
                        owner_user_id=context["owner_id"],
                        session_factory=session_factory,
                    ),
                )
                for resolved in resolved_assets:
                    assets.extend(await _download_resolved_asset(resolved))
            else:
                links = await import_resolvers.resolve_selected_download(
                    source_url, chosen
                )
                for link in links:
                    assets.extend(await _download_assets(link))
        elif kind == "collection":
            members = {item["id"]: item for item in manifest.get("members", [])}
            wanted = [item for item in selected if item in members] or list(members)
            for member_id in wanted:
                assets.extend(await _download_assets(members[member_id]["page_url"]))
        else:
            assets.extend(await _download_assets(source_url))
        await asyncio.to_thread(
            importer.import_assets,
            job_id=job_id,
            staged_files=assets,
            collection=context["collection_path"],
            tags=context["tags"],
            source_url=source_url,
            actor_user_id=context["owner_id"],
            session_factory=session_factory,
            inbox_item_id=item_id if v2_manifest is not None else None,
        )
        await asyncio.to_thread(_finish_import, item_id, job_id, session_factory)
    except Exception as exc:
        registry.update(job_id, state="failed", error=str(exc), retryable=True)
        await asyncio.to_thread(_fail_import, item_id, exc, session_factory)


def _begin_import(
    item_id: int, selected_ids: list[str], session_factory: SessionFactory
) -> dict[str, Any] | None:
    with session_factory.scoped_session() as session:
        row = session.get(InboxItem, item_id)
        if row is None or row.state != InboxItemState.REVIEW:
            return
        user = session.get(User, row.owner_user_id)
        if user is None:
            return
        _require_target(session, user, row.target_collection_id)
        collection = (
            session.get(Collection, row.target_collection_id)
            if row.target_collection_id
            else None
        )
        collection_path = collection.path if collection else None
        tags = ",".join(requested_tags(row.requested_tags_json)) or None
        manifest = _json_dict(row.manifest_json)
        selected = validate_import_selection(row, selected_ids)
        row.state = InboxItemState.IMPORTING
        row.error_code = None
        row.retryable = False
        row.updated_at = utcnow()
        job_id = registry.create(
            owner_user_id=row.owner_user_id, kind="pending_import", session=session
        )
        job_row = session.get(BackgroundJob, job_id)
        if job_row is not None:
            job_row.kind = "pending_import"
            job_row.replay_safe = True
            session.add(job_row)
        row.background_job_id = job_id
        if row.source_kind == InboxSourceKind.BROWSER:
            if row.id is None:
                raise RuntimeError("persisted inbox item has no id")
            try:
                has_slots = (
                    session.exec(
                        select(CaptureUploadSlot).where(
                            CaptureUploadSlot.inbox_item_id == row.id
                        )
                    ).first()
                    is not None
                )
                if has_slots:
                    staging_leases.transfer_capture_slots_to_job(
                        session, inbox_item_id=row.id, job_id=job_id
                    )
                else:
                    staging_leases.transfer_inbox_to_job(
                        session, inbox_item_id=row.id, job_id=job_id
                    )
            except staging_leases.StagingLeaseError:
                # Do not dispatch a browser import without durable ownership.
                session.rollback()
                row = session.get(InboxItem, item_id)
                if row is None:
                    return None
                row.state = InboxItemState.FAILED
                row.error_code = "staging_expired"
                row.retryable = False
                row.background_job_id = None
                session.add(row)
                session.commit()
                return None
        session.add(row)
        session.commit()
        return {
            "manifest": manifest,
            "selected": selected,
            "source_url": row.source_url,
            "staging_key": row.staging_key,
            "job_id": job_id,
            "collection_path": collection_path,
            "tags": tags,
            "owner_id": row.owner_user_id,
            "slot_storage": {
                slot.source_file_id: slot.storage_key
                for slot in session.exec(
                    select(CaptureUploadSlot).where(
                        CaptureUploadSlot.inbox_item_id == row.id
                    )
                ).all()
                if slot.role == "file" and slot.source_file_id and slot.storage_key
            },
        }


def _finish_import(item_id: int, job_id: str, session_factory: SessionFactory) -> None:
    job = registry.get(job_id)
    with session_factory.scoped_session() as session:
        row = session.get(InboxItem, item_id)
        if row is None:
            return
        cover_write: source_covers.SourceCoverWrite | None = None
        # The cover intent uses a separate engine-bound transaction. Keep this
        # terminalization session in ``no_autoflush`` until that intent commit
        # is complete; otherwise result upserts or Inbox state assignment can
        # open a SQLite write transaction and contend with the intent writer.
        with session.no_autoflush:
            results_durable, succeeded, failed = _record_v2_results(
                session, row, job.result if job is not None else None
            )
            if job and job.state == "completed" and job.model_id and results_durable:
                row.state = InboxItemState.COMPLETED
                row.resulting_model_id = job.model_id
                row.completed_at = utcnow()
                row.completion = (
                    InboxItemCompletion.PARTIAL
                    if failed
                    else InboxItemCompletion.COMPLETE
                )
                row.retryable = failed > 0
                if not row.retryable:
                    cover_result = _attach_capture_cover(session, row)
                    if cover_result is False:
                        row.error_code = "capture_cover_attach_pending"
                        row.retryable = True
                    elif isinstance(cover_result, source_covers.SourceCoverWrite):
                        cover_write = cover_result
                if not row.retryable and not _cleanup_capture_slots(session, row):
                    row.error_code = "capture_upload_cleanup_pending"
                    row.retryable = True
                if row.staging_key and not row.retryable:
                    unlink_managed_file(row.staging_key, settings.incoming_dir)
                    row.staging_key = None
            else:
                row.state = InboxItemState.FAILED
                row.error_code = (
                    job.error
                    if job and job.error
                    else "provenance_link_missing"
                    if not results_durable
                    else "import_failed"
                )
                row.retryable = True
            row.updated_at = utcnow()
            session.add(row)
        try:
            session.commit()
        except Exception:
            session.rollback()
            if cover_write is not None:
                source_covers.rollback_after_commit_failure(
                    session, get_backend(), cover_write
                )
            raise


def _record_v2_results(
    session: Session, row: InboxItem, result: object
) -> tuple[bool, int, int]:
    """Upsert durable per-file V2 results after each child artifact commits."""
    expects_v2_results = _json_dict(row.manifest_json).get("schema_version") == 2
    if row.id is None or not isinstance(result, dict):
        return (not expects_v2_results), 0, int(expects_v2_results)
    items = result.get("items")
    if not isinstance(items, list):
        return (not expects_v2_results), 0, int(expects_v2_results)
    has_v2_items = False
    all_links_durable = True
    succeeded = 0
    failed = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        selection = item.get("source_selection_id")
        result_key = item.get("result_key")
        name = item.get("name")
        if not all(
            isinstance(value, str) and value for value in (selection, result_key, name)
        ):
            continue
        assert isinstance(selection, str)
        assert isinstance(result_key, str)
        assert isinstance(name, str)
        has_v2_items = True
        existing = session.exec(
            select(InboxItemResult).where(
                InboxItemResult.inbox_item_id == row.id,
                InboxItemResult.source_selection_id == selection,
                InboxItemResult.result_key == result_key,
            )
        ).first()
        model_id = (
            item.get("model_id") if isinstance(item.get("model_id"), int) else None
        )
        file_id = item.get("file_id") if isinstance(item.get("file_id"), int) else None
        link = (
            session.exec(
                select(ArtifactProvenanceLink).where(
                    ArtifactProvenanceLink.file_id == file_id
                )
            ).first()
            if file_id is not None
            else None
        )
        values = {
            "original_filename": name,
            "state": (
                "deduplicated"
                if item.get("deduplicated") and link is not None
                else "imported"
                if model_id is not None and link is not None
                else "failed"
            ),
            "model_id": model_id,
            "file_id": file_id,
            "provenance_source_id": link.provenance_source_id
            if link is not None
            else None,
            "error_code": item.get("error")
            if isinstance(item.get("error"), str)
            else None,
            "retryable": model_id is None or link is None,
            "updated_at": utcnow(),
        }
        if existing is None:
            session.add(
                InboxItemResult(
                    inbox_item_id=row.id,
                    source_selection_id=selection,
                    result_key=result_key,
                    **values,
                )
            )
        else:
            for key, value in values.items():
                setattr(existing, key, value)
            session.add(existing)
        if model_id is not None:
            all_links_durable = all_links_durable and link is not None
        if model_id is not None and link is not None:
            succeeded += 1
        else:
            failed += 1
    if not has_v2_items:
        return (not expects_v2_results), succeeded, failed + int(expects_v2_results)
    return all_links_durable, succeeded, failed


def _fail_import(item_id: int, exc: Exception, session_factory: SessionFactory) -> None:
    with session_factory.scoped_session() as session:
        row = session.get(InboxItem, item_id)
        if row is not None:
            row.state = InboxItemState.FAILED
            row.error_code = safe_error(str(exc)) or "import_failed"
            row.retryable = True
            row.updated_at = utcnow()
            session.add(row)
            session.commit()


def retry(session: Session, row: InboxItem) -> InboxItem:
    if (
        row.state not in {InboxItemState.FAILED, InboxItemState.COMPLETED}
        or not row.retryable
    ):
        raise HTTPException(status_code=409, detail="pending_import_not_retryable")
    manifest = _json_dict(row.manifest_json)
    if (
        row.id is not None
        and getattr(row.state, "value", row.state) == InboxItemState.COMPLETED.value
    ):
        failed_selection_ids = [
            result.source_selection_id
            for result in session.exec(
                select(InboxItemResult).where(
                    InboxItemResult.inbox_item_id == row.id,
                )
            ).all()
            if result.model_id is None and result.retryable
        ]
        if failed_selection_ids:
            manifest["selected_ids"] = list(dict.fromkeys(failed_selection_ids))
            row.manifest_json = json.dumps(manifest, separators=(",", ":"))
    row.state = (
        InboxItemState.REVIEW
        if bool(manifest) and row.manifest_json.strip() not in {"", "{}"}
        else InboxItemState.CAPTURED
    )
    if (
        row.state == InboxItemState.REVIEW
        and row.source_kind == InboxSourceKind.BROWSER
    ):
        if row.id is None:
            raise RuntimeError("persisted inbox item has no id")
        try:
            if row.background_job_id is not None:
                has_slots = (
                    session.exec(
                        select(CaptureUploadSlot.id).where(
                            CaptureUploadSlot.inbox_item_id == row.id
                        )
                    ).first()
                    is not None
                )
                if has_slots:
                    staging_leases.return_capture_slots_to_review(
                        session, inbox_item_id=row.id, job_id=row.background_job_id
                    )
                else:
                    staging_leases.return_inbox_lease_to_review(
                        session, inbox_item_id=row.id, job_id=row.background_job_id
                    )
            else:
                staging_leases.renew_review_lease(session, inbox_item_id=row.id)
        except staging_leases.StagingLeaseError as exc:
            raise HTTPException(status_code=409, detail="staging_expired") from exc
    row.error_code = None
    row.retryable = False
    row.completion = None
    row.updated_at = utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def dismiss(session: Session, row: InboxItem) -> None:
    if row.state in {InboxItemState.RESOLVING, InboxItemState.IMPORTING}:
        raise HTTPException(status_code=409, detail="pending_import_busy")
    if row.source_kind == InboxSourceKind.BROWSER and row.id is not None:
        has_capture_slots = session.exec(
            select(CaptureUploadSlot.id).where(
                CaptureUploadSlot.inbox_item_id == row.id
            )
        ).first()
        if has_capture_slots is not None:
            released = _cleanup_capture_slots(session, row)
        elif row.state == InboxItemState.COMPLETED:
            released = _dismiss_completed_browser_staging(session, row)
        else:
            released = _dismiss_browser_lease(session, row)
        if not released:
            raise HTTPException(status_code=409, detail="staging_cleanup_failed")
        row.staging_key = None
        row.background_job_id = None
    elif row.staging_key:
        path = Path(row.staging_key)
        unlink_managed_file(path, settings.incoming_dir)
        try:
            path.parent.rmdir()
        except OSError:
            pass
        row.staging_key = None
    row.state = InboxItemState.DISMISSED
    row.updated_at = utcnow()
    session.add(row)
    session.commit()


def _dismiss_completed_browser_staging(session: Session, row: InboxItem) -> bool:
    """Dismiss terminal browser staging after import cleanup already ran.

    V2 capture completion removes its slot and lease rows before leaving the
    terminal inbox reference to the job for polling/history. In that case
    there is no lease left to return. Legacy browser uploads still retain a
    job-owned review lease and use the normal exact-identity cleanup path.
    """
    if row.background_job_id is None:
        return True
    has_job_lease = session.exec(
        select(StagingLease.id).where(
            StagingLease.background_job_id == row.background_job_id
        )
    ).first()
    if has_job_lease is None:
        return True
    return _dismiss_browser_lease(session, row)


def _dismiss_browser_lease(session: Session, row: InboxItem) -> bool:
    """Return a failed job lease to inbox ownership before exact dismissal."""
    assert row.id is not None
    try:
        if row.background_job_id is not None:
            staging_leases.return_inbox_lease_to_review(
                session, inbox_item_id=row.id, job_id=row.background_job_id
            )
        return staging_leases.dismiss_review_lease(session, inbox_item_id=row.id)
    except staging_leases.StagingLeaseNotFoundError as exc:
        # Expiry reconciliation can remove an already-missing staged file and
        # its lease before the user dismisses the terminal review row. With no
        # lease and no bytes left, there is no owned resource to clean; retain
        # the fail-closed behavior if a path still exists.
        if row.staging_key is None:
            return True
        try:
            Path(row.staging_key).lstat()
        except FileNotFoundError:
            return True
        except OSError:
            pass
        detail = (
            "staging_expired" if "expired" in str(exc) else "staging_cleanup_failed"
        )
        raise HTTPException(status_code=409, detail=detail) from exc
    except staging_leases.StagingLeaseError as exc:
        detail = (
            "staging_expired" if "expired" in str(exc) else "staging_cleanup_failed"
        )
        raise HTTPException(status_code=409, detail=detail) from exc


def reconcile_interrupted_items() -> int:
    completed_imports: list[tuple[int, str]] = []
    with get_session_factory().scoped_session() as session:
        # Capture slot publication is independent of inbox state. Recover all
        # durable slot intents before interrupted imports are retried/dismissed.
        # Startup historically invokes this function before composing the
        # storage adapter. Defer only the storage portion in that narrow
        # window; upload retry/dismiss and the post-bind recovery pass still
        # use the same exact backend reconciliation seam.
        try:
            backend = get_backend()
        except RuntimeError:
            backend = None
        if backend is not None:
            _reconcile_storage_publications(session, backend)
        rows = session.exec(
            select(InboxItem).where(
                col(InboxItem.state).in_(
                    (InboxItemState.RESOLVING, InboxItemState.IMPORTING)
                )
            )
        ).all()
        for row in rows:
            job = registry.get(row.background_job_id) if row.background_job_id else None
            if (
                row.state == InboxItemState.IMPORTING
                and job is not None
                and job.state == "completed"
                and job.model_id is not None
            ):
                if row.id is not None and row.background_job_id is not None:
                    completed_imports.append((row.id, row.background_job_id))
                continue
            row.state = InboxItemState.FAILED
            row.error_code = "import_interrupted"
            row.retryable = True
            row.updated_at = utcnow()
            session.add(row)
        session.commit()
    for item_id, job_id in completed_imports:
        # Preserve every durable terminalization step: per-file results,
        # optional source cover, capture receipt cleanup, and rollback rules.
        _finish_import(item_id, job_id, get_session_factory())
    _recover_completed_capture_cleanups()
    return len(rows)


def _recover_completed_capture_cleanups() -> int:
    """Finish only the durable cleanup for completed browser captures.

    A process can commit the imported Model and result rows before the final
    capture-slot cleanup transaction succeeds.  Those items must not be sent
    through ingestion again: the imported result is already authoritative.
    Each item gets its own transaction so an unproven cleanup rolls back all
    lease/intent mutations and leaves the warning visible for a later retry.
    """
    with get_session_factory().scoped_session() as session:
        item_ids = session.exec(
            select(InboxItem.id).where(
                InboxItem.state == InboxItemState.COMPLETED,
                InboxItem.retryable.is_(True),
                InboxItem.error_code == "capture_upload_cleanup_pending",
            )
        ).all()

    recovered = 0
    for item_id in item_ids:
        if item_id is None:
            continue
        with get_session_factory().scoped_session() as session:
            row = session.get(InboxItem, item_id)
            if (
                row is None
                or row.state != InboxItemState.COMPLETED
                or not row.retryable
                or row.error_code != "capture_upload_cleanup_pending"
            ):
                continue
            if not _cleanup_capture_slots(session, row):
                session.rollback()
                continue
            row.error_code = None
            row.retryable = False
            row.updated_at = utcnow()
            session.add(row)
            try:
                session.commit()
            except Exception:
                session.rollback()
                continue
            recovered += 1
    return recovered


def _reconcile_storage_publications(session: Session, backend: StorageBackend) -> int:
    """Reconcile storage intents in one caller-owned DB transaction."""
    recovered = staging_leases.reconcile_capture_slots(session, backend)
    # Publication recovery handles objects that reached the backend before a
    # process died. Any remaining lease-owned deterministic spool is a request
    # partial and can be removed by inode proof; unknown directory contents are
    # never discovered or deleted.
    staging_leases.reconcile_capture_staging(session)
    recovered += source_covers.reconcile_pending(session, backend)
    session.commit()
    return recovered


def reconcile_storage_publications() -> int:
    """Run receipt recovery after the storage backend has been composed.

    Application startup historically invokes inbox recovery before binding the
    backend. The composition root can call this explicit pass immediately
    after binding, while request retry/dismiss paths remain self-healing.
    """
    backend = get_backend()
    with get_session_factory().scoped_session() as session:
        return _reconcile_storage_publications(session, backend)
