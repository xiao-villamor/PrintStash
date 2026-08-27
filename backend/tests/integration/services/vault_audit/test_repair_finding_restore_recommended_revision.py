"""Defends repair finding restore recommended revision at the services vault audit integration boundary.

A regression could miss corruption or repair ownership and metadata incorrectly.
"""

from __future__ import annotations

from ._vault_audit_internals_shared import (
    FileType,
    InboxItem,
    InboxItemState,
    OwnedBlob,
    Session,
    StorageOwnershipSnapshot,
    VaultAuditFinding,
    VaultAuditFindingState,
    VaultAuditMode,
    VaultAuditRunState,
    VaultAuditSeverity,
    _make_file,
    _make_model,
    _make_run,
    _make_user,
    get_backend,
    json,
    pytest,
    vault_audit,
)


def test_repair_finding_restore_recommended_revision(db_session: Session) -> None:
    user = _make_user(db_session, "repair-owner3")
    run = _make_run(db_session, user)
    model = _make_model(db_session, "repair-rec")
    older = _make_file(
        db_session,
        model,
        file_type=FileType.GCODE,
        path="v1.gcode",
        version=1,
        is_recommended=False,
    )
    newer = _make_file(
        db_session,
        model,
        file_type=FileType.GCODE,
        path="v2.gcode",
        version=2,
        is_recommended=False,
    )
    finding = VaultAuditFinding(
        run_id=run.id,
        code="recommended_revision_missing",
        severity=VaultAuditSeverity.WARNING,
        resource_type="model",
        resource_identifier=model.name,
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
        run_id=run.id,
        code="recommended_revision_missing",
        severity=VaultAuditSeverity.WARNING,
        resource_type="model",
        resource_identifier=model.name,
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
        run_id=run.id,
        code="metadata_missing",
        severity=VaultAuditSeverity.WARNING,
        resource_type="file",
        resource_identifier=file_row.original_filename,
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
        run_id=run.id,
        code="background_job_stuck",
        severity=VaultAuditSeverity.WARNING,
        resource_type="pending_import",
        resource_identifier="x",
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
        run_id=run.id,
        code="linked_file_missing",
        severity=VaultAuditSeverity.WARNING,
        resource_type="file",
        resource_identifier="x",
        repair_action="rescan_external_library",
        details_json=json.dumps({"library_id": 1}),
    )
    db_session.add(finding)
    db_session.commit()
    db_session.refresh(finding)

    monkeypatch.setattr(
        external_library, "scan_library", lambda _id: {"aborted_unmounted": False}
    )

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
        run_id=run.id,
        code="linked_file_missing",
        severity=VaultAuditSeverity.WARNING,
        resource_type="file",
        resource_identifier="x",
        repair_action="rescan_external_library",
        details_json=json.dumps({"library_id": 1}),
    )
    db_session.add(finding)
    db_session.commit()
    db_session.refresh(finding)

    monkeypatch.setattr(
        external_library, "scan_library", lambda _id: {"aborted_unmounted": True}
    )

    result = vault_audit.repair_finding(db_session, finding.id, user.id)

    assert result is not None
    assert result.state != VaultAuditFindingState.RESOLVED


def test_restore_recommended_no_files_returns_false(db_session: Session) -> None:
    model = _make_model(db_session, "restore-empty")
    assert vault_audit._restore_recommended(db_session, model.id) is False


def test_details_malformed_json_returns_empty_dict() -> None:
    finding = VaultAuditFinding(
        run_id=1,
        code="x",
        severity=VaultAuditSeverity.INFO,
        resource_type="file",
        resource_identifier="x",
        details_json="{not valid json",
    )
    assert vault_audit._details(finding) == {}


