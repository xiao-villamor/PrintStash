from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from sqlmodel import Session, col, select

import app.modules.backups.backup.targets as backup_targets
import app.modules.backups.backup.verification as backup_verification
from app.core.logging import get_logger
from app.core.time import ensure_utc, utcnow
from app.db.models import (
    SENTINEL_MODEL_HASH,
    BackgroundJob,
    Collection,
    Document,
    ExternalLibrary,
    File,
    FileType,
    InboxItem,
    InboxItemState,
    Metadata,
    Model,
    OwnedStorageObject,
    StorageObjectState,
    VaultAuditFinding,
    VaultAuditFindingState,
    VaultAuditMode,
    VaultAuditRun,
    VaultAuditRunState,
    VaultAuditSeverity,
)
from app.db.scopes import live
from app.db.session import get_session_factory
from app.modules.administration import audit
from app.modules.media import thumbnail_repair
from app.modules.storage.artifact_content import ArtifactContentError, resolve
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.storage.storage_utils import OwnedBlob, ownership_snapshot
from app.schemas.maintenance import VaultAuditFindingRead, VaultAuditRunRead

if TYPE_CHECKING:
    from app.modules.storage.capacity import CapacityReservationHandle

_ACTIVE_STATES = (VaultAuditRunState.PENDING, VaultAuditRunState.RUNNING)
logger = get_logger(__name__)


class AuditWindowExpired(Exception):
    """The scheduled read budget ended; do not classify this as corrupt storage."""


def _auto_repair_active(run: VaultAuditRun) -> bool:
    return (
        run.state == VaultAuditRunState.COMPLETED
        and run.current_phase == "auto_repair"
        and run.active_slot is not None
    )


def _safe_name(blob: OwnedBlob) -> str:
    return (blob.display_name or Path(blob.key.replace("\\", "/")).name)[:255]


def _unowned_storage_details(key: str) -> dict[str, int | str]:
    """Describe an unclaimed object without widening ownership or exposing its key."""
    backend = get_backend()
    details: dict[str, int | str] = {}
    try:
        details["actual_size"] = backend.stat_size(key)
    except Exception:
        pass
    try:
        direct = backend.direct_path(key)
        if direct is not None:
            stat = direct.stat(follow_symlinks=False)
            details["modified_at"] = datetime.fromtimestamp(
                stat.st_mtime, tz=UTC
            ).isoformat()
    except Exception:
        pass
    return details


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


def read_run(
    session: Session, row: VaultAuditRun, *, findings: bool = True
) -> VaultAuditRunRead:
    finding_rows: list[VaultAuditFinding] = []
    severities: list[VaultAuditSeverity] = []
    if row.id is not None and findings:
        finding_rows = list(
            session.exec(
                select(VaultAuditFinding)
                .where(VaultAuditFinding.run_id == row.id)
                .order_by(VaultAuditFinding.severity.desc(), VaultAuditFinding.id.asc())  # type: ignore[attr-defined]
            ).all()
        )
        severities = [finding.severity for finding in finding_rows]
    elif row.id is not None:
        severities = list(
            session.exec(
                select(VaultAuditFinding.severity).where(
                    VaultAuditFinding.run_id == row.id
                )
            ).all()
        )
    values = row.model_dump()
    values.update(
        critical_count=severities.count(VaultAuditSeverity.CRITICAL),
        warning_count=severities.count(VaultAuditSeverity.WARNING),
        info_count=severities.count(VaultAuditSeverity.INFO),
    )
    return VaultAuditRunRead(
        **values,
        findings=[finding_read(finding) for finding in finding_rows],
    )


def _blob_is_live(session: Session, blob: OwnedBlob) -> bool:
    """Keep trash claimed by storage without reporting it as a live issue."""
    if blob.resource_type == "file":
        file_row = session.get(File, blob.resource_id)
        if file_row is None:
            return True
        if file_row.deleted_at is not None:
            return False
        model_row = session.get(Model, file_row.model_id)
        return model_row is None or model_row.deleted_at is None
    model = {
        "document": Document,
        "document_image": Document,
        "collection_image": Collection,
    }.get(blob.resource_type)
    if model is None:
        return True
    row = session.get(model, blob.resource_id)
    return row is None or row.deleted_at is None


