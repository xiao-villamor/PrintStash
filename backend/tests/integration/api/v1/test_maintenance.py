"""Maintenance routes keep Vault audits superuser-only, durable, and explicit."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from app.db.models import (
    AuditLog,
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
from app.services.auth import create_access_token, hash_password


def _ordinary_headers(db_session: Session, username: str) -> dict[str, str]:
    user = User(
        username=username,
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=False,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token(user.id, user.username, scope="write")
    return {"Authorization": f"Bearer {token}"}


def _requester(db_session: Session) -> User:
    user = User(username="audit-requester", hashed_password="x", is_superuser=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _run(db_session: Session, requested_by: int, **overrides) -> VaultAuditRun:
    values = {"requested_by": requested_by, "mode": VaultAuditMode.QUICK}
    values.update(overrides)
    row = VaultAuditRun(**values)
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _finding(db_session: Session, run_id: int, **overrides) -> VaultAuditFinding:
    values = {
        "run_id": run_id,
        "code": "owned_blob_missing",
        "severity": VaultAuditSeverity.CRITICAL,
        "resource_type": "file",
        "resource_identifier": "artifact-1",
    }
    values.update(overrides)
    row = VaultAuditFinding(**values)
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


@pytest.fixture(params=["missing", "ordinary"], ids=str)
def unauthorized_headers(request, db_session: Session) -> dict[str, str]:
    if request.param == "ordinary":
        return _ordinary_headers(db_session, f"ordinary-{request.node.name}")
    return {}


class TestStartAudit:
    def test_starts_a_quick_audit(self, client, auth_headers, db_session):
        response = client.post(
            "/api/v1/maintenance/audits", headers=auth_headers, json={"mode": "quick"}
        )

        assert response.status_code == 202, response.text
        assert response.json()["mode"] == "quick"
        assert db_session.get(VaultAuditRun, response.json()["id"]) is not None

    def test_starts_a_full_audit(self, client, auth_headers):
        response = client.post(
            "/api/v1/maintenance/audits", headers=auth_headers, json={"mode": "full"}
        )

        assert response.status_code == 202, response.text
        assert response.json()["mode"] == "full"

    def test_defaults_a_missing_audit_mode_to_quick(self, client, auth_headers):
        response = client.post(
            "/api/v1/maintenance/audits", headers=auth_headers, json={}
        )

        assert response.status_code == 202, response.text
        assert response.json()["mode"] == "quick"

    def test_returns_the_active_run_for_a_duplicate_start(
        self, client, auth_headers, db_session
    ):
        user = db_session.exec(select(User).where(User.is_superuser.is_(True))).one()
        active = _run(db_session, user.id, state=VaultAuditRunState.RUNNING)

        response = client.post(
            "/api/v1/maintenance/audits", headers=auth_headers, json={"mode": "full"}
        )

        assert response.status_code == 202, response.text
        assert response.json()["id"] == active.id
        assert len(db_session.exec(select(VaultAuditRun)).all()) == 1

    def test_validates_an_unsupported_audit_mode(
        self, client, auth_headers, db_session
    ):
        response = client.post(
            "/api/v1/maintenance/audits", headers=auth_headers, json={"mode": "deep"}
        )

        assert response.status_code == 422, response.text
        assert db_session.exec(select(VaultAuditRun)).first() is None

    def test_rejects_an_unauthenticated_audit_start(self, client, db_session):
        response = client.post("/api/v1/maintenance/audits", json={})

        assert response.status_code == 401, response.text
        assert db_session.exec(select(VaultAuditRun)).first() is None

    def test_rejects_a_non_superuser_audit_start(self, client, db_session):
        headers = _ordinary_headers(db_session, "ordinary-start")

        response = client.post("/api/v1/maintenance/audits", headers=headers, json={})

        assert response.status_code == 403, response.text
        assert db_session.exec(select(VaultAuditRun)).first() is None


class TestListAudits:
    def test_lists_audit_runs_with_persisted_findings_and_counters(
        self, client, auth_headers, db_session
    ):
        user = db_session.exec(select(User).where(User.is_superuser.is_(True))).one()
        run = _run(db_session, user.id, state=VaultAuditRunState.COMPLETED)
        _finding(db_session, run.id)

        response = client.get("/api/v1/maintenance/audits", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json()[0]["id"] == run.id
        assert response.json()[0]["critical_count"] == 1
        assert response.json()[0]["findings"] == []

    def test_returns_an_empty_audit_list_when_no_runs_exist(self, client, auth_headers):
        response = client.get("/api/v1/maintenance/audits", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json() == []

    @pytest.mark.parametrize(
        "limit", [pytest.param(1, id="minimum"), pytest.param(100, id="maximum")]
    )
    def test_honors_the_audit_list_limit_boundaries(
        self, client, auth_headers, db_session, limit
    ):
        user = db_session.exec(select(User).where(User.is_superuser.is_(True))).one()
        _run(db_session, user.id, state=VaultAuditRunState.COMPLETED)
        _run(db_session, user.id, state=VaultAuditRunState.FAILED)

        response = client.get(
            f"/api/v1/maintenance/audits?limit={limit}", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert len(response.json()) == min(limit, 2)

    @pytest.mark.parametrize(
        "limit",
        [pytest.param(0, id="below-minimum"), pytest.param(101, id="above-maximum")],
    )
    def test_rejects_an_out_of_range_audit_list_limit(
        self, client, auth_headers, limit
    ):
        response = client.get(
            f"/api/v1/maintenance/audits?limit={limit}", headers=auth_headers
        )

        assert response.status_code == 422, response.text

    def test_rejects_an_unauthenticated_audit_list(self, client):
        response = client.get("/api/v1/maintenance/audits")

        assert response.status_code == 401, response.text

    def test_rejects_a_non_superuser_audit_list(self, client, db_session):
        headers = _ordinary_headers(db_session, "ordinary-list")

        response = client.get("/api/v1/maintenance/audits", headers=headers)

        assert response.status_code == 403, response.text


class TestLatestAudit:
    def test_returns_the_latest_audit_run(self, client, auth_headers, db_session):
        user = db_session.exec(select(User).where(User.is_superuser.is_(True))).one()
        _run(db_session, user.id, state=VaultAuditRunState.COMPLETED)
        latest = _run(db_session, user.id, state=VaultAuditRunState.FAILED)

        response = client.get("/api/v1/maintenance/audits/latest", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json()["id"] == latest.id

    def test_returns_not_found_when_no_latest_audit_exists(self, client, auth_headers):
        response = client.get("/api/v1/maintenance/audits/latest", headers=auth_headers)

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "audit_not_found"

    def test_rejects_unauthorized_latest_audit_reads(
        self, client, unauthorized_headers
    ):
        response = client.get(
            "/api/v1/maintenance/audits/latest", headers=unauthorized_headers
        )

        assert response.status_code in {401, 403}, response.text


class TestGetAudit:
    def test_returns_an_audit_run_by_id(self, client, auth_headers, db_session):
        user = db_session.exec(select(User).where(User.is_superuser.is_(True))).one()
        run = _run(
            db_session, user.id, state=VaultAuditRunState.COMPLETED, progress=100
        )
        finding = _finding(db_session, run.id)

        response = client.get(
            f"/api/v1/maintenance/audits/{run.id}", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert response.json()["progress"] == 100
        assert [item["id"] for item in response.json()["findings"]] == [finding.id]

    def test_returns_not_found_for_a_missing_audit_run(self, client, auth_headers):
        response = client.get("/api/v1/maintenance/audits/999999", headers=auth_headers)

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "audit_not_found"

    def test_rejects_unauthorized_audit_detail_reads(
        self, client, unauthorized_headers
    ):
        response = client.get(
            "/api/v1/maintenance/audits/1", headers=unauthorized_headers
        )

        assert response.status_code in {401, 403}, response.text


class TestCancelAudit:
    def test_requests_cancellation_for_an_active_audit(
        self, client, auth_headers, db_session
    ):
        user = db_session.exec(select(User).where(User.is_superuser.is_(True))).one()
        run = _run(db_session, user.id, state=VaultAuditRunState.RUNNING)

        response = client.post(
            f"/api/v1/maintenance/audits/{run.id}/cancel", headers=auth_headers
        )

        db_session.expire_all()
        assert response.status_code == 200, response.text
        assert db_session.get(VaultAuditRun, run.id).cancel_requested is True

    def test_handles_repeated_cancellation_idempotently(
        self, client, auth_headers, db_session
    ):
        user = db_session.exec(select(User).where(User.is_superuser.is_(True))).one()
        run = _run(
            db_session, user.id, state=VaultAuditRunState.RUNNING, cancel_requested=True
        )

        response = client.post(
            f"/api/v1/maintenance/audits/{run.id}/cancel", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert response.json()["state"] == "running"

    def test_leaves_a_terminal_run_unchanged_when_cancelled(
        self, client, auth_headers, db_session
    ):
        user = db_session.exec(select(User).where(User.is_superuser.is_(True))).one()
        run = _run(db_session, user.id, state=VaultAuditRunState.COMPLETED)

        response = client.post(
            f"/api/v1/maintenance/audits/{run.id}/cancel", headers=auth_headers
        )

        db_session.expire_all()
        assert response.status_code == 200, response.text
        assert db_session.get(VaultAuditRun, run.id).cancel_requested is False

    def test_returns_not_found_when_cancelling_a_missing_audit(
        self, client, auth_headers
    ):
        response = client.post(
            "/api/v1/maintenance/audits/999999/cancel", headers=auth_headers
        )

        assert response.status_code == 404, response.text

    def test_rejects_unauthorized_audit_cancellation(
        self, client, unauthorized_headers, db_session
    ):
        user = _requester(db_session)
        run = _run(db_session, user.id, state=VaultAuditRunState.RUNNING)

        response = client.post(
            f"/api/v1/maintenance/audits/{run.id}/cancel", headers=unauthorized_headers
        )

        db_session.expire_all()
        assert response.status_code in {401, 403}, response.text
        assert db_session.get(VaultAuditRun, run.id).cancel_requested is False


class TestRepairFinding:
    def test_repairs_a_repairable_open_finding(self, client, db_session):
        actor_uuid = uuid4()
        actor_id = 2**62 + actor_uuid.int % (2**62 - 1)
        actor = User(
            id=actor_id,
            username=f"maintenance-repair-{actor_uuid.hex}",
            hashed_password=hash_password("obviously-fake-maintenance-password"),
            is_active=True,
            is_superuser=True,
        )
        db_session.add(actor)
        db_session.commit()
        db_session.refresh(actor)
        token = create_access_token(actor.id, actor.username, scope="admin")
        actor_headers = {"Authorization": f"Bearer {token}"}
        run = _run(db_session, actor.id)
        model = Model(name="Repair", slug="repair", hash="a" * 64)
        db_session.add(model)
        db_session.commit()
        db_session.refresh(model)
        file_row = File(
            model_id=model.id,
            path="repair.gcode",
            original_filename="repair.gcode",
            file_type=FileType.GCODE,
            size_bytes=1,
            sha256="b" * 64,
        )
        db_session.add(file_row)
        db_session.commit()
        finding = _finding(
            db_session,
            run.id,
            repair_action="restore_recommended_revision",
            details_json=json.dumps({"model_id": model.id}),
        )
        db_session.add(
            AuditLog(
                actor_id=actor.id,
                action="audit.repair",
                resource_type="vault_audit_finding",
                resource_id=999999,
            )
        )
        db_session.commit()

        response = client.post(
            f"/api/v1/maintenance/findings/{finding.id}/repair", headers=actor_headers
        )

        db_session.expire_all()
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "resolved"
        assert db_session.get(File, file_row.id).is_recommended is True
        repair_log = db_session.exec(
            select(AuditLog).where(
                AuditLog.action == "audit.repair",
                AuditLog.resource_type == "vault_audit_finding",
                AuditLog.resource_id == finding.id,
                AuditLog.actor_id == actor.id,
            )
        ).one()
        assert (
            repair_log.action,
            repair_log.resource_type,
            repair_log.resource_id,
            repair_log.actor_id,
        ) == ("audit.repair", "vault_audit_finding", finding.id, actor.id)

    def test_handles_a_repeated_finding_repair_idempotently(
        self, client, auth_headers, db_session
    ):
        user = db_session.exec(select(User).where(User.is_superuser.is_(True))).one()
        run = _run(db_session, user.id)
        finding = _finding(db_session, run.id, state=VaultAuditFindingState.RESOLVED)

        response = client.post(
            f"/api/v1/maintenance/findings/{finding.id}/repair", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert response.json()["state"] == "resolved"

    def test_rejects_repair_for_a_non_repairable_finding(
        self, client, auth_headers, db_session
    ):
        user = db_session.exec(select(User).where(User.is_superuser.is_(True))).one()
        run = _run(db_session, user.id)
        finding = _finding(db_session, run.id, repair_action="no_such_action")

        response = client.post(
            f"/api/v1/maintenance/findings/{finding.id}/repair", headers=auth_headers
        )

        db_session.expire_all()
        assert response.status_code == 409, response.text
        assert (
            db_session.get(VaultAuditFinding, finding.id).state
            == VaultAuditFindingState.OPEN
        )

    def test_returns_not_found_when_repairing_a_missing_finding(
        self, client, auth_headers
    ):
        response = client.post(
            "/api/v1/maintenance/findings/999999/repair", headers=auth_headers
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "audit_finding_not_found"

    def test_rejects_unauthorized_finding_repair(
        self, client, unauthorized_headers, db_session
    ):
        user = _requester(db_session)
        run = _run(db_session, user.id)
        finding = _finding(db_session, run.id, repair_action="no_such_action")

        response = client.post(
            f"/api/v1/maintenance/findings/{finding.id}/repair",
            headers=unauthorized_headers,
        )

        db_session.expire_all()
        assert response.status_code in {401, 403}, response.text
        assert (
            db_session.get(VaultAuditFinding, finding.id).state
            == VaultAuditFindingState.OPEN
        )


class TestIgnoreFinding:
    def test_ignores_an_open_finding(self, client, auth_headers, db_session):
        user = db_session.exec(select(User).where(User.is_superuser.is_(True))).one()
        run = _run(db_session, user.id)
        finding = _finding(db_session, run.id)

        response = client.post(
            f"/api/v1/maintenance/findings/{finding.id}/ignore", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert response.json()["state"] == "ignored"
        assert response.json()["resolved_by"] == user.id

    def test_handles_repeated_finding_ignore_idempotently(
        self, client, auth_headers, db_session
    ):
        user = db_session.exec(select(User).where(User.is_superuser.is_(True))).one()
        run = _run(db_session, user.id)
        finding = _finding(db_session, run.id, state=VaultAuditFindingState.IGNORED)

        response = client.post(
            f"/api/v1/maintenance/findings/{finding.id}/ignore", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert response.json()["state"] == "ignored"

    def test_allows_a_resolved_finding_to_be_explicitly_ignored(
        self, client, auth_headers, db_session
    ):
        user = db_session.exec(select(User).where(User.is_superuser.is_(True))).one()
        run = _run(db_session, user.id)
        finding = _finding(db_session, run.id, state=VaultAuditFindingState.RESOLVED)

        response = client.post(
            f"/api/v1/maintenance/findings/{finding.id}/ignore", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert response.json()["state"] == "ignored"

    def test_returns_not_found_when_ignoring_a_missing_finding(
        self, client, auth_headers
    ):
        response = client.post(
            "/api/v1/maintenance/findings/999999/ignore", headers=auth_headers
        )

        assert response.status_code == 404, response.text

    def test_rejects_unauthorized_finding_ignore(
        self, client, unauthorized_headers, db_session
    ):
        user = _requester(db_session)
        run = _run(db_session, user.id)
        finding = _finding(db_session, run.id)

        response = client.post(
            f"/api/v1/maintenance/findings/{finding.id}/ignore",
            headers=unauthorized_headers,
        )

        db_session.expire_all()
        assert response.status_code in {401, 403}, response.text
        assert (
            db_session.get(VaultAuditFinding, finding.id).state
            == VaultAuditFindingState.OPEN
        )
