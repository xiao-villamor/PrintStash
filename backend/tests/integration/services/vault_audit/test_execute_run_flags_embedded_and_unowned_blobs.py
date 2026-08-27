"""Defends execute run flags embedded and unowned blobs at the services vault audit integration boundary.

A regression could miss corruption or repair ownership and metadata incorrectly.
"""

from __future__ import annotations

from ._vault_audit_internals_shared import (
    Session,
    StorageOwnershipSnapshot,
    VaultAuditFinding,
    VaultAuditFindingState,
    VaultAuditMode,
    VaultAuditRunState,
    VaultAuditSeverity,
    _make_run,
    _make_user,
    get_backend,
    json,
    pytest,
    vault_audit,
)


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

    findings = db_session.exec(
        select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
    ).all()
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
    monkeypatch.setattr(
        backup,
        "verify_backup",
        lambda _id: backup.BackupVerification(
            backup_id="b1",
            valid=False,
            app_compatible=True,
            manifest_version="1",
            checked_members=1,
            findings=[{"code": "unexpected_code", "member": "a/b.stl"}],
        ),
    )

    vault_audit.execute_run(run.id)

    from sqlmodel import select

    findings = db_session.exec(
        select(VaultAuditFinding).where(VaultAuditFinding.run_id == run.id)
    ).all()
    backup_findings = [f for f in findings if f.resource_type == "backup"]
    assert len(backup_findings) == 1
    # Unrecognized issue codes fall back to the generic manifest-invalid code.
    assert backup_findings[0].code == "backup_manifest_invalid"
    db_session.refresh(run)
    assert run.state == VaultAuditRunState.COMPLETED


def test_ignore_finding_marks_ignored(db_session: Session) -> None:
    user = _make_user(db_session, "ignore-owner")
    run = _make_run(db_session, user)
    finding = VaultAuditFinding(
        run_id=run.id,
        code="metadata_missing",
        severity=VaultAuditSeverity.WARNING,
        resource_type="file",
        resource_identifier="x",
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
        run_id=run.id,
        code="metadata_missing",
        severity=VaultAuditSeverity.WARNING,
        resource_type="file",
        resource_identifier="x",
        state=VaultAuditFindingState.RESOLVED,
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
        run_id=run.id,
        code="thumbnail_missing",
        severity=VaultAuditSeverity.WARNING,
        resource_type="model",
        resource_identifier="x",
        repair_action="regenerate_thumbnail",
        details_json=json.dumps({"model_id": 1}),
    )
    db_session.add(finding)
    db_session.commit()
    db_session.refresh(finding)

    monkeypatch.setattr(
        vault_audit.thumbnail_repair, "regenerate_model_thumbnail", lambda _s, _id: True
    )

    result = vault_audit.repair_finding(db_session, finding.id, user.id)

    assert result is not None
    assert result.state == VaultAuditFindingState.RESOLVED