def _sync_counts(session: Session, run: VaultAuditRun) -> None:
    """Persist exact totals after the audit, independent of phase refreshes."""
    session.flush()
    severities = list(
        session.exec(
            select(VaultAuditFinding.severity).where(VaultAuditFinding.run_id == run.id)
        ).all()
    )
    run.critical_count = severities.count(VaultAuditSeverity.CRITICAL)
    run.warning_count = severities.count(VaultAuditSeverity.WARNING)
    run.info_count = severities.count(VaultAuditSeverity.INFO)


def create_run(
    session: Session, requested_by: int, mode: VaultAuditMode
) -> tuple[VaultAuditRun, bool]:
    from app.modules.administration.vault_audit_policy import admit_run

    return admit_run(session, requested_by, mode)


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
    if row.state in _ACTIVE_STATES or _auto_repair_active(row):
        from app.db.models import AuditLog

        session.add(
            AuditLog(
                action="audit.cancel_requested",
                resource_type="vault_audit_run",
                resource_id=run_id,
                actor_id=audit.current_audit_context()[0],
            )
        )
        row.cancel_requested = True
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


def reconcile_interrupted_runs() -> int:
    from app.modules.storage.capacity import CapacityManager

    factory = get_session_factory()
    with factory.scoped_session() as session:
        rows = session.exec(
            select(VaultAuditRun).where(VaultAuditRun.state.in_(_ACTIVE_STATES))
        ).all()  # type: ignore[attr-defined]
        interrupted = len(rows)
        for row in rows:
            row.state = VaultAuditRunState.FAILED
            row.error_code = "audit_interrupted"
            row.finished_at = utcnow()
            from app.modules.administration.vault_audit_results import record_terminal

            record_terminal(session, row)
            session.add(row)
        claims = session.exec(
            select(VaultAuditRun).where(col(VaultAuditRun.active_slot).is_not(None))
        ).all()  # noqa: E711
        run_ids = [row.id for row in claims]
        for row in claims:
            if _auto_repair_active(row):
                row.current_phase = "completed"
            row.active_slot = None
            session.add(row)
        session.commit()
    manager = CapacityManager(factory)
    for run_id in run_ids:
        manager.release(f"audit:{run_id}")
    return interrupted


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
    from app.runtime.maintenance import restore_in_progress

    maintenance = restore_in_progress()
    expired = run.deadline_at is not None and utcnow() >= ensure_utc(run.deadline_at)
    if not run.cancel_requested and not expired and not maintenance:
        return False
    run.active_slot = None
    if expired:
        run.error_code = "audit_window_expired"
    elif maintenance:
        run.error_code = "audit_maintenance"
    run.state = VaultAuditRunState.CANCELLED
    run.finished_at = utcnow()
    run.current_phase = "cancelled"
    from app.modules.administration.vault_audit_results import record_terminal

    record_terminal(session, run)
    session.add(run)
    session.commit()
    return True


def _hash_blob(
    key: str, session: Session | None = None, run: VaultAuditRun | None = None
) -> str:
    digest = hashlib.sha256()
    started = time.monotonic()
    consumed = 0
    for chunk in get_backend().stream_chunks(key):
        if run is not None and session is not None:
            if _cancelled(session, run):
                raise AuditWindowExpired
            consumed += len(chunk)
            run.bytes_read += len(chunk)
            session.add(run)
            # Release SQLite's writer before another provider read or a
            # throttling wait so cancellation can commit from another request.
            session.commit()
            if run.bytes_per_second:
                delay = consumed / run.bytes_per_second - (time.monotonic() - started)
                while delay > 0:
                    if _cancelled(session, run):
                        raise AuditWindowExpired
                    time.sleep(min(delay, 0.2))
                    delay = consumed / run.bytes_per_second - (
                        time.monotonic() - started
                    )
        digest.update(chunk)
    return digest.hexdigest()


