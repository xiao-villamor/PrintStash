"""Fail-closed garbage collection planning and authorization.

Discovery only creates an immutable preview.  A different transition binds
that preview to an exact recent backup on an independent provider.  Catalog
rows and owned storage receipts are untouched until the quarantine deadline
has elapsed and every piece of evidence is revalidated.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.config import settings
from app.core.time import utcnow
from app.db.models import (
    Collection,
    Document,
    File,
    GcItem,
    GcRun,
    GcRunState,
    Model,
    ModelProvenanceSource,
    ModelSourceCover,
    RestoreMarker,
)
from app.db.scopes import trashed
from app.db.session import get_session_factory
from app.services.storage_backend import StorageTier, get_backend
from app.services.storage_identity import independent_evidence
from app.services.storage_ownership import provider_ref_for_backend

_DOCUMENT_IMAGE_RE = re.compile(
    r"/api/v1/documents/(\d+)/images/([0-9a-f]{64}\.(?:png|jpe?g|gif|webp))"
)
_COLLECTION_IMAGE_RE = re.compile(
    r"/api/v1/collections/(\d+)/images/([0-9a-f]{64}\.(?:png|jpe?g|gif|webp))"
)
_MAX_RESOURCES = 25
_MAX_KEYS = 100
_MAX_BYTES = 1024 * 1024 * 1024
_BACKUP_MAX_AGE = timedelta(hours=24)


class GcSafetyError(RuntimeError):
    """A destructive transition lacks one of its required proofs."""


@dataclass(frozen=True)
class BackupWitness:
    backup_id: str
    source_ref: str
    provider_ref: str
    archive_sha256: str
    verified_at: datetime
    active_identity_evidence: dict | None = None
    backup_identity_evidence: dict | None = None


@dataclass(frozen=True)
class _Candidate:
    kind: str
    resource_id: int
    deleted_at: datetime
    key_count: int
    size_bytes: int


def _restore_generation(session: Session) -> str:
    rows = session.exec(
        select(RestoreMarker).order_by(RestoreMarker.id.asc())  # type: ignore[attr-defined]
    ).all()
    payload = [
        (
            row.id,
            row.operation_nonce,
            row.archive_sha256,
            row.state,
            row.updated_at.isoformat(),
        )
        for row in rows
    ]
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _stable_iso(value: datetime) -> str:
    """Normalize timestamps across SQLite's timezone-naive round trip."""
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat()


def _active_provider_ref() -> str:
    backend = get_backend()
    return provider_ref_for_backend(backend)


def _file_metrics(file_row: File) -> tuple[int, int]:
    if file_row.is_external:
        return 0, 0
    # Primary, current + legacy thumbnail, and the shared STL conversion cache.
    # This is an upper bound: limits must fail safe even when an optional key is
    # absent or shared with another revision.
    return 4, max(0, int(file_row.size_bytes))


def _model_candidate(session: Session, row: Model) -> _Candidate:
    files = session.exec(select(File).where(File.model_id == row.id)).all()
    key_count = 0
    size_bytes = 0
    for file_row in files:
        file_keys, file_bytes = _file_metrics(file_row)
        key_count += file_keys
        size_bytes += file_bytes
    cover_count = session.exec(
        select(func.count(ModelSourceCover.id))
        .select_from(ModelSourceCover)
        .join(ModelProvenanceSource)
        .where(ModelProvenanceSource.model_id == row.id)
    ).one()
    return _Candidate(
        kind="model",
        resource_id=int(row.id),
        deleted_at=row.deleted_at,  # type: ignore[arg-type]
        key_count=key_count + int(cover_count),
        size_bytes=size_bytes,
    )


def _document_candidate(row: Document) -> _Candidate:
    images = {
        name
        for document_id, name in _DOCUMENT_IMAGE_RE.findall(row.body or "")
        if int(document_id) == row.id
    }
    return _Candidate(
        kind="document",
        resource_id=int(row.id),
        deleted_at=row.deleted_at,  # type: ignore[arg-type]
        key_count=int(bool(row.filename)) + len(images),
        size_bytes=0,
    )


def _collection_candidate(row: Collection) -> _Candidate:
    images = {
        name
        for collection_id, name in _COLLECTION_IMAGE_RE.findall(row.readme or "")
        if int(collection_id) == row.id
    }
    return _Candidate(
        kind="collection",
        resource_id=int(row.id),
        deleted_at=row.deleted_at,  # type: ignore[arg-type]
        key_count=len(images),
        size_bytes=0,
    )


