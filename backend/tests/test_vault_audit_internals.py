"""Unit coverage for app/services/vault_audit.py's run lifecycle, per-phase
checks, and finding repair dispatch — beyond the happy-path in test_vault_audit.py."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import (
    BackgroundJob,
    ExternalLibrary,
    File,
    FileType,
    InboxItem,
    InboxItemState,
    Model,
    User,
    VaultAuditFinding,
    VaultAuditFindingState,
    VaultAuditMode,
    VaultAuditRun,
    VaultAuditRunState,
    VaultAuditSeverity,
)
from app.services import vault_audit
from app.services.storage_backend import get_backend
from app.services.storage_utils import OwnedBlob, StorageOwnershipSnapshot


def _make_user(session: Session, username: str, *, admin: bool = True) -> User:
    user = User(username=username, hashed_password="x", is_superuser=admin)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _make_run(session: Session, user: User, mode: VaultAuditMode = VaultAuditMode.QUICK) -> VaultAuditRun:
    run = VaultAuditRun(requested_by=user.id, mode=mode)
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _make_model(session: Session, slug: str) -> Model:
    model = Model(name=slug, slug=slug, hash=(slug * 8)[:64].ljust(64, "0"))
    session.add(model)
    session.commit()
    session.refresh(model)
    return model


def _make_file(session: Session, model: Model, **overrides) -> File:
    defaults = dict(
        model_id=model.id,
        path=f"{model.slug}.stl",
        original_filename=f"{model.slug}.stl",
        file_type=FileType.STL,
        size_bytes=16,
        sha256="a" * 64,
    )
    defaults.update(overrides)
    row = File(**defaults)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


# --------------------------------------------------------------------------- #
# list_runs / latest_run / request_cancel / reconcile_interrupted_runs
# --------------------------------------------------------------------------- #


def test_list_runs_orders_newest_first_and_respects_limit(db_session: Session) -> None:
    user = _make_user(db_session, "runs-owner")
    older = _make_run(db_session, user)
    older.created_at = utcnow() - timedelta(hours=1)
    db_session.add(older)
    db_session.commit()
    newer = _make_run(db_session, user)

    rows = vault_audit.list_runs(db_session, limit=1)

    assert len(rows) == 1
    assert rows[0].id == newer.id


def test_latest_run_returns_most_recent(db_session: Session) -> None:
    user = _make_user(db_session, "latest-owner")
    _make_run(db_session, user)
    newest = _make_run(db_session, user)

    result = vault_audit.latest_run(db_session)

    assert result is not None
    assert result.id == newest.id


def test_request_cancel_flags_active_run(db_session: Session) -> None:
    user = _make_user(db_session, "cancel-owner")
    run = _make_run(db_session, user)

    result = vault_audit.request_cancel(db_session, run.id)

    assert result is not None
    assert result.cancel_requested is True


def test_request_cancel_ignores_terminal_run(db_session: Session) -> None:
    user = _make_user(db_session, "cancel-owner2")
    run = _make_run(db_session, user)
    run.state = VaultAuditRunState.COMPLETED
    db_session.add(run)
    db_session.commit()

    result = vault_audit.request_cancel(db_session, run.id)

    assert result is not None
    assert result.cancel_requested is False


def test_request_cancel_missing_run_returns_none(db_session: Session) -> None:
    assert vault_audit.request_cancel(db_session, 999999) is None


def test_reconcile_interrupted_runs_marks_running_as_failed(db_session: Session) -> None:
    user = _make_user(db_session, "reconcile-owner")
    run = _make_run(db_session, user)
    run.state = VaultAuditRunState.RUNNING
    db_session.add(run)
    db_session.commit()
    run_id = run.id

    count = vault_audit.reconcile_interrupted_runs()

    assert count >= 1
    result = db_session.get(VaultAuditRun, run_id)
    db_session.refresh(result)
    assert result.state == VaultAuditRunState.FAILED
    assert result.error_code == "audit_interrupted"


# --------------------------------------------------------------------------- #
# _check_primary
# --------------------------------------------------------------------------- #


def test_check_primary_flags_size_and_hash_mismatch(db_session: Session) -> None:
    user = _make_user(db_session, "primary-owner")
    run = _make_run(db_session, user, VaultAuditMode.FULL)
    get_backend().write_bytes(b"actual-bytes", "size-mismatch.stl")
    get_backend().write_bytes(b"hash-mismatch-content", "hash-mismatch.stl")
    blobs = [
        OwnedBlob(key="size-mismatch.stl", resource_type="file", resource_id=1, expected_size=999),
        OwnedBlob(
            key="hash-mismatch.stl",
            resource_type="file",
            resource_id=2,
            expected_sha256="0" * 64,
        ),
    ]

    completed = vault_audit._check_primary(db_session, run, blobs)

    assert completed is True
    codes = {finding.code for finding in db_session.exec(
        __import__("sqlmodel").select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
    ).all()}
    assert "owned_blob_size_mismatch" in codes
    assert "owned_blob_hash_mismatch" in codes


def test_check_primary_unreadable_blob_becomes_finding(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _make_user(db_session, "primary-owner2")
    run = _make_run(db_session, user)
    get_backend().write_bytes(b"data", "unreadable.stl")

    def boom(_key: str) -> int:
        raise OSError("disk exploded")

    monkeypatch.setattr(get_backend(), "stat_size", boom)

    completed = vault_audit._check_primary(
        db_session, run, [OwnedBlob(key="unreadable.stl", resource_type="file", resource_id=3)]
    )

    assert completed is True
    from sqlmodel import select

    findings = db_session.exec(select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)).all()
    assert any(f.code == "owned_blob_unreadable" for f in findings)


def test_check_primary_stops_when_cancelled(db_session: Session) -> None:
    user = _make_user(db_session, "primary-owner3")
    run = _make_run(db_session, user)
    run.cancel_requested = True
    db_session.add(run)
    db_session.commit()

    completed = vault_audit._check_primary(
        db_session, run, [OwnedBlob(key="whatever.stl", resource_type="file", resource_id=4)]
    )

    assert completed is False
    db_session.refresh(run)
    assert run.state == VaultAuditRunState.CANCELLED


# --------------------------------------------------------------------------- #
# _check_database
# --------------------------------------------------------------------------- #


def test_check_database_flags_model_without_live_artifact(db_session: Session) -> None:
    user = _make_user(db_session, "db-owner")
    run = _make_run(db_session, user)
    _make_model(db_session, "no-files")

    vault_audit._check_database(db_session, run)

    from sqlmodel import select

    findings = db_session.exec(select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)).all()
    assert any(f.code == "model_without_live_artifact" for f in findings)


def test_check_database_flags_missing_and_duplicate_recommended_revision(db_session: Session) -> None:
    user = _make_user(db_session, "db-owner2")
    run = _make_run(db_session, user)
    missing_rec = _make_model(db_session, "no-rec")
    _make_file(db_session, missing_rec, file_type=FileType.GCODE, is_recommended=False, path="a.gcode")
    dup_rec = _make_model(db_session, "dup-rec")
    _make_file(db_session, dup_rec, file_type=FileType.GCODE, is_recommended=True, path="b1.gcode")
    _make_file(db_session, dup_rec, file_type=FileType.GCODE, is_recommended=True, path="b2.gcode", version=2)

    vault_audit._check_database(db_session, run)

    from sqlmodel import select

    findings = db_session.exec(select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)).all()
    codes = {f.code for f in findings}
    assert "recommended_revision_missing" in codes
    assert "recommended_revision_duplicate" in codes


def test_check_database_flags_metadata_missing(db_session: Session) -> None:
    user = _make_user(db_session, "db-owner3")
    run = _make_run(db_session, user)
    model = _make_model(db_session, "no-meta")
    _make_file(db_session, model)

    vault_audit._check_database(db_session, run)

    from sqlmodel import select

    findings = db_session.exec(select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)).all()
    assert any(f.code == "metadata_missing" for f in findings)


def test_check_database_flags_missing_thumbnail(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _make_user(db_session, "db-owner4")
    run = _make_run(db_session, user)
    model = _make_model(db_session, "no-thumb")
    file_row = _make_file(db_session, model)
    model.thumbnail_file_id = file_row.id
    db_session.add(model)
    db_session.commit()

    # `settings.thumb_dir` is a real, shared absolute path across the whole
    # suite (not per-test tmp_path), so don't rely on it happening to be
    # empty — pin `exists()` so this test can't collide with a leftover
    # thumbnail file another test wrote for the same file id.
    monkeypatch.setattr(get_backend(), "exists", lambda _key: False)

    vault_audit._check_database(db_session, run)

    from sqlmodel import select

    findings = db_session.exec(select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)).all()
    assert any(f.code == "thumbnail_missing" for f in findings)


def test_check_database_stops_when_cancelled(db_session: Session) -> None:
    user = _make_user(db_session, "db-owner5")
    run = _make_run(db_session, user)
    run.cancel_requested = True
    db_session.add(run)
    db_session.commit()
    _make_model(db_session, "irrelevant")

    vault_audit._check_database(db_session, run)

    db_session.refresh(run)
    assert run.state == VaultAuditRunState.CANCELLED


# --------------------------------------------------------------------------- #
# _check_external
# --------------------------------------------------------------------------- #


def test_check_external_flags_unavailable_root(db_session: Session) -> None:
    user = _make_user(db_session, "ext-owner")
    run = _make_run(db_session, user)
    library = ExternalLibrary(name="nas", root_path="/nowhere/does-not-exist")
    db_session.add(library)
    db_session.commit()

    vault_audit._check_external(db_session, run, [])

    from sqlmodel import select

    findings = db_session.exec(select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)).all()
    assert any(f.code == "external_root_unavailable" for f in findings)


def test_check_external_flags_missing_linked_file(db_session: Session) -> None:
    user = _make_user(db_session, "ext-owner2")
    run = _make_run(db_session, user)
    model = _make_model(db_session, "ext-model")
    file_row = _make_file(
        db_session, model, path="/nowhere/missing.stl", is_external=True, external_library_id=None
    )

    vault_audit._check_external(
        db_session,
        run,
        [OwnedBlob(key=file_row.path, resource_type="file", resource_id=file_row.id, display_name="missing.stl")],
    )

    from sqlmodel import select

    findings = db_session.exec(select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)).all()
    linked = [f for f in findings if f.code == "linked_file_missing"]
    assert len(linked) == 1
    assert linked[0].repair_action is None


# --------------------------------------------------------------------------- #
# _check_background_jobs
# --------------------------------------------------------------------------- #


def test_check_background_jobs_flags_stuck_job_and_pending_import(db_session: Session) -> None:
    user = _make_user(db_session, "jobs-owner")
    run = _make_run(db_session, user)
    stuck_job = BackgroundJob(id="stuck-job-1", kind="thumbnail_rebuild", state="running")
    stuck_job.updated_at = utcnow() - timedelta(hours=2)
    db_session.add(stuck_job)
    stuck_import = InboxItem(
        owner_user_id=user.id,
        source_url="https://example.com/x",
        state=InboxItemState.FAILED,
        retryable=True,
    )
    db_session.add(stuck_import)
    db_session.commit()

    vault_audit._check_background_jobs(db_session, run)

    from sqlmodel import select

    findings = db_session.exec(select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)).all()
    resource_types = {f.resource_type for f in findings}
    assert "background_job" in resource_types
    assert "pending_import" in resource_types


# --------------------------------------------------------------------------- #
# execute_run — cancellation, exceptions, embedded/unowned discovery, backups
# --------------------------------------------------------------------------- #


def test_execute_run_ignores_non_pending_run(db_session: Session) -> None:
    user = _make_user(db_session, "exec-owner")
    run = _make_run(db_session, user)
    run.state = VaultAuditRunState.COMPLETED
    db_session.add(run)
    db_session.commit()

    vault_audit.execute_run(run.id)

    db_session.refresh(run)
    assert run.state == VaultAuditRunState.COMPLETED


def test_execute_run_cancelled_before_primary_check_completes(db_session: Session) -> None:
    user = _make_user(db_session, "exec-owner2")
    run = _make_run(db_session, user)
    model = _make_model(db_session, "cancel-mid")
    _make_file(db_session, model)
    run.cancel_requested = True
    db_session.add(run)
    db_session.commit()

    vault_audit.execute_run(run.id)

    db_session.refresh(run)
    assert run.state == VaultAuditRunState.CANCELLED


def test_execute_run_marks_failed_on_unexpected_exception(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _make_user(db_session, "exec-owner3")
    run = _make_run(db_session, user)

    def boom(_session):
        raise RuntimeError("snapshot exploded")

    monkeypatch.setattr(vault_audit, "ownership_snapshot", boom)

    vault_audit.execute_run(run.id)

    db_session.refresh(run)
    assert run.state == VaultAuditRunState.FAILED
    assert run.error_code == "audit_failed"


def test_execute_run_flags_embedded_and_unowned_blobs(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _make_user(db_session, "exec-owner4")
    run = _make_run(db_session, user)

    snapshot = StorageOwnershipSnapshot(
        discovered_keys={"vault/collection-images/9/pic.png", "vault/stray/orphan.stl"},
    )
    monkeypatch.setattr(vault_audit, "ownership_snapshot", lambda _session: snapshot)
    monkeypatch.setattr(get_backend(), "exists", lambda _key: True)

    vault_audit.execute_run(run.id)

    from sqlmodel import select

    findings = db_session.exec(select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)).all()
    codes = {f.code for f in findings}
    assert "embedded_image_unreferenced" in codes
    assert "unowned_blob_detected" in codes
    db_session.refresh(run)
    assert run.state == VaultAuditRunState.COMPLETED


def test_execute_run_full_mode_runs_backup_check(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import backup

    user = _make_user(db_session, "exec-owner5")
    run = _make_run(db_session, user, VaultAuditMode.FULL)

    monkeypatch.setattr(
        backup,
        "list_backups",
        lambda: [backup.BackupMeta(
            id="b1", created_at="now", size_bytes=1, storage_backend="local",
            file_count=1, app_version="0.0.0", path="b1.tar.gz",
        )],
    )
    monkeypatch.setattr(
        backup,
        "verify_backup",
        lambda _id: backup.BackupVerification(
            backup_id="b1", valid=False, app_compatible=True, manifest_version="1",
            checked_members=1, findings=[{"code": "unexpected_code", "member": "a/b.stl"}],
        ),
    )

    vault_audit.execute_run(run.id)

    from sqlmodel import select

    findings = db_session.exec(select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)).all()
    backup_findings = [f for f in findings if f.resource_type == "backup"]
    assert len(backup_findings) == 1
    # Unrecognized issue codes fall back to the generic manifest-invalid code.
    assert backup_findings[0].code == "backup_manifest_invalid"
    db_session.refresh(run)
    assert run.state == VaultAuditRunState.COMPLETED


# --------------------------------------------------------------------------- #
# ignore_finding / repair_finding
# --------------------------------------------------------------------------- #


def test_ignore_finding_marks_ignored(db_session: Session) -> None:
    user = _make_user(db_session, "ignore-owner")
    run = _make_run(db_session, user)
    finding = VaultAuditFinding(
        run_id=run.id, code="metadata_missing", severity=VaultAuditSeverity.WARNING,
        resource_type="file", resource_identifier="x",
    )
    db_session.add(finding)
    db_session.commit()
    db_session.refresh(finding)

    result = vault_audit.ignore_finding(db_session, finding.id, user.id)

    assert result is not None
    assert result.state == VaultAuditFindingState.IGNORED
    assert result.resolved_by == user.id


def test_ignore_finding_missing_returns_none(db_session: Session) -> None:
    assert vault_audit.ignore_finding(db_session, 999999, 1) is None


def test_repair_finding_already_resolved_is_a_noop(db_session: Session) -> None:
    user = _make_user(db_session, "repair-owner")
    run = _make_run(db_session, user)
    finding = VaultAuditFinding(
        run_id=run.id, code="metadata_missing", severity=VaultAuditSeverity.WARNING,
        resource_type="file", resource_identifier="x", state=VaultAuditFindingState.RESOLVED,
    )
    db_session.add(finding)
    db_session.commit()
    db_session.refresh(finding)

    result = vault_audit.repair_finding(db_session, finding.id, user.id)

    assert result is not None
    assert result.state == VaultAuditFindingState.RESOLVED


def test_repair_finding_missing_returns_none(db_session: Session) -> None:
    assert vault_audit.repair_finding(db_session, 999999, 1) is None


def test_repair_finding_regenerate_thumbnail(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _make_user(db_session, "repair-owner2")
    run = _make_run(db_session, user)
    finding = VaultAuditFinding(
        run_id=run.id, code="thumbnail_missing", severity=VaultAuditSeverity.WARNING,
        resource_type="model", resource_identifier="x", repair_action="regenerate_thumbnail",
        details_json=json.dumps({"model_id": 1}),
    )
    db_session.add(finding)
    db_session.commit()
    db_session.refresh(finding)

    monkeypatch.setattr(vault_audit.thumbnail_repair, "regenerate_model_thumbnail", lambda _s, _id: True)

    result = vault_audit.repair_finding(db_session, finding.id, user.id)

    assert result is not None
    assert result.state == VaultAuditFindingState.RESOLVED


def test_repair_finding_restore_recommended_revision(db_session: Session) -> None:
    user = _make_user(db_session, "repair-owner3")
    run = _make_run(db_session, user)
    model = _make_model(db_session, "repair-rec")
    older = _make_file(db_session, model, file_type=FileType.GCODE, path="v1.gcode", version=1, is_recommended=False)
    newer = _make_file(db_session, model, file_type=FileType.GCODE, path="v2.gcode", version=2, is_recommended=False)
    finding = VaultAuditFinding(
        run_id=run.id, code="recommended_revision_missing", severity=VaultAuditSeverity.WARNING,
        resource_type="model", resource_identifier=model.name,
        repair_action="restore_recommended_revision",
        details_json=json.dumps({"model_id": model.id}),
    )
    db_session.add(finding)
    db_session.commit()
    db_session.refresh(finding)

    result = vault_audit.repair_finding(db_session, finding.id, user.id)

    assert result is not None
    assert result.state == VaultAuditFindingState.RESOLVED
    db_session.refresh(newer)
    db_session.refresh(older)
    assert newer.is_recommended is True
    assert older.is_recommended is False


def test_repair_finding_restore_recommended_revision_no_files_leaves_unresolved(
    db_session: Session,
) -> None:
    user = _make_user(db_session, "repair-owner3b")
    run = _make_run(db_session, user)
    model = _make_model(db_session, "repair-rec-empty")
    finding = VaultAuditFinding(
        run_id=run.id, code="recommended_revision_missing", severity=VaultAuditSeverity.WARNING,
        resource_type="model", resource_identifier=model.name,
        repair_action="restore_recommended_revision",
        details_json=json.dumps({"model_id": model.id}),
    )
    db_session.add(finding)
    db_session.commit()
    db_session.refresh(finding)

    result = vault_audit.repair_finding(db_session, finding.id, user.id)

    assert result is not None
    assert result.state != VaultAuditFindingState.RESOLVED


def test_repair_finding_reparse_metadata(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _make_user(db_session, "repair-owner4")
    run = _make_run(db_session, user)
    model = _make_model(db_session, "repair-meta")
    file_row = _make_file(db_session, model)
    finding = VaultAuditFinding(
        run_id=run.id, code="metadata_missing", severity=VaultAuditSeverity.WARNING,
        resource_type="file", resource_identifier=file_row.original_filename,
        repair_action="reparse_metadata",
        details_json=json.dumps({"file_id": file_row.id}),
    )
    db_session.add(finding)
    db_session.commit()
    db_session.refresh(finding)

    monkeypatch.setattr(vault_audit, "_reparse_metadata", lambda _s, _id: True)

    result = vault_audit.repair_finding(db_session, finding.id, user.id)

    assert result is not None
    assert result.state == VaultAuditFindingState.RESOLVED


def test_repair_finding_retry_pending_import(db_session: Session) -> None:
    user = _make_user(db_session, "repair-owner5")
    run = _make_run(db_session, user)
    item = InboxItem(
        owner_user_id=user.id,
        source_url="https://example.com/x",
        state=InboxItemState.FAILED,
        retryable=True,
        manifest_json="{}",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    finding = VaultAuditFinding(
        run_id=run.id, code="background_job_stuck", severity=VaultAuditSeverity.WARNING,
        resource_type="pending_import", resource_identifier="x",
        repair_action="retry_pending_import",
        details_json=json.dumps({"inbox_item_id": item.id}),
    )
    db_session.add(finding)
    db_session.commit()
    db_session.refresh(finding)

    result = vault_audit.repair_finding(db_session, finding.id, user.id)

    assert result is not None
    assert result.state == VaultAuditFindingState.RESOLVED
    db_session.refresh(item)
    assert item.state == InboxItemState.CAPTURED


def test_repair_finding_rescan_external_library(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import external_library

    user = _make_user(db_session, "repair-owner6")
    run = _make_run(db_session, user)
    finding = VaultAuditFinding(
        run_id=run.id, code="linked_file_missing", severity=VaultAuditSeverity.WARNING,
        resource_type="file", resource_identifier="x",
        repair_action="rescan_external_library",
        details_json=json.dumps({"library_id": 1}),
    )
    db_session.add(finding)
    db_session.commit()
    db_session.refresh(finding)

    monkeypatch.setattr(external_library, "scan_library", lambda _id: {"aborted_unmounted": False})

    result = vault_audit.repair_finding(db_session, finding.id, user.id)

    assert result is not None
    assert result.state == VaultAuditFindingState.RESOLVED


def test_repair_finding_rescan_external_library_aborted_leaves_unresolved(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import external_library

    user = _make_user(db_session, "repair-owner7")
    run = _make_run(db_session, user)
    finding = VaultAuditFinding(
        run_id=run.id, code="linked_file_missing", severity=VaultAuditSeverity.WARNING,
        resource_type="file", resource_identifier="x",
        repair_action="rescan_external_library",
        details_json=json.dumps({"library_id": 1}),
    )
    db_session.add(finding)
    db_session.commit()
    db_session.refresh(finding)

    monkeypatch.setattr(external_library, "scan_library", lambda _id: {"aborted_unmounted": True})

    result = vault_audit.repair_finding(db_session, finding.id, user.id)

    assert result is not None
    assert result.state != VaultAuditFindingState.RESOLVED


def test_restore_recommended_no_files_returns_false(db_session: Session) -> None:
    model = _make_model(db_session, "restore-empty")
    assert vault_audit._restore_recommended(db_session, model.id) is False