def _check_primary(
    session: Session, run: VaultAuditRun, blobs: list[OwnedBlob]
) -> bool:
    backend = get_backend()
    blobs = [blob for blob in blobs if _blob_is_live(session, blob)]
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
                    details={
                        **details,
                        "expected_size": blob.expected_size,
                        "actual_size": size,
                    },
                )
            if run.mode == VaultAuditMode.FULL and blob.expected_sha256:
                actual = _hash_blob(blob.key, session, run)
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
        except AuditWindowExpired:
            return False
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


def _check_artifact_cache(session: Session, run: VaultAuditRun) -> None:
    from app.modules.storage.materializer_runtime import get_materializer

    cache = get_materializer()
    if cache is None:
        return
    try:
        observation = cache.inspect_entries(full=run.mode == VaultAuditMode.FULL)
        if observation["corrupt"]:
            _add(
                session,
                run,
                code="artifact_cache_corrupt",
                severity=VaultAuditSeverity.WARNING,
                resource_type="artifact_cache",
                identifier="Artifact cache",
                details=observation,
            )
    except Exception:
        _add(
            session,
            run,
            code="artifact_cache_unavailable",
            severity=VaultAuditSeverity.INFO,
            resource_type="artifact_cache",
            identifier="Artifact cache",
        )


def _check_database(session: Session, run: VaultAuditRun) -> None:
    backend = get_backend()
    run.current_phase = "database"
    for lifecycle in backend.destructive_lifecycle_findings():
        _add(
            session,
            run,
            code="managed_storage_lifecycle_expiration",
            severity=VaultAuditSeverity.CRITICAL,
            resource_type="storage",
            identifier=str(lifecycle.get("rule_id", "unnamed")),
            details=lifecycle,
        )
    models = session.exec(select(Model).where(live(Model))).all()
    for model in models:
        # The reserved external-job placeholder is database compatibility
        # state, not a Vault model. It owns no managed storage and must not
        # make every otherwise-healthy migration preflight fail.
        if model.hash == SENTINEL_MODEL_HASH:
            continue
        if _cancelled(session, run):
            return
        files = session.exec(
            select(File).where(File.model_id == model.id, live(File))
        ).all()
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
                select(Metadata.file_id).where(
                    Metadata.file_id.in_([item.id for item in files])
                )  # type: ignore[union-attr]
            ).all()
        )
        for item in files:
            if not item.is_external:
                direct = backend.direct_path(item.path)
                primary_prefix = backend.blob_key(
                    "audit-namespace", 1, "probe"
                ).removesuffix("audit-namespace/v1/probe")
                outside_managed = (
                    direct is not None
                    and not direct.resolve(strict=False).is_relative_to(
                        Path(primary_prefix).resolve(strict=False)
                    )
                ) or (direct is None and not item.path.startswith(primary_prefix))
                if outside_managed:
                    _add(
                        session,
                        run,
                        code="managed_storage_namespace_escape",
                        severity=VaultAuditSeverity.CRITICAL,
                        resource_type="file",
                        identifier=item.original_filename,
                        details={"file_id": item.id},
                    )
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
            thumbnail_file = session.get(File, model.thumbnail_file_id)
            current = (
                model.thumbnail_path
                or (
                    thumbnail_file.thumbnail_path
                    if thumbnail_file is not None
                    else None
                )
                or backend.thumbnail_key(model.thumbnail_file_id)
            )
            legacy = backend.legacy_thumbnail_key(model.thumbnail_file_id)
            try:
                if backend.exists(current):
                    key = current
                else:
                    compatibility = backend.thumbnail_key(model.thumbnail_file_id)
                    key = compatibility if backend.exists(compatibility) else legacy
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
                        details={
                            "model_id": model.id,
                            "file_id": model.thumbnail_file_id,
                        },
                        repair_action="regenerate_thumbnail",
                    )