def _candidate_pool(session: Session, cutoff: datetime) -> list[_Candidate]:
    models = session.exec(
        select(Model).where(
            trashed(Model),
            Model.deleted_at <= cutoff,  # type: ignore[operator]
        )
    ).all()
    model_ids = {int(row.id) for row in models if row.id is not None}
    documents = session.exec(
        select(Document).where(
            trashed(Document),
            Document.deleted_at <= cutoff,  # type: ignore[operator]
        )
    ).all()
    files = session.exec(
        select(File).where(
            trashed(File),
            File.deleted_at <= cutoff,  # type: ignore[operator]
        )
    ).all()
    collections = session.exec(
        select(Collection).where(
            trashed(Collection),
            Collection.deleted_at <= cutoff,  # type: ignore[operator]
        )
    ).all()
    candidates = [_model_candidate(session, row) for row in models]
    candidates.extend(_document_candidate(row) for row in documents)
    candidates.extend(
        _Candidate(
            kind="file",
            resource_id=int(row.id),
            deleted_at=row.deleted_at,  # type: ignore[arg-type]
            key_count=_file_metrics(row)[0],
            size_bytes=_file_metrics(row)[1],
        )
        for row in files
        if row.model_id not in model_ids
    )
    candidates.extend(_collection_candidate(row) for row in collections)
    return sorted(
        candidates, key=lambda item: (item.deleted_at, item.kind, item.resource_id)
    )


