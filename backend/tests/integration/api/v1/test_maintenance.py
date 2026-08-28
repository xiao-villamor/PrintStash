"""The vault audit's control surface: start, watch, cancel, repair, ignore.

An audit walks every owned blob and row looking for damage, so two things matter here.
Only one may run at a time — a second start must join the active run rather than launch
a concurrent walk over the same storage — and a repair must refuse to *claim* it fixed
something it cannot fix: a finding whose repair action is unknown answers 409 rather
than silently marking itself resolved.

Everything on this router is superuser-only; the auth sweep at the bottom covers every
route so a lost dependency cannot go unnoticed.

The audit engine's own behaviour lives in `integration/services/test_vault_audit.py`.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import (
    User,
    VaultAuditFinding,
    VaultAuditFindingState,
    VaultAuditMode,
    VaultAuditRun,
    VaultAuditRunState,
    VaultAuditSeverity,
)
from app.services import vault_audit
from tests.integration.conftest import UserHeaders

# Every route on the router, for the auth sweeps.
ROUTES = [
    pytest.param("post", "/api/v1/maintenance/audits", {"mode": "quick"}, id="start"),
    pytest.param("get", "/api/v1/maintenance/audits", None, id="list"),
    pytest.param("get", "/api/v1/maintenance/audits/latest", None, id="latest"),
    pytest.param("get", "/api/v1/maintenance/audits/1", None, id="get"),
    pytest.param("post", "/api/v1/maintenance/audits/1/cancel", None, id="cancel"),
    pytest.param("post", "/api/v1/maintenance/findings/1/repair", None, id="repair"),
    pytest.param("post", "/api/v1/maintenance/findings/1/ignore", None, id="ignore"),
]


@pytest.fixture
def superuser(db_session: Session) -> User:
    return db_session.exec(select(User)).first()  # type: ignore[return-value]


@pytest.fixture
def make_run(db_session: Session, auth_headers: dict[str, str], superuser: User):
    def build(**overrides: Any) -> VaultAuditRun:
        fields: dict[str, Any] = {
            "requested_by": superuser.id,
            "mode": VaultAuditMode.QUICK,
        }
        fields.update(overrides)
        row = VaultAuditRun(**fields)
        db_session.add(row)
        db_session.commit()
        db_session.refresh(row)
        return row

    return build


@pytest.fixture
def make_finding(db_session: Session):
    def build(run_id: int, **overrides: Any) -> VaultAuditFinding:
        fields: dict[str, Any] = {
            "run_id": run_id,
            "code": "owned_blob_missing",
            "severity": VaultAuditSeverity.CRITICAL,
            "resource_type": "file",
            "resource_identifier": "1",
        }
        fields.update(overrides)
        row = VaultAuditFinding(**fields)
        db_session.add(row)
        db_session.commit()
        db_session.refresh(row)
        return row

    return build


@pytest.fixture
def deferred_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a started run PENDING.

    TestClient runs BackgroundTasks synchronously before the response returns, so the
    real `execute_run` would finish the audit immediately and there would never be an
    active run to deduplicate against.
    """
    monkeypatch.setattr(vault_audit, "execute_run", lambda _run_id: None)