def _check_external(
    session: Session, run: VaultAuditRun, blobs: list[OwnedBlob]
) -> None:
    for library in session.exec(select(ExternalLibrary)).all():
        if _cancelled(session, run):
            return
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
    for blob in (blob for blob in blobs if _blob_is_live(session, blob)):
        if _cancelled(session, run):
            return
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
    if _cancelled(session, run):
        return
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
                InboxItem.state.in_(
                    (InboxItemState.RESOLVING, InboxItemState.IMPORTING)
                )  # type: ignore[attr-defined]
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
            identifier=item.display_title
            or item.source_hostname
            or f"Pending Import {item.id}",
            details={"inbox_item_id": item.id, "state": item.state.value},
            repair_action="retry_pending_import",
        )


def _check_backups(
    session: Session,
    run: VaultAuditRun,
    *,
    capacity: CapacityReservationHandle | None = None,
) -> None:

    run.current_phase = "backups"
    # Audit the ownership ledger directly. Discovery/listing is a presentation
    # seam and can omit inaccessible objects or collapse same-ID replicas.
    # Cache projections are deliberately excluded: they are rebuildable
    # derivatives, not authoritative backup sources.
    rows = session.exec(
        select(OwnedStorageObject).where(
            (
                OwnedStorageObject.backend.in_(("local", "backup-s3"))
                | OwnedStorageObject.backend.startswith("backup-opendal-")
            ),
            OwnedStorageObject.object_kind.in_(("backup", "backup-legacy")),
            OwnedStorageObject.state.in_(
                (StorageObjectState.COMMITTED, StorageObjectState.BLOCKED)
            ),
        )
    ).all()
    if _cancelled(session, run):
        return
    started = time.monotonic()
    consumed = 0
    base_resources = list(capacity.resources) if capacity is not None else []

    def allocate(size: int) -> None:
        import tempfile

        from app.modules.storage.capacity import CapacityResource

        if capacity is not None:
            capacity.renew(
                [
                    *base_resources,
                    CapacityResource.for_path(
                        Path(tempfile.gettempdir()), size, role="audit-database"
                    ),
                ]
            )

    def progress(size: int) -> None:
        nonlocal consumed
        consumed += size
        run.bytes_read += size
        session.add(run)
        # Verification can reserve scratch space or persist a downloaded
        # receipt in another session immediately after this callback returns.
        session.commit()
        if _cancelled(session, run):
            raise AuditWindowExpired
        if run.bytes_per_second:
            delay = consumed / run.bytes_per_second - (time.monotonic() - started)
            while delay > 0:
                if _cancelled(session, run):
                    raise AuditWindowExpired
                time.sleep(min(delay, 0.2))
                delay = consumed / run.bytes_per_second - (time.monotonic() - started)

    for row in rows:
        if _cancelled(session, run):
            return
        location = "local" if row.backend == "local" else "s3"
        source_ref = backup_targets.source_reference(  # noqa: SLF001
            location=location,
            namespace=row.namespace,
            path=row.key,
            provider_ref=row.provider_ref,
        )
        # Publish prior findings/progress before entering the backup owner.
        # Its receipt and capacity transactions must never nest under our
        # SQLite writer; result events still commit with record_success below.
        session.add(run)
        session.commit()
        try:
            result = (
                backup_verification.verify_backup_ownership(
                    int(row.id), progress=progress, fresh_remote=True, allocate=allocate
                )
                if run.trigger == "scheduled"
                else backup_verification.verify_backup_ownership(int(row.id))
            )
        except AuditWindowExpired:
            return
        if result.status == "valid":
            continue
        if result.status in {"missing", "inaccessible"}:
            default_code = "backup_storage_inaccessible"
        elif result.status == "identity":
            default_code = "backup_identity_unavailable"
        elif result.status == "digest":
            default_code = "backup_digest_mismatch"
        else:
            default_code = "backup_corrupt"
        findings = result.verification.findings if result.verification else []
        if not findings:
            _add(
                session,
                run,
                code=default_code,
                severity=VaultAuditSeverity.CRITICAL,
                resource_type="backup",
                identifier=source_ref,
                details={
                    "ownership_id": int(row.id),
                    "location": location,
                    "provider_ref": row.provider_ref,
                    "error": result.error,
                },
            )
            continue
        for issue in findings:
            code = str(issue.get("code", "backup_manifest_invalid"))
            code = {
                "backup_remote_identity_unavailable": "backup_identity_unavailable",
                "backup_remote_etag_changed": "backup_identity_mismatch",
                "backup_remote_version_changed": "backup_identity_mismatch",
                "backup_download_digest_mismatch": "backup_digest_mismatch",
                "backup_publication_digest_mismatch": "backup_digest_mismatch",
                "backup_member_hash_mismatch": "backup_corrupt",
                "backup_blob_hash_changed": "backup_corrupt",
            }.get(code, code)
            if code not in {
                "backup_manifest_invalid",
                "backup_member_missing",
                "backup_member_size_mismatch",
                "backup_identity_unavailable",
                "backup_identity_mismatch",
                "backup_digest_mismatch",
                "backup_corrupt",
            }:
                code = "backup_manifest_invalid"
            member = Path(str(issue.get("member", "archive")).replace("\\", "/")).name
            _add(
                session,
                run,
                code=code,
                severity=VaultAuditSeverity.CRITICAL,
                resource_type="backup",
                identifier=source_ref,
                details={
                    "member": member,
                    "ownership_id": int(row.id),
                    "location": location,
                    "provider_ref": row.provider_ref,
                },
            )