def _resource_limit(session: Session) -> int:
    model_count = int(session.exec(select(func.count(Model.id))).one())
    one_percent = max(1, model_count // 100)
    return min(_MAX_RESOURCES, one_percent)


def _select_bounded(session: Session, pool: list[_Candidate]) -> list[_Candidate]:
    selected: list[_Candidate] = []
    keys = 0
    size = 0
    resource_limit = _resource_limit(session)
    for candidate in pool:
        if len(selected) >= resource_limit:
            break
        if keys + candidate.key_count > _MAX_KEYS:
            break
        if size + candidate.size_bytes > _MAX_BYTES:
            break
        selected.append(candidate)
        keys += candidate.key_count
        size += candidate.size_bytes
    return selected


def _digest_payload(run: GcRun, items: list[GcItem]) -> bytes:
    payload = {
        "cutoff_at": _stable_iso(run.cutoff_at),
        "retention_days": run.retention_days,
        "active_provider_ref": run.active_provider_ref,
        "restore_generation": run.restore_generation,
        "items": [
            {
                "kind": item.resource_kind,
                "id": item.resource_id,
                "deleted_at": _stable_iso(item.deleted_at_snapshot),
                "keys": item.key_count,
                "bytes": item.size_bytes,
            }
            for item in sorted(
                items, key=lambda value: (value.resource_kind, value.resource_id)
            )
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def create_plan(
    session: Session,
    *,
    retention_days: int,
    requested_by: int | None,
    scheduled: bool = False,
) -> GcRun:
    """Persist a bounded preview without claiming or deleting anything."""
    if retention_days < 0:
        raise GcSafetyError("gc_retention_disabled")
    active = session.exec(select(GcRun.id).where(GcRun.active_slot == 1)).first()
    if active is not None:
        raise GcSafetyError("gc_plan_active")
    cutoff = utcnow() - timedelta(days=retention_days)
    pool = _candidate_pool(session, cutoff)
    selected = _select_bounded(session, pool)
    run = GcRun(
        digest="0" * 64,
        retention_days=retention_days,
        cutoff_at=cutoff,
        resource_count=len(selected),
        candidate_pool_count=len(pool),
        key_count=sum(item.key_count for item in selected),
        size_bytes=sum(item.size_bytes for item in selected),
        scheduled=scheduled,
        requested_by=requested_by,
        active_provider_ref=_active_provider_ref(),
        restore_generation=_restore_generation(session),
    )
    try:
        session.add(run)
        session.flush()
        items = [
            GcItem(
                run_id=int(run.id),
                resource_kind=item.kind,
                resource_id=item.resource_id,
                deleted_at_snapshot=item.deleted_at,
                key_count=item.key_count,
                size_bytes=item.size_bytes,
            )
            for item in selected
        ]
        session.add_all(items)
        session.flush()
        run.digest = hashlib.sha256(_digest_payload(run, items)).hexdigest()
        run.updated_at = utcnow()
        session.add(run)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise GcSafetyError("gc_plan_active") from exc
    session.refresh(run)
    return run


def _parse_created_at(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def find_backup_witness() -> BackupWitness | None:
    """Return the newest exact backup proof on an independent failure domain."""
    from app.services import backup

    active = _active_provider_ref()
    now = utcnow()
    for meta in backup.list_backup_sources():
        created_at = _parse_created_at(meta.created_at)
        if (
            created_at is None
            or now - created_at > _BACKUP_MAX_AGE
            or meta.location != "s3"
            or not meta.source_ref
            or not meta.provider_ref
            or meta.provider_ref == active
            or not meta.archive_sha256
        ):
            continue
        evidence = _source_identity_evidence(meta)
        if evidence is None:
            continue
        verification = backup.verify_backup(meta.id, source_ref=meta.source_ref)
        if not verification.valid or not verification.app_compatible:
            continue
        if _source_identity_evidence(meta) != evidence:
            continue
        return BackupWitness(
            backup_id=meta.id,
            source_ref=meta.source_ref,
            provider_ref=meta.provider_ref,
            archive_sha256=meta.archive_sha256,
            verified_at=now,
            active_identity_evidence=evidence[0],
            backup_identity_evidence=evidence[1],
        )
    return None


def _source_identity_evidence(meta) -> tuple[dict, dict] | None:
    from app.services import backup

    target = backup._get_backup_s3_target()
    if (
        target is None
        or meta.location != "s3"
        or target.provider_ref != meta.provider_ref
    ):
        return None
    return independent_evidence(get_backend().storage_target, target.storage_target)


def _serialize_evidence(evidence: dict) -> str:
    return json.dumps(evidence, sort_keys=True, separators=(",", ":"))


def _items(session: Session, run_id: int) -> list[GcItem]:
    return session.exec(
        select(GcItem).where(GcItem.run_id == run_id).order_by(GcItem.id.asc())  # type: ignore[attr-defined]
    ).all()


def _resource(session: Session, item: GcItem):
    resource_type = {
        "model": Model,
        "document": Document,
        "file": File,
        "collection": Collection,
    }.get(item.resource_kind)
    return session.get(resource_type, item.resource_id) if resource_type else None


def _revalidate_plan(session: Session, run: GcRun) -> list[GcItem]:
    items = _items(session, int(run.id))
    if hashlib.sha256(_digest_payload(run, items)).hexdigest() != run.digest:
        raise GcSafetyError("gc_digest_changed")
    if _restore_generation(session) != run.restore_generation:
        raise GcSafetyError("gc_restore_generation_changed")
    if _active_provider_ref() != run.active_provider_ref:
        raise GcSafetyError("gc_provider_changed")
    for item in items:
        resource = _resource(session, item)
        if (
            resource is None
            or resource.deleted_at is None
            or resource.deleted_at != item.deleted_at_snapshot
            or resource.purge_token is not None
        ):
            raise GcSafetyError("gc_candidate_changed")
    return items


def approve_plan(session: Session, run_id: int, digest: str, actor_id: int) -> GcRun:
    run = session.get(GcRun, run_id)
    if run is None:
        raise GcSafetyError("gc_plan_not_found")
    if run.state != GcRunState.PREVIEW:
        raise GcSafetyError("gc_plan_not_preview")
    if not hmac.compare_digest(digest, run.digest):
        raise GcSafetyError("gc_digest_mismatch")
    _revalidate_plan(session, run)
    if get_backend().capabilities.tier is not StorageTier.VERIFIED:
        raise GcSafetyError("gc_verified_storage_required")
    witness = find_backup_witness()
    if (
        witness is None
        or witness.active_identity_evidence is None
        or witness.backup_identity_evidence is None
    ):
        raise GcSafetyError("gc_backup_required")
    # Verification may take long enough for another request to restore a
    # candidate. Re-read committed rows instead of the session identity map.
    session.expire_all()
    _revalidate_plan(session, run)
    if get_backend().capabilities.tier is not StorageTier.VERIFIED:
        raise GcSafetyError("gc_verified_storage_required")
    now = utcnow()
    run.state = GcRunState.QUARANTINED
    run.approved_by = actor_id
    run.approved_at = now
    run.quarantine_until = now + timedelta(days=int(settings.gc_quarantine_days))
    run.backup_id = witness.backup_id
    run.backup_source_ref = witness.source_ref
    run.backup_provider_ref = witness.provider_ref
    run.backup_archive_sha256 = witness.archive_sha256
    run.backup_verified_at = witness.verified_at
    run.active_identity_evidence = _serialize_evidence(witness.active_identity_evidence)
    run.backup_identity_evidence = _serialize_evidence(witness.backup_identity_evidence)
    run.updated_at = now
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _reverify_backup(run: GcRun) -> None:
    from app.services import backup

    if (
        not run.backup_id
        or not run.backup_source_ref
        or not run.backup_provider_ref
        or not run.backup_archive_sha256
        or not run.active_identity_evidence
        or not run.backup_identity_evidence
        or run.backup_provider_ref == _active_provider_ref()
    ):
        raise GcSafetyError("gc_backup_witness_invalid")
    sources = backup.list_backup_sources()
    source = next(
        (
            item
            for item in sources
            if item.id == run.backup_id
            and item.source_ref == run.backup_source_ref
            and item.provider_ref == run.backup_provider_ref
            and item.archive_sha256 == run.backup_archive_sha256
            and item.location == "s3"
        ),
        None,
    )
    if source is None:
        raise GcSafetyError("gc_backup_witness_missing")
    _require_unchanged_identity(run, source)
    verification = backup.verify_backup(run.backup_id, source_ref=run.backup_source_ref)
    if not verification.valid or not verification.app_compatible:
        raise GcSafetyError("gc_backup_witness_invalid")
    _require_unchanged_identity(run, source)


def _require_unchanged_identity(run: GcRun, source) -> None:
    evidence = _source_identity_evidence(source)
    if evidence is None or (
        _serialize_evidence(evidence[0]) != run.active_identity_evidence
        or _serialize_evidence(evidence[1]) != run.backup_identity_evidence
    ):
        raise GcSafetyError("gc_identity_evidence_changed")


def finalize_plan(session: Session, run_id: int) -> GcRun:
    """Finalize one quarantined plan after all evidence is revalidated."""
    from app.services.storage_deletion import process_storage_delete_intents
    from app.services.trash import (
        hard_delete_collection,
        hard_delete_document,
        hard_delete_file,
        hard_delete_model,
    )

    run = session.get(GcRun, run_id)
    if run is None:
        raise GcSafetyError("gc_plan_not_found")
    if run.state != GcRunState.QUARANTINED:
        raise GcSafetyError("gc_plan_not_quarantined")
    if run.quarantine_until is None or _stable_iso(utcnow()) < _stable_iso(
        run.quarantine_until
    ):
        raise GcSafetyError("gc_quarantine_active")
    try:
        items = _revalidate_plan(session, run)
        if get_backend().capabilities.tier is not StorageTier.VERIFIED:
            raise GcSafetyError("gc_verified_storage_required")
        _reverify_backup(run)
        session.expire_all()
        items = _revalidate_plan(session, run)
        if get_backend().capabilities.tier is not StorageTier.VERIFIED:
            raise GcSafetyError("gc_verified_storage_required")
        run.state = GcRunState.FINALIZING
        run.updated_at = utcnow()
        session.add(run)
        session.commit()
        for item in items:
            resource = _resource(session, item)
            if isinstance(resource, Model):
                hard_delete_model(session, resource)
            elif isinstance(resource, Document):
                hard_delete_document(session, resource)
            elif isinstance(resource, File):
                hard_delete_file(session, resource)
            elif isinstance(resource, Collection):
                hard_delete_collection(session, resource)
            else:
                raise GcSafetyError("gc_candidate_changed")
        session.commit()
    except Exception as exc:
        session.rollback()
        run = session.get(GcRun, run_id)
        if run is not None:
            run.state = GcRunState.BLOCKED
            run.active_slot = None
            run.last_error = str(exc)[:255]
            run.updated_at = utcnow()
            session.add(run)
            session.commit()
        if isinstance(exc, GcSafetyError):
            raise
        raise GcSafetyError("gc_finalization_failed") from exc

    result = process_storage_delete_intents(limit=_MAX_KEYS)
    session.expire_all()
    run = session.get(GcRun, run_id)
    if run is None:
        raise GcSafetyError("gc_plan_not_found")
    if result.blocked:
        run.state = GcRunState.BLOCKED
        run.active_slot = None
        run.last_error = "gc_storage_delete_blocked"
    elif result.pending:
        run.state = GcRunState.FINALIZING
        run.last_error = "gc_storage_delete_pending"
    else:
        run.state = GcRunState.COMPLETED
        run.active_slot = None
        run.completed_at = utcnow()
        run.last_error = None
    run.updated_at = utcnow()
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def abort_plan(session: Session, run_id: int) -> GcRun:
    run = session.get(GcRun, run_id)
    if run is None:
        raise GcSafetyError("gc_plan_not_found")
    if run.state not in {GcRunState.PREVIEW, GcRunState.QUARANTINED}:
        raise GcSafetyError("gc_plan_not_abortable")
    run.state = GcRunState.ABORTED
    run.active_slot = None
    run.updated_at = utcnow()
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _resume_storage_finalization(session: Session, run: GcRun) -> GcRun:
    """Retry the durable storage outbox after catalog deletion committed."""
    from app.services.storage_deletion import process_storage_delete_intents

    result = process_storage_delete_intents(limit=_MAX_KEYS)
    if result.blocked:
        run.state = GcRunState.BLOCKED
        run.active_slot = None
        run.last_error = "gc_storage_delete_blocked"
    elif result.pending:
        run.state = GcRunState.FINALIZING
        run.last_error = "gc_storage_delete_pending"
    else:
        run.state = GcRunState.COMPLETED
        run.active_slot = None
        run.completed_at = utcnow()
        run.last_error = None
    run.updated_at = utcnow()
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def run_scheduled_gc(retention_days: int | None = None) -> dict[str, int]:
    """Hourly coordinator: preview, wait, and finalize; never approve itself."""
    from app.services.backup import restore_in_progress

    if restore_in_progress():
        return {"rows": 0, "orphan_blobs": 0, "gc_candidates": 0}
    effective_retention = (
        int(settings.trash_retention_days) if retention_days is None else retention_days
    )
    if effective_retention < 0:
        return {"rows": 0, "orphan_blobs": 0, "gc_candidates": 0}
    with get_session_factory().scoped_session() as session:
        active = session.exec(
            select(GcRun)
            .where(
                GcRun.state.in_(  # type: ignore[attr-defined]
                    [
                        GcRunState.PREVIEW,
                        GcRunState.QUARANTINED,
                        GcRunState.FINALIZING,
                    ]
                )
            )
            .order_by(GcRun.id.asc())  # type: ignore[attr-defined]
        ).first()
        if active is not None:
            if active.state == GcRunState.FINALIZING:
                resumed = _resume_storage_finalization(session, active)
                return {
                    "rows": (
                        resumed.resource_count
                        if resumed.state == GcRunState.COMPLETED
                        else 0
                    ),
                    "orphan_blobs": 0,
                    "gc_candidates": resumed.resource_count,
                    "gc_plan_id": int(resumed.id),
                }
            if (
                active.state == GcRunState.QUARANTINED
                and active.quarantine_until is not None
                and _stable_iso(utcnow()) >= _stable_iso(active.quarantine_until)
            ):
                try:
                    finalized = finalize_plan(session, int(active.id))
                except GcSafetyError:
                    return {
                        "rows": 0,
                        "orphan_blobs": 0,
                        "gc_candidates": active.resource_count,
                        "gc_plan_id": int(active.id),
                    }
                return {
                    "rows": (
                        finalized.resource_count
                        if finalized.state == GcRunState.COMPLETED
                        else 0
                    ),
                    "orphan_blobs": 0,
                    "gc_candidates": finalized.resource_count,
                    "gc_plan_id": int(finalized.id),
                }
            return {
                "rows": 0,
                "orphan_blobs": 0,
                "gc_candidates": active.resource_count,
                "gc_plan_id": int(active.id),
            }
        run = create_plan(
            session,
            retention_days=effective_retention,
            requested_by=None,
            scheduled=True,
        )
        if run.resource_count == 0:
            run.state = GcRunState.COMPLETED
            run.active_slot = None
            run.completed_at = utcnow()
            run.updated_at = utcnow()
            session.add(run)
            session.commit()
        return {
            "rows": 0,
            "orphan_blobs": 0,
            "gc_candidates": run.resource_count,
            "gc_plan_id": int(run.id),
        }
