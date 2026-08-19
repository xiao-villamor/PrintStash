from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import (
    File,
    FileType,
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


def test_quick_audit_persists_missing_owned_blob_finding(db_session: Session) -> None:
    user = User(username="auditor", hashed_password="x", is_superuser=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    model = Model(name="Missing", slug="missing", hash="a" * 64)
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)
    db_session.add(
        File(
            model_id=model.id,
            path="definitely-missing.stl",
            original_filename="missing.stl",
            file_type=FileType.STL,
            size_bytes=12,
            sha256="b" * 64,
        )
    )
    db_session.commit()

    run, created = vault_audit.create_run(db_session, user.id, VaultAuditMode.QUICK)
    assert created is True
    vault_audit.execute_run(run.id)
    db_session.expire_all()

    result = vault_audit.read_run(db_session, db_session.get(type(run), run.id))
    assert result.state == VaultAuditRunState.COMPLETED
    assert any(item.code == "owned_blob_missing" for item in result.findings)
    assert all("/" not in item.resource_identifier for item in result.findings)


def test_read_run_counts_actual_findings_instead_of_stale_run_totals(
    db_session: Session,
) -> None:
    user = User(username="count-auditor", hashed_password="x", is_superuser=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    run = VaultAuditRun(
        requested_by=user.id,
        mode=VaultAuditMode.QUICK,
        critical_count=0,
        warning_count=99,
        info_count=7,
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    db_session.add_all(
        [
            VaultAuditFinding(
                run_id=run.id,
                code="owned_blob_missing",
                severity=VaultAuditSeverity.CRITICAL,
                resource_type="file",
                resource_identifier="one.stl",
            ),
            VaultAuditFinding(
                run_id=run.id,
                code="owned_blob_missing",
                severity=VaultAuditSeverity.CRITICAL,
                resource_type="file",
                resource_identifier="two.stl",
            ),
            VaultAuditFinding(
                run_id=run.id,
                code="metadata_missing",
                severity=VaultAuditSeverity.WARNING,
                resource_type="file",
                resource_identifier="three.gcode",
            ),
        ]
    )
    db_session.commit()

    result = vault_audit.read_run(db_session, run)
    summary = vault_audit.read_run(db_session, run, findings=False)

    assert result.critical_count == 2
    assert result.warning_count == 1
    assert result.info_count == 0
    assert summary.critical_count == 2
    assert summary.warning_count == 1
    assert summary.info_count == 0
    assert summary.findings == []


def test_concurrent_audit_start_returns_active_run(db_session: Session) -> None:
    user = User(username="auditor-2", hashed_password="x", is_superuser=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    first, created = vault_audit.create_run(db_session, user.id, VaultAuditMode.QUICK)
    second, duplicate_created = vault_audit.create_run(
        db_session, user.id, VaultAuditMode.FULL
    )
    assert created is True
    assert duplicate_created is False
    assert second.id == first.id


# --------------------------------------------------------------------------- #
# API route layer (app/api/v1/maintenance.py)
# --------------------------------------------------------------------------- #


def _run(db_session: Session, requested_by: int, **overrides) -> VaultAuditRun:
    defaults = dict(requested_by=requested_by, mode=VaultAuditMode.QUICK)
    defaults.update(overrides)
    row = VaultAuditRun(**defaults)
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _finding(db_session: Session, run_id: int, **overrides) -> VaultAuditFinding:
    defaults = dict(
        run_id=run_id,
        code="owned_blob_missing",
        severity=VaultAuditSeverity.CRITICAL,
        resource_type="file",
        resource_identifier="1",
    )
    defaults.update(overrides)
    row = VaultAuditFinding(**defaults)
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def test_start_audit_returns_202_and_reuses_active_run(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    # TestClient runs BackgroundTasks synchronously before the response
    # returns, so the real execute_run would finish the run immediately and
    # defeat the dedup check below. Stub it out to keep the run PENDING.
    monkeypatch.setattr(vault_audit, "execute_run", lambda _run_id: None)

    started = client.post(
        "/api/v1/maintenance/audits", headers=auth_headers, json={"mode": "quick"}
    )
    assert started.status_code == 202
    run_id = started.json()["id"]

    again = client.post(
        "/api/v1/maintenance/audits", headers=auth_headers, json={"mode": "full"}
    )
    assert again.status_code == 202
    # An active run already exists, so the second call reuses it rather than
    # starting a concurrent audit.
    assert again.json()["id"] == run_id


def test_list_audits_returns_created_runs(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    user = db_session.exec(select(User)).first()
    _run(db_session, user.id, state=VaultAuditRunState.COMPLETED)

    response = client.get("/api/v1/maintenance/audits?limit=5", headers=auth_headers)

    assert response.status_code == 200
    assert len(response.json()) >= 1


def test_latest_audit_404_when_none_exist(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/maintenance/audits/latest", headers=auth_headers)
    assert response.status_code == 404
    assert response.json()["detail"] == "audit_not_found"


def test_latest_audit_returns_most_recent_run(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    user = db_session.exec(select(User)).first()
    run = _run(db_session, user.id, state=VaultAuditRunState.COMPLETED)

    response = client.get("/api/v1/maintenance/audits/latest", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["id"] == run.id


def test_get_audit_404_for_missing_run(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/maintenance/audits/99999", headers=auth_headers)
    assert response.status_code == 404
    assert response.json()["detail"] == "audit_not_found"


def test_get_audit_returns_run(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    user = db_session.exec(select(User)).first()
    run = _run(db_session, user.id)

    response = client.get(f"/api/v1/maintenance/audits/{run.id}", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["id"] == run.id


def test_cancel_audit_404_for_missing_run(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/maintenance/audits/99999/cancel", headers=auth_headers
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "audit_not_found"


def test_cancel_audit_marks_active_run(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    user = db_session.exec(select(User)).first()
    run = _run(db_session, user.id, state=VaultAuditRunState.RUNNING)

    response = client.post(
        f"/api/v1/maintenance/audits/{run.id}/cancel", headers=auth_headers
    )

    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.get(VaultAuditRun, run.id).cancel_requested is True


def test_repair_finding_404_for_missing_finding(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/maintenance/findings/99999/repair", headers=auth_headers
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "audit_finding_not_found"


def test_repair_finding_409_when_repair_action_unsupported(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    user = db_session.exec(select(User)).first()
    run = _run(db_session, user.id)
    finding = _finding(db_session, run.id, repair_action="no_such_action")

    response = client.post(
        f"/api/v1/maintenance/findings/{finding.id}/repair", headers=auth_headers
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "audit_repair_not_available"


def test_repair_finding_returns_already_resolved(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    user = db_session.exec(select(User)).first()
    run = _run(db_session, user.id)
    finding = _finding(
        db_session, run.id, state=VaultAuditFindingState.RESOLVED
    )

    response = client.post(
        f"/api/v1/maintenance/findings/{finding.id}/repair", headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json()["state"] == "resolved"


def test_ignore_finding_404_for_missing_finding(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/maintenance/findings/99999/ignore", headers=auth_headers
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "audit_finding_not_found"


def test_ignore_finding_marks_ignored(
    client: TestClient, auth_headers: dict[str, str], db_session: Session
) -> None:
    user = db_session.exec(select(User)).first()
    run = _run(db_session, user.id)
    finding = _finding(db_session, run.id)

    response = client.post(
        f"/api/v1/maintenance/findings/{finding.id}/ignore", headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json()["state"] == "ignored"