def execute_run(run_id: int) -> None:
    from app.core.errors import OperationError
    from app.modules.administration.vault_audit_capacity import estimate_resources
    from app.modules.storage.capacity import CapacityManager
    from app.runtime.maintenance import begin_mutating_operation, end_mutating_operation

    if not begin_mutating_operation():
        return
    try:
        factory = get_session_factory()
        try:
            with factory.scoped_session() as session:
                run = session.get(VaultAuditRun, run_id)
                if run is None or run.state != VaultAuditRunState.PENDING:
                    return
                resources = estimate_resources(session, run)
            with CapacityManager(factory).hold(
                f"audit:{run_id}", resources
            ) as capacity:
                _execute_run(run_id, capacity=capacity)
        except OperationError as exc:
            with factory.scoped_session() as session:
                run = session.get(VaultAuditRun, run_id)
                if run is not None:
                    run.state = VaultAuditRunState.FAILED
                    run.error_code = exc.code
                    run.active_slot = None
                    run.finished_at = utcnow()
                    from app.modules.administration.vault_audit_results import (
                        record_terminal,
                    )

                    record_terminal(session, run)
                    session.add(run)
                    session.commit()
    finally:
        end_mutating_operation()


def _execute_run(
    run_id: int, *, capacity: CapacityReservationHandle | None = None
) -> None:
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
            if _cancelled(session, run):
                return
            snapshot = ownership_snapshot(session)
            if not _check_primary(session, run, snapshot.primary):
                return
            _check_artifact_cache(session, run)
            _check_external(session, run, snapshot.external)
            if _cancelled(session, run):
                return
            _check_database(session, run)
            if run.state == VaultAuditRunState.CANCELLED:
                return
            run.current_phase = "references"
            for blob in (
                blob for blob in snapshot.embedded if _blob_is_live(session, blob)
            ):
                if not get_backend().exists(blob.key):
                    _add(
                        session,
                        run,
                        code="embedded_image_missing",
                        severity=VaultAuditSeverity.WARNING,
                        resource_type=blob.resource_type,
                        identifier=_safe_name(blob),
                        details={
                            "resource_id": blob.resource_id,
                            "name": _safe_name(blob),
                        },
                    )
            claimed = snapshot.claimed_keys
            for key in sorted(snapshot.discovered_keys - claimed):
                normalized = key.replace("\\", "/")
                if (
                    "/collection-images/" in normalized
                    or "/document-images/" in normalized
                ):
                    _add(
                        session,
                        run,
                        code="embedded_image_unreferenced",
                        severity=VaultAuditSeverity.INFO,
                        resource_type="embedded_image",
                        identifier=Path(normalized).name,
                    )
                    continue
                details = _unowned_storage_details(key)
                actual_size = details.get("actual_size")
                if isinstance(actual_size, int):
                    run.unclaimed_bytes += actual_size
                else:
                    run.unclaimed_unknown_size_count += 1
                _add(
                    session,
                    run,
                    code="unowned_blob_detected",
                    severity=VaultAuditSeverity.INFO,
                    resource_type="storage_object",
                    identifier=Path(key.replace("\\", "/")).name,
                    details=details,
                )
            _check_background_jobs(session, run)
            if run.mode == VaultAuditMode.FULL:
                _check_backups(session, run, capacity=capacity)
                if run.state == VaultAuditRunState.CANCELLED:
                    return
            if _cancelled(session, run):
                return
            run.state = VaultAuditRunState.COMPLETED
            run.progress = 100.0
            run.current_phase = "completed"
            run.finished_at = utcnow()
            _sync_counts(session, run)
            from app.modules.administration.vault_audit_results import (
                record_success,
                repair_safe_findings,
            )

            record_success(session, run)
            run.current_phase = "auto_repair"
            session.add(run)
            session.commit()
            repair_safe_findings(session, run)
            run.current_phase = "completed"
            run.active_slot = None
            session.add(run)
            session.commit()
        except Exception:
            logger.error("vault audit %s failed", run_id)
            session.rollback()
            run = session.get(VaultAuditRun, run_id)
            if run is not None:
                run.active_slot = None
                if run.result_recorded:
                    run.state = VaultAuditRunState.COMPLETED
                    run.current_phase = "completed"
                    session.add(run)
                    session.commit()
                    return
                run.state = VaultAuditRunState.FAILED
                run.error_code = "audit_failed"
                run.finished_at = utcnow()
                from app.modules.administration.vault_audit_results import (
                    record_terminal,
                )

                record_terminal(session, run)
                session.add(run)
                session.commit()