def test_check_database_thumbnail_exists_check_raises_marks_missing(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _make_user(db_session, "db-owner6")
    run = _make_run(db_session, user)
    model = _make_model(db_session, "thumb-exists-boom")
    file_row = _make_file(db_session, model)
    model.thumbnail_file_id = file_row.id
    db_session.add(model)
    db_session.commit()

    def boom(_key: str) -> bool:
        raise RuntimeError("storage backend unavailable")

    monkeypatch.setattr(get_backend(), "exists", boom)

    vault_audit._check_database(db_session, run)

    from sqlmodel import select

    findings = db_session.exec(
        select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
    ).all()
    assert any(f.code == "thumbnail_missing" for f in findings)


def test_check_database_flags_unreadable_thumbnail(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _make_user(db_session, "db-owner7")
    run = _make_run(db_session, user)
    model = _make_model(db_session, "thumb-unreadable")
    file_row = _make_file(db_session, model)
    model.thumbnail_file_id = file_row.id
    db_session.add(model)
    db_session.commit()

    # Thumbnail "exists" but is not a valid image — verify() raises.
    monkeypatch.setattr(get_backend(), "exists", lambda _key: True)

    class _BoomImage:
        def __enter__(self):
            raise OSError("truncated image")

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr("PIL.Image.open", lambda _path: _BoomImage())

    vault_audit._check_database(db_session, run)

    from sqlmodel import select

    findings = db_session.exec(
        select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
    ).all()
    assert any(f.code == "thumbnail_unreadable" for f in findings)


def test_check_external_stat_raises_marks_unavailable(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    user = _make_user(db_session, "ext-owner3")
    run = _make_run(db_session, user)
    model = _make_model(db_session, "ext-stat-boom")
    linked = tmp_path / "linked.stl"
    linked.write_text("x")
    file_row = _make_file(
        db_session, model, path=str(linked), is_external=True, external_library_id=None
    )

    from pathlib import Path as _Path

    original_stat = _Path.stat

    def boom_stat(self, *args, **kwargs):
        if self == linked:
            raise OSError("permission denied")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(_Path, "stat", boom_stat)

    vault_audit._check_external(
        db_session,
        run,
        [
            OwnedBlob(
                key=str(linked),
                resource_type="file",
                resource_id=file_row.id,
                display_name="linked.stl",
            )
        ],
    )

    from sqlmodel import select

    findings = db_session.exec(
        select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
    ).all()
    assert any(f.code == "linked_file_missing" for f in findings)


def test_check_backups_stops_when_cancelled(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import backup

    user = _make_user(db_session, "backup-owner")
    run = _make_run(db_session, user, VaultAuditMode.FULL)
    run.cancel_requested = True
    db_session.add(run)
    db_session.commit()

    called = {"verify": False}
    monkeypatch.setattr(
        backup,
        "list_backups",
        lambda: [
            backup.BackupMeta(
                id="b1",
                created_at="now",
                size_bytes=1,
                storage_backend="local",
                file_count=1,
                app_version="0.0.0",
                path="b1.tar.gz",
            )
        ],
    )

    def fake_verify(_id):
        called["verify"] = True
        raise AssertionError("verify_backup must not run once cancelled")

    monkeypatch.setattr(backup, "verify_backup", fake_verify)

    vault_audit._check_backups(db_session, run)

    assert called["verify"] is False
    db_session.refresh(run)
    assert run.state == VaultAuditRunState.CANCELLED


def test_execute_run_cancelled_between_external_and_database_checks(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _make_user(db_session, "exec-owner6")
    run = _make_run(db_session, user)
    run.cancel_requested = True
    db_session.add(run)
    db_session.commit()

    monkeypatch.setattr(
        vault_audit, "ownership_snapshot", lambda _session: StorageOwnershipSnapshot()
    )

    vault_audit.execute_run(run.id)

    db_session.refresh(run)
    assert run.state == VaultAuditRunState.CANCELLED


def test_execute_run_returns_when_database_check_cancels(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _make_user(db_session, "exec-owner7")
    run = _make_run(db_session, user)

    monkeypatch.setattr(
        vault_audit, "ownership_snapshot", lambda _session: StorageOwnershipSnapshot()
    )

    def fake_check_database(session, run_arg):
        run_arg.state = VaultAuditRunState.CANCELLED
        session.add(run_arg)
        session.commit()

    monkeypatch.setattr(vault_audit, "_check_database", fake_check_database)

    vault_audit.execute_run(run.id)

    db_session.refresh(run)
    assert run.state == VaultAuditRunState.CANCELLED
    assert run.current_phase != "completed"


def test_execute_run_flags_missing_embedded_image(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _make_user(db_session, "exec-owner8")
    run = _make_run(db_session, user)

    blob = OwnedBlob(
        key="vault/collection-images/1/pic.png",
        resource_type="collection_image",
        resource_id=1,
    )
    snapshot = StorageOwnershipSnapshot(embedded=[blob])
    monkeypatch.setattr(vault_audit, "ownership_snapshot", lambda _session: snapshot)
    monkeypatch.setattr(get_backend(), "exists", lambda _key: False)

    vault_audit.execute_run(run.id)

    from sqlmodel import select

    findings = db_session.exec(
        select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
    ).all()
    assert any(f.code == "embedded_image_missing" for f in findings)
    db_session.refresh(run)
    assert run.state == VaultAuditRunState.COMPLETED


def test_execute_run_full_mode_returns_when_backup_check_cancels(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _make_user(db_session, "exec-owner9")
    run = _make_run(db_session, user, VaultAuditMode.FULL)

    monkeypatch.setattr(
        vault_audit, "ownership_snapshot", lambda _session: StorageOwnershipSnapshot()
    )

    def fake_check_backups(session, run_arg):
        run_arg.state = VaultAuditRunState.CANCELLED
        session.add(run_arg)
        session.commit()

    monkeypatch.setattr(vault_audit, "_check_backups", fake_check_backups)

    vault_audit.execute_run(run.id)

    db_session.refresh(run)
    assert run.state == VaultAuditRunState.CANCELLED
    assert run.finished_at is None