class TestStartAudit:
    def test_accepts_the_request(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        deferred_execution: None,
    ) -> None:
        response = client.post(
            "/api/v1/maintenance/audits", headers=auth_headers, json={"mode": "quick"}
        )

        assert response.status_code == 202, response.text
        assert response.json()["mode"] == "quick"

    def test_persists_the_run(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        deferred_execution: None,
    ) -> None:
        run_id = client.post(
            "/api/v1/maintenance/audits", headers=auth_headers, json={"mode": "quick"}
        ).json()["id"]

        assert db_session.get(VaultAuditRun, run_id) is not None

    def test_joins_the_active_run_instead_of_starting_a_second(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        deferred_execution: None,
    ) -> None:
        first = client.post(
            "/api/v1/maintenance/audits", headers=auth_headers, json={"mode": "quick"}
        ).json()["id"]

        again = client.post(
            "/api/v1/maintenance/audits", headers=auth_headers, json={"mode": "full"}
        )

        assert again.status_code == 202, again.text
        assert again.json()["id"] == first, (
            "two concurrent walks over the same storage is the thing to avoid"
        )

    def test_runs_the_audit_in_the_background(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        # Without the stand-in, TestClient runs the background task inline, so a
        # completed run proves it was scheduled at all.
        response = client.post(
            "/api/v1/maintenance/audits", headers=auth_headers, json={"mode": "quick"}
        )

        run_id = response.json()["id"]
        follow_up = client.get(
            f"/api/v1/maintenance/audits/{run_id}", headers=auth_headers
        )
        assert follow_up.json()["state"] == VaultAuditRunState.COMPLETED.value

    def test_rejects_an_unknown_mode(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.post(
            "/api/v1/maintenance/audits",
            headers=auth_headers,
            json={"mode": "exhaustive"},
        )

        assert response.status_code == 422, response.text

    @pytest.mark.parametrize("mode", list(VaultAuditMode), ids=lambda m: m.value)
    def test_accepts_every_mode(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        deferred_execution: None,
        mode: VaultAuditMode,
    ) -> None:
        response = client.post(
            "/api/v1/maintenance/audits",
            headers=auth_headers,
            json={"mode": mode.value},
        )

        assert response.status_code == 202, response.text


class TestListAudits:
    def test_lists_the_stored_runs(
        self, client: TestClient, auth_headers: dict[str, str], make_run
    ) -> None:
        run = make_run(state=VaultAuditRunState.COMPLETED)

        response = client.get("/api/v1/maintenance/audits", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert run.id in [row["id"] for row in response.json()]

    def test_returns_an_empty_list_with_no_runs(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        assert (
            client.get("/api/v1/maintenance/audits", headers=auth_headers).json() == []
        )

    def test_honours_the_limit(
        self, client: TestClient, auth_headers: dict[str, str], make_run
    ) -> None:
        for _ in range(3):
            make_run(state=VaultAuditRunState.COMPLETED)

        response = client.get(
            "/api/v1/maintenance/audits?limit=2", headers=auth_headers
        )

        assert len(response.json()) == 2

    @pytest.mark.parametrize(
        "limit", [pytest.param(0, id="below-min"), pytest.param(101, id="above-max")]
    )
    def test_rejects_a_limit_outside_its_bounds(
        self, client: TestClient, auth_headers: dict[str, str], limit: int
    ) -> None:
        response = client.get(
            f"/api/v1/maintenance/audits?limit={limit}", headers=auth_headers
        )

        assert response.status_code == 422, response.text


class TestLatestAudit:
    def test_returns_the_most_recent_run(
        self, client: TestClient, auth_headers: dict[str, str], make_run
    ) -> None:
        make_run(state=VaultAuditRunState.COMPLETED)
        newest = make_run(state=VaultAuditRunState.COMPLETED)

        response = client.get("/api/v1/maintenance/audits/latest", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json()["id"] == newest.id

    def test_reports_no_run_as_not_found(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.get("/api/v1/maintenance/audits/latest", headers=auth_headers)

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "audit_not_found"


class TestGetAudit:
    def test_returns_the_run(
        self, client: TestClient, auth_headers: dict[str, str], make_run
    ) -> None:
        run = make_run()

        response = client.get(
            f"/api/v1/maintenance/audits/{run.id}", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert response.json()["id"] == run.id

    def test_includes_the_runs_findings(
        self, client: TestClient, auth_headers: dict[str, str], make_run, make_finding
    ) -> None:
        run = make_run()
        finding = make_finding(run.id)

        body = client.get(
            f"/api/v1/maintenance/audits/{run.id}", headers=auth_headers
        ).json()

        assert [item["id"] for item in body["findings"]] == [finding.id]

    def test_reports_an_unknown_run_as_not_found(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.get("/api/v1/maintenance/audits/99999", headers=auth_headers)

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "audit_not_found"


class TestCancelAudit:
    def test_marks_the_run_cancel_requested(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        make_run,
    ) -> None:
        run = make_run(state=VaultAuditRunState.RUNNING)

        response = client.post(
            f"/api/v1/maintenance/audits/{run.id}/cancel", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        db_session.expire_all()
        assert db_session.get(VaultAuditRun, run.id).cancel_requested is True

    def test_reports_an_unknown_run_as_not_found(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.post(
            "/api/v1/maintenance/audits/99999/cancel", headers=auth_headers
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "audit_not_found"


class TestRepairFinding:
    def test_reports_an_already_resolved_finding_as_resolved(
        self, client: TestClient, auth_headers: dict[str, str], make_run, make_finding
    ) -> None:
        run = make_run()
        finding = make_finding(run.id, state=VaultAuditFindingState.RESOLVED)

        response = client.post(
            f"/api/v1/maintenance/findings/{finding.id}/repair", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert response.json()["state"] == "resolved"

    def test_refuses_a_finding_it_cannot_repair(
        self, client: TestClient, auth_headers: dict[str, str], make_run, make_finding
    ) -> None:
        run = make_run()
        finding = make_finding(run.id, repair_action="no_such_action")

        response = client.post(
            f"/api/v1/maintenance/findings/{finding.id}/repair", headers=auth_headers
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "audit_repair_not_available"

    def test_leaves_an_unrepairable_finding_open(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        make_run,
        make_finding,
    ) -> None:
        run = make_run()
        finding = make_finding(run.id, repair_action="no_such_action")

        client.post(
            f"/api/v1/maintenance/findings/{finding.id}/repair", headers=auth_headers
        )

        db_session.expire_all()
        assert (
            db_session.get(VaultAuditFinding, finding.id).state
            != VaultAuditFindingState.RESOLVED
        ), "a refused repair must not claim the damage is gone"

    def test_reports_an_unknown_finding_as_not_found(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.post(
            "/api/v1/maintenance/findings/99999/repair", headers=auth_headers
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "audit_finding_not_found"


class TestIgnoreFinding:
    def test_marks_the_finding_ignored(
        self, client: TestClient, auth_headers: dict[str, str], make_run, make_finding
    ) -> None:
        run = make_run()
        finding = make_finding(run.id)

        response = client.post(
            f"/api/v1/maintenance/findings/{finding.id}/ignore", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert response.json()["state"] == "ignored"

    def test_records_who_ignored_it(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        superuser: User,
        make_run,
        make_finding,
    ) -> None:
        run = make_run()
        finding = make_finding(run.id)

        client.post(
            f"/api/v1/maintenance/findings/{finding.id}/ignore", headers=auth_headers
        )

        db_session.expire_all()
        assert db_session.get(VaultAuditFinding, finding.id).resolved_by == superuser.id

    def test_reports_an_unknown_finding_as_not_found(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.post(
            "/api/v1/maintenance/findings/99999/ignore", headers=auth_headers
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "audit_finding_not_found"


class TestAuthorisation:
    @pytest.mark.parametrize(("method", "path", "payload"), ROUTES)
    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, method: str, path: str, payload: dict | None
    ) -> None:
        kwargs = {"json": payload} if payload is not None else {}

        response = getattr(client, method)(path, **kwargs)

        assert response.status_code == 401, response.text

    @pytest.mark.parametrize(("method", "path", "payload"), ROUTES)
    def test_rejects_a_non_superuser(
        self,
        client: TestClient,
        user_headers: UserHeaders,
        method: str,
        path: str,
        payload: dict | None,
    ) -> None:
        headers = user_headers(f"operator-{path.replace('/', '-')}")
        kwargs = {"json": payload} if payload is not None else {}

        response = getattr(client, method)(path, headers=headers, **kwargs)

        assert response.status_code == 403, response.text