def ignore_finding(
    session: Session, finding_id: int, user_id: int
) -> VaultAuditFinding | None:
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
    from app.modules.ingestion.ingestion import strategy_for_artifact

    row = session.get(File, file_id)
    if (
        row is None
        or row.deleted_at is not None
        or session.exec(select(Metadata).where(Metadata.file_id == file_id)).first()
        is not None
    ):
        return row is not None
    try:
        with resolve(row).materialize(authoritative=True) as path:
            strategy = strategy_for_artifact(row.file_type)
            values, _thumbnail = strategy.process(path, lambda _label: None)
    except ArtifactContentError:
        return False
    fields = {
        key: value for key, value in values.items() if key in Metadata.model_fields
    }
    session.add(Metadata(file_id=file_id, **fields))
    session.commit()
    return True


def repair_finding(
    session: Session, finding_id: int, user_id: int
) -> VaultAuditFinding | None:
    row = session.get(VaultAuditFinding, finding_id)
    if row is None:
        return None
    if row.state == VaultAuditFindingState.RESOLVED:
        return row
    details = _details(row)
    ok = False
    if row.repair_action == "regenerate_thumbnail":
        ok = thumbnail_repair.regenerate_model_thumbnail(
            session, int(details["model_id"])
        )
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
            item.state = (
                InboxItemState.REVIEW
                if item.manifest_json != "{}"
                else InboxItemState.CAPTURED
            )
            item.error_code = None
            item.retryable = True
            item.updated_at = utcnow()
            session.add(item)
            session.commit()
            ok = True
    elif row.repair_action == "rescan_external_library":
        from app.modules.sources.external_library import scan_library

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
