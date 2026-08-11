from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path

from sqlmodel import Session, select

from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import (
    BackgroundJob,
    ExternalLibrary,
    File,
    FileType,
    InboxItem,
    InboxItemState,
    Metadata,
    Model,
    VaultAuditFinding,
    VaultAuditFindingState,
    VaultAuditMode,
    VaultAuditRun,
    VaultAuditRunState,
    VaultAuditSeverity,
)
from app.db.scopes import live
from app.db.session import get_session_factory
from app.schemas.maintenance import VaultAuditFindingRead, VaultAuditRunRead
from app.services import audit, thumbnail_repair
from app.services.storage_backend import get_backend
from app.services.storage_utils import OwnedBlob, ownership_snapshot

_ACTIVE_STATES = (VaultAuditRunState.PENDING, VaultAuditRunState.RUNNING)
logger = get_logger(__name__)


def _safe_name(blob: OwnedBlob) -> str:
    return (blob.display_name or Path(blob.key.replace("\\", "/")).name)[:255]


def _details(row: VaultAuditFinding) -> dict:
    try:
        value = json.loads(row.details_json or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def finding_read(row: VaultAuditFinding) -> VaultAuditFindingRead:
    return VaultAuditFindingRead(
        id=row.id,
        run_id=row.run_id,
        code=row.code,
        severity=row.severity,
        resource_type=row.resource_type,
        resource_identifier=row.resource_identifier,
        repair_action=row.repair_action,
        state=row.state,
        details=_details(row),
        created_at=row.created_at,
        resolved_at=row.resolved_at,
        resolved_by=row.resolved_by,
    )


def read_run(session: Session, row: VaultAuditRun, *, findings: bool = True) -> VaultAuditRunRead:
    finding_rows = []
    if findings and row.id is not None:
        finding_rows = list(
            session.exec(
                select(VaultAuditFinding)
                .where(VaultAuditFinding.run_id == row.id)
                .order_by(VaultAuditFinding.severity.desc(), VaultAuditFinding.id.asc())  # type: ignore[attr-defined]
            ).all()
        )
    return VaultAuditRunRead(
        **row.model_dump(),
        findings=[finding_read(finding) for finding in finding_rows],
    )


def create_run(session: Session, requested_by: int, mode: VaultAuditMode) -> tuple[VaultAuditRun, bool]:
    active = session.exec(
        select(VaultAuditRun)
        .where(VaultAuditRun.state.in_(_ACTIVE_STATES))  # type: ignore[attr-defined]
        .order_by(VaultAuditRun.created_at.desc())  # type: ignore[attr-defined]
    ).first()
    if active is not None:
        return active, False
    row = VaultAuditRun(requested_by=requested_by, mode=mode)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row, True


def list_runs(session: Session, limit: int = 25) -> list[VaultAuditRunRead]:
    rows = session.exec(
        select(VaultAuditRun)
        .order_by(VaultAuditRun.created_at.desc())  # type: ignore[attr-defined]
        .limit(limit)
    ).all()
    return [read_run(session, row, findings=False) for row in rows]


def latest_run(session: Session) -> VaultAuditRun | None:
    return session.exec(
        select(VaultAuditRun).order_by(VaultAuditRun.created_at.desc())  # type: ignore[attr-defined]
    ).first()


def request_cancel(session: Session, run_id: int) -> VaultAuditRun | None:
    row = session.get(VaultAuditRun, run_id)
    if row is None:
        return None
    if row.state in _ACTIVE_STATES:
        row.cancel_requested = True
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


def reconcile_interrupted_runs() -> int:
    with get_session_factory().scoped_session() as session:
        rows = session.exec(
            select(VaultAuditRun).where(VaultAuditRun.state == VaultAuditRunState.RUNNING)
        ).all()
        for row in rows:
            row.state = VaultAuditRunState.FAILED
            row.error_code = "audit_interrupted"
            row.finished_at = utcnow()
            session.add(row)
        session.commit()
        return len(rows)


def _add(
    session: Session,
    run: VaultAuditRun,
    *,
    code: str,
    severity: VaultAuditSeverity,
    resource_type: str,
    identifier: str,
    details: dict | None = None,
    repair_action: str | None = None,
) -> None:
    session.add(
        VaultAuditFinding(
            run_id=run.id,
            code=code,
            severity=severity,
            resource_type=resource_type,
            resource_identifier=identifier[:255],
            details_json=json.dumps(details or {}, separators=(",", ":")),
            repair_action=repair_action,
        )
    )
    if severity == VaultAuditSeverity.CRITICAL:
        run.critical_count += 1
    elif severity == VaultAuditSeverity.WARNING:
        run.warning_count += 1
    else:
        run.info_count += 1


def _cancelled(session: Session, run: VaultAuditRun) -> bool:
    session.refresh(run)
    if not run.cancel_requested:
        return False
    run.state = VaultAuditRunState.CANCELLED
    run.finished_at = utcnow()
    run.current_phase = "cancelled"
    session.add(run)
    session.commit()
    return True


def _hash_blob(key: str) -> str:
    digest = hashlib.sha256()
    for chunk in get_backend().stream_chunks(key):
        digest.update(chunk)
    return digest.hexdigest()


def _check_primary(session: Session, run: VaultAuditRun, blobs: list[OwnedBlob]) -> bool:
    backend = get_backend()
    total = max(len(blobs), 1)
    for index, blob in enumerate(blobs):
        if _cancelled(session, run):
            return False
        name = _safe_name(blob)
        details = {"resource_id": blob.resource_id, "name": name}
        try:
            if not backend.exists(blob.key):
                _add(
                    session,
                    run,
                    code="owned_blob_missing",
                    severity=VaultAuditSeverity.CRITICAL,
                    resource_type=blob.resource_type,
                    identifier=name,
                    details=details,
                )
                continue
            size = backend.stat_size(blob.key)
            if blob.expected_size is not None and size != blob.expected_size:
                _add(
                    session,
                    run,
                    code="owned_blob_size_mismatch",
                    severity=VaultAuditSeverity.CRITICAL,
                    resource_type=blob.resource_type,
                    identifier=name,
                    details={**details, "expected_size": blob.expected_size, "actual_size": size},
                )
            if run.mode == VaultAuditMode.FULL and blob.expected_sha256:
                actual = _hash_blob(blob.key)
                if actual != blob.expected_sha256.lower():
                    _add(
                        session,
                        run,
                        code="owned_blob_hash_mismatch",
                        severity=VaultAuditSeverity.CRITICAL,
                        resource_type=blob.resource_type,
                        identifier=name,
                        details=details,
                    )
        except Exception:
            _add(
                session,
                run,
                code="owned_blob_unreadable",
                severity=VaultAuditSeverity.CRITICAL,
                resource_type=blob.resource_type,
                identifier=name,
                details=details,
            )
        run.progress = 5 + ((index + 1) / total * 45)
        if index % 25 == 0:
            session.add(run)
            session.commit()
    return True


def _check_database(session: Session, run: VaultAuditRun) -> None:
    backend = get_backend()
    run.current_phase = "database"
    models = session.exec(select(Model).where(live(Model))).all()
    for model in models:
        if _cancelled(session, run):
            return
        files = session.exec(select(File).where(File.model_id == model.id, live(File))).all()
        if not files:
            _add(
                session,
                run,
                code="model_without_live_artifact",
                severity=VaultAuditSeverity.CRITICAL,
                resource_type="model",
                identifier=model.name,
                details={"model_id": model.id},
            )
            continue
        gcode = [item for item in files if item.file_type == FileType.GCODE]
        recommended = [item for item in gcode if item.is_recommended]
        if gcode and not recommended:
            _add(
                session,
                run,
                code="recommended_revision_missing",
                severity=VaultAuditSeverity.WARNING,
                resource_type="model",
                identifier=model.name,
                details={"model_id": model.id},
                repair_action="restore_recommended_revision",
            )
        elif len(recommended) > 1:
            _add(
                session,
                run,
                code="recommended_revision_duplicate",
                severity=VaultAuditSeverity.WARNING,
                resource_type="model",
                identifier=model.name,
                details={"model_id": model.id},
                repair_action="restore_recommended_revision",
            )
        metadata_ids = set(
            session.exec(
                select(Metadata.file_id).where(Metadata.file_id.in_([item.id for item in files]))  # type: ignore[union-attr]
            ).all()
        )
        for item in files:
            if item.id not in metadata_ids:
                _add(
                    session,
                    run,
                    code="metadata_missing",
                    severity=VaultAuditSeverity.WARNING,
                    resource_type="file",
                    identifier=item.original_filename,
                    details={"file_id": item.id, "model_id": model.id},
                    repair_action="reparse_metadata",
                )
        if model.thumbnail_file_id:
            current = backend.thumbnail_key(model.thumbnail_file_id)
            legacy = backend.legacy_thumbnail_key(model.thumbnail_file_id)
            try:
                key = current if backend.exists(current) else legacy
                present = backend.exists(key)
            except Exception:
                present = False
            if not present:
                _add(
                    session,
                    run,
                    code="thumbnail_missing",
                    severity=VaultAuditSeverity.WARNING,
                    resource_type="model",
                    identifier=model.name,
                    details={"model_id": model.id, "file_id": model.thumbnail_file_id},
                    repair_action="regenerate_thumbnail",
                )
            else:
                try:
                    from PIL import Image

                    with backend.local_path(key) as path, Image.open(path) as image:
                        image.verify()
                except Exception:
                    _add(
                        session,
                        run,
                        code="thumbnail_unreadable",
                        severity=VaultAuditSeverity.WARNING,
                        resource_type="model",
                        identifier=model.name,
                        details={"model_id": model.id, "file_id": model.thumbnail_file_id},
                        repair_action="regenerate_thumbnail",
                    )


def _check_external(session: Session, run: VaultAuditRun, blobs: list[OwnedBlob]) -> None:
    for library in session.exec(select(ExternalLibrary)).all():
        root = Path(library.root_path)
        if not root.exists() or not root.is_dir():
            _add(
                session,
                run,
                code="external_root_unavailable",
                severity=VaultAuditSeverity.CRITICAL,
                resource_type="external_library",
                identifier=library.name,
                details={"library_id": library.id, "root_label": library.name},
            )
    for blob in blobs:
        path = Path(blob.key)
        file_row = session.get(File, blob.resource_id)
        try:
            available = path.is_file() and path.stat().st_size >= 0
        except OSError:
            available = False
        if not available:
            _add(
                session,
                run,
                code="linked_file_missing",
                severity=VaultAuditSeverity.WARNING,
                resource_type="file",
                identifier=_safe_name(blob),
                details={
                    "file_id": blob.resource_id,
                    "name": _safe_name(blob),
                    "library_id": file_row.external_library_id if file_row else None,
                },
                repair_action=(
                    "rescan_external_library"
                    if file_row is not None and file_row.external_library_id is not None
                    else None
                ),
            )


def _check_background_jobs(session: Session, run: VaultAuditRun) -> None:
    cutoff = utcnow() - timedelta(hours=1)
    stuck = session.exec(
        select(BackgroundJob).where(
            BackgroundJob.state.in_(("pending", "running")),  # type: ignore[attr-defined]
            BackgroundJob.updated_at < cutoff,
        )
    ).all()
    for job in stuck:
        _add(
            session,
            run,
            code="background_job_stuck",
            severity=VaultAuditSeverity.WARNING,
            resource_type="background_job",
            identifier=job.kind,
            details={"job_id": job.id, "kind": job.kind},
        )
    pending_imports = session.exec(
        select(InboxItem).where(
            (
                InboxItem.state.in_((InboxItemState.RESOLVING, InboxItemState.IMPORTING))  # type: ignore[attr-defined]
                & (InboxItem.updated_at < cutoff)
            )
            | (
                (InboxItem.state == InboxItemState.FAILED)
                & (InboxItem.retryable.is_(True))  # type: ignore[union-attr]
            )
        )
    ).all()
    for item in pending_imports:
        _add(
            session,
            run,
            code="background_job_stuck",
            severity=VaultAuditSeverity.WARNING,
            resource_type="pending_import",
            identifier=item.display_title or item.source_hostname or f"Pending Import {item.id}",
            details={"inbox_item_id": item.id, "state": item.state.value},
            repair_action="retry_pending_import",
        )


def _check_backups(session: Session, run: VaultAuditRun) -> None:
    from app.services import backup

    run.current_phase = "backups"
    for meta in backup.list_backups():
        if _cancelled(session, run):
            return
        result = backup.verify_backup(meta.id)
        for issue in result.findings:
            code = str(issue.get("code", "backup_manifest_invalid"))
            if code not in {
                "backup_manifest_invalid",
                "backup_member_missing",
                "backup_member_size_mismatch",
            }:
                code = "backup_manifest_invalid"
            member = Path(str(issue.get("member", "archive")).replace("\\", "/")).name
            _add(
                session,
                run,
                code=code,
                severity=VaultAuditSeverity.CRITICAL,
                resource_type="backup",
                identifier=meta.id,
                details={"member": member},
            )


def execute_run(run_id: int) -> None:
    with get_session_factory().scoped_session() as session:
        run = session.get(VaultAuditRun, run_id)
        if run is None or run.state != VaultAuditRunState.PENDING:
            return
        run.state = VaultAuditRunState.RUNNING
        run.started_at = utcnow()
        run.current_phase = "ownership_census"
        session.add(run)
        session.commit()
        try:
            snapshot = ownership_snapshot(session)
            if not _check_primary(session, run, snapshot.primary):
                return
            _check_external(session, run, snapshot.external)
            if _cancelled(session, run):
                return
            _check_database(session, run)
            if run.state == VaultAuditRunState.CANCELLED:
                return
            run.current_phase = "references"
            for blob in snapshot.embedded:
                if not get_backend().exists(blob.key):
                    _add(
                        session,
                        run,
                        code="embedded_image_missing",
                        severity=VaultAuditSeverity.WARNING,
                        resource_type=blob.resource_type,
                        identifier=_safe_name(blob),
                        details={"resource_id": blob.resource_id, "name": _safe_name(blob)},
                    )
            claimed = snapshot.claimed_keys
            for key in sorted(snapshot.discovered_keys - claimed):
                normalized = key.replace("\\", "/")
                if "/collection-images/" in normalized or "/document-images/" in normalized:
                    _add(
                        session,
                        run,
                        code="embedded_image_unreferenced",
                        severity=VaultAuditSeverity.INFO,
                        resource_type="embedded_image",
                        identifier=Path(normalized).name,
                    )
                    continue
                _add(
                    session,
                    run,
                    code="unowned_blob_detected",
                    severity=VaultAuditSeverity.INFO,
                    resource_type="storage_object",
                    identifier=Path(key.replace("\\", "/")).name,
                )
            _check_background_jobs(session, run)
            if run.mode == VaultAuditMode.FULL:
                _check_backups(session, run)
                if run.state == VaultAuditRunState.CANCELLED:
                    return
            run.state = VaultAuditRunState.COMPLETED
            run.progress = 100.0
            run.current_phase = "completed"
            run.finished_at = utcnow()
            session.add(run)
            session.commit()
        except Exception:
            logger.exception("vault audit %s failed", run_id)
            session.rollback()
            run = session.get(VaultAuditRun, run_id)
            if run is not None:
                run.state = VaultAuditRunState.FAILED
                run.error_code = "audit_failed"
                run.finished_at = utcnow()
                session.add(run)
                session.commit()


def ignore_finding(session: Session, finding_id: int, user_id: int) -> VaultAuditFinding | None:
    row = session.get(VaultAuditFinding, finding_id)
    if row is None:
        return None
    row.state = VaultAuditFindingState.IGNORED
    row.resolved_at = utcnow()
    row.resolved_by = user_id
    session.add(row)
    session.commit()
    session.refresh(row)
    audit.record(
        session,
        action="audit.ignore",
        resource_type="vault_audit_finding",
        resource_id=row.id,
        actor_id=user_id,
        diff={"code": row.code},
    )
    return row


def _restore_recommended(session: Session, model_id: int) -> bool:
    files = session.exec(
        select(File)
        .where(File.model_id == model_id, File.file_type == FileType.GCODE, live(File))
        .order_by(File.version.desc(), File.id.desc())  # type: ignore[attr-defined]
    ).all()
    if not files:
        return False
    for row in files:
        row.is_recommended = False
        session.add(row)
    session.flush()
    files[0].is_recommended = True
    session.add(files[0])
    session.commit()
    return True


def _reparse_metadata(session: Session, file_id: int) -> bool:
    from app.services.ingestion import _gcode_strategy, _mesh_strategy

    row = session.get(File, file_id)
    if row is None or row.deleted_at is not None or session.exec(
        select(Metadata).where(Metadata.file_id == file_id)
    ).first() is not None:
        return row is not None
    backend = get_backend()
    if not backend.exists(row.path):
        return False
    with backend.local_path(row.path) as path:
        strategy = _gcode_strategy() if row.file_type == FileType.GCODE else _mesh_strategy(row.file_type)
        values, _thumbnail = strategy.process(path)
    fields = {key: value for key, value in values.items() if key in Metadata.model_fields}
    session.add(Metadata(file_id=file_id, **fields))
    session.commit()
    return True


def repair_finding(session: Session, finding_id: int, user_id: int) -> VaultAuditFinding | None:
    row = session.get(VaultAuditFinding, finding_id)
    if row is None:
        return None
    if row.state == VaultAuditFindingState.RESOLVED:
        return row
    details = _details(row)
    ok = False
    if row.repair_action == "regenerate_thumbnail":
        ok = thumbnail_repair.regenerate_model_thumbnail(session, int(details["model_id"]))
    elif row.repair_action == "restore_recommended_revision":
        ok = _restore_recommended(session, int(details["model_id"]))
    elif row.repair_action == "reparse_metadata":
        ok = _reparse_metadata(session, int(details["file_id"]))
    elif row.repair_action == "retry_pending_import":
        item = session.get(InboxItem, int(details["inbox_item_id"]))
        if item is not None and item.state in {
            InboxItemState.FAILED,
            InboxItemState.RESOLVING,
            InboxItemState.IMPORTING,
        }:
            item.state = InboxItemState.REVIEW if item.manifest_json != "{}" else InboxItemState.CAPTURED
            item.error_code = None
            item.retryable = True
            item.updated_at = utcnow()
            session.add(item)
            session.commit()
            ok = True
    elif row.repair_action == "rescan_external_library":
        from app.services.external_library import scan_library

        summary = scan_library(int(details["library_id"]))
        ok = not bool(summary.get("aborted_unmounted"))
    if not ok:
        return row
    row.state = VaultAuditFindingState.RESOLVED
    row.resolved_at = utcnow()
    row.resolved_by = user_id
    session.add(row)
    session.commit()
    session.refresh(row)
    audit.record(
        session,
        action="audit.repair",
        resource_type="vault_audit_finding",
        resource_id=row.id,
        actor_id=user_id,
        diff={"code": row.code, "repair_action": row.repair_action},
    )
    return row
