"""Fleet summary, compatibility, batch, operator, maintenance, and access behaviours."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import (
    OperatorGateState,
    Printer,
    PrinterRole,
    PrinterStatus,
    PrintJob,
    PrintJobState,
    User,
)
from tests.integration.api.v1.fleet._fleet_shared import (
    _gcode,
    _grant_printer,
    _user_headers,
)


def test_fleet_summary_is_scoped_to_accessible_printers(
    client: TestClient, db_session: Session
) -> None:
    visible = Printer(
        name="Visible fleet printer",
        moonraker_url="http://visible-fleet-printer",
        status=PrinterStatus.READY,
    )
    hidden = Printer(
        name="Hidden fleet printer",
        moonraker_url="http://hidden-fleet-printer",
        status=PrinterStatus.READY,
    )
    db_session.add(visible)
    db_session.add(hidden)
    db_session.commit()
    db_session.refresh(visible)
    headers = _user_headers(db_session, "fleet-summary-viewer")
    _grant_printer(db_session, "fleet-summary-viewer", visible, PrinterRole.VIEW)

    response = client.get("/api/v1/fleet/summary", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["total_printers"] == 1
    assert [row["printer_id"] for row in response.json()["printers"]] == [visible.id]


class TestCompatibilityAndBatch:
    def test_reports_unknown_compatibility_as_schedulable(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        printer = Printer(
            name="Unknown compatibility",
            moonraker_url="http://unknown-compatibility",
            status=PrinterStatus.READY,
        )
        db_session.add(printer)
        db_session.commit()
        db_session.refresh(printer)
        artifact = _gcode(db_session)

        response = client.post(
            "/api/v1/fleet/compatibility",
            headers=auth_headers,
            json={"file_id": artifact.id, "printer_ids": [printer.id]},
        )

        assert response.status_code == 200, response.text
        assert response.json()["printers"][0]["printer_id"] == printer.id
        assert response.json()["printers"][0]["verdict"] == "unknown"

    def test_creates_an_atomic_multi_copy_batch(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        printer = Printer(
            name="Batch printer",
            moonraker_url="http://batch-printer",
            status=PrinterStatus.READY,
        )
        db_session.add(printer)
        db_session.commit()
        artifact = _gcode(db_session)

        response = client.post(
            "/api/v1/fleet/batches",
            headers=auth_headers,
            json={"file_id": artifact.id, "quantity": 3, "strategy": "least_busy"},
        )

        assert response.status_code == 201, response.text
        assert len(response.json()["jobs"]) == 3
        batch_id = response.json()["id"]
        assert all(job["batch_id"] == batch_id for job in response.json()["jobs"])

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({"file_id": 1, "quantity": 0}, id="zero-quantity"),
            pytest.param(
                {"file_id": 1, "quantity": 1, "strategy": "manual"},
                id="manual-without-printer",
            ),
        ],
    )
    def test_rejects_invalid_batch_without_partial_jobs(
        self,
        payload: dict,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        response = client.post(
            "/api/v1/fleet/batches", headers=auth_headers, json=payload
        )

        assert response.status_code == 422, response.text
        assert db_session.exec(select(PrintJob)).all() == []


class TestOperatorDecision:
    @staticmethod
    def _pending_job(db_session: Session) -> PrintJob:
        user = db_session.exec(select(User).where(User.username == "test-writer")).one()
        printer = Printer(
            name="Release printer",
            moonraker_url="http://release-printer",
            status=PrinterStatus.READY,
        )
        db_session.add(printer)
        db_session.commit()
        db_session.refresh(printer)
        artifact = _gcode(db_session)
        job = PrintJob(
            printer_id=printer.id,
            file_id=artifact.id,
            model_id=artifact.model_id,
            remote_filename="release.gcode",
            state=PrintJobState.COMPLETED,
            operator_gate_state=OperatorGateState.PENDING,
            requested_by=user.id,
        )
        db_session.add(job)
        db_session.commit()
        db_session.refresh(job)
        return job

    def test_releases_a_pending_operator_gate(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        job = self._pending_job(db_session)

        response = client.post(
            f"/api/v1/fleet/queue/{job.id}/operator-decision",
            headers=auth_headers,
            json={"action": "release"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["operator_gate_state"] == "released"

    def test_rejects_a_repeated_operator_release(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        job = self._pending_job(db_session)
        first = client.post(
            f"/api/v1/fleet/queue/{job.id}/operator-decision",
            headers=auth_headers,
            json={"action": "release"},
        )
        assert first.status_code == 200, first.text

        response = client.post(
            f"/api/v1/fleet/queue/{job.id}/operator-decision",
            headers=auth_headers,
            json={"action": "release"},
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "operator_decision_not_pending"


class TestRoutingAndMaintenanceValidation:
    def test_rejects_an_overlong_drain_reason_without_mutation(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        printer = Printer(
            name="Drain validation",
            moonraker_url="http://drain-validation",
            status=PrinterStatus.READY,
        )
        db_session.add(printer)
        db_session.commit()
        db_session.refresh(printer)

        response = client.patch(
            f"/api/v1/fleet/printers/{printer.id}/routing",
            headers=auth_headers,
            json={"drain_mode": True, "drain_reason": "x" * 513},
        )

        assert response.status_code == 422, response.text
        db_session.refresh(printer)
        assert printer.drain_mode is False
        assert printer.drain_reason is None

    def test_cross_printer_maintenance_window_id_is_hidden(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        first = Printer(
            name="Maintenance owner",
            moonraker_url="http://maintenance-owner",
            status=PrinterStatus.READY,
        )
        second = Printer(
            name="Maintenance stranger",
            moonraker_url="http://maintenance-stranger",
            status=PrinterStatus.READY,
        )
        db_session.add(first)
        db_session.add(second)
        db_session.commit()
        db_session.refresh(first)
        db_session.refresh(second)
        now = utcnow()
        window = client.post(
            f"/api/v1/fleet/printers/{first.id}/maintenance-windows",
            headers=auth_headers,
            json={
                "starts_at": now.isoformat(),
                "ends_at": (now + timedelta(hours=1)).isoformat(),
            },
        ).json()
        response = client.patch(
            f"/api/v1/fleet/printers/{second.id}/maintenance-windows/{window['id']}",
            headers=auth_headers,
            json={"reason": "wrong printer"},
        )

        assert response.status_code == 404, response.text

    def test_cross_printer_maintenance_log_id_is_hidden(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        first = Printer(
            name="Log owner",
            moonraker_url="http://log-owner",
            status=PrinterStatus.READY,
        )
        second = Printer(
            name="Log stranger",
            moonraker_url="http://log-stranger",
            status=PrinterStatus.READY,
        )
        db_session.add(first)
        db_session.add(second)
        db_session.commit()
        db_session.refresh(first)
        db_session.refresh(second)
        log = client.post(
            f"/api/v1/fleet/printers/{first.id}/maintenance-log",
            headers=auth_headers,
            json={"category": "service", "note": "owner only"},
        ).json()

        response = client.patch(
            f"/api/v1/fleet/printers/{second.id}/maintenance-log/{log['id']}",
            headers=auth_headers,
            json={"note": "wrong printer"},
        )

        assert response.status_code == 404, response.text


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        pytest.param("get", "/api/v1/fleet/summary", {}, id="summary"),
        pytest.param("get", "/api/v1/fleet/queue", {}, id="queue-list"),
        pytest.param(
            "post",
            "/api/v1/fleet/queue",
            {"json": {"file_id": 1, "strategy": "manual", "printer_id": 1}},
            id="queue-create",
        ),
        pytest.param(
            "post",
            "/api/v1/fleet/compatibility",
            {"json": {"file_id": 1, "printer_ids": [1]}},
            id="compatibility",
        ),
        pytest.param(
            "post",
            "/api/v1/fleet/batches",
            {"json": {"file_id": 1, "quantity": 1, "printer_id": 1}},
            id="batch",
        ),
        pytest.param(
            "patch",
            "/api/v1/fleet/queue/1",
            {"json": {"priority": "rush"}},
            id="queue-update",
        ),
        pytest.param("delete", "/api/v1/fleet/queue/1", {}, id="queue-delete"),
        pytest.param("post", "/api/v1/fleet/queue/1/retry", {}, id="retry"),
        pytest.param(
            "post",
            "/api/v1/fleet/queue/1/operator-decision",
            {"json": {"action": "hold"}},
            id="operator-decision",
        ),
        pytest.param(
            "patch",
            "/api/v1/fleet/printers/1/routing",
            {"json": {"drain_mode": True}},
            id="routing",
        ),
        pytest.param(
            "get",
            "/api/v1/fleet/printers/1/maintenance-windows",
            {},
            id="windows-list",
        ),
        pytest.param(
            "post",
            "/api/v1/fleet/printers/1/maintenance-windows",
            {
                "json": {
                    "starts_at": "2026-01-01T00:00:00Z",
                    "ends_at": "2026-01-02T00:00:00Z",
                }
            },
            id="windows-create",
        ),
        pytest.param(
            "patch",
            "/api/v1/fleet/printers/1/maintenance-windows/1",
            {"json": {"reason": "service"}},
            id="windows-update",
        ),
        pytest.param(
            "delete",
            "/api/v1/fleet/printers/1/maintenance-windows/1",
            {},
            id="windows-delete",
        ),
        pytest.param(
            "get",
            "/api/v1/fleet/printers/1/maintenance-log",
            {},
            id="log-list",
        ),
        pytest.param(
            "post",
            "/api/v1/fleet/printers/1/maintenance-log",
            {"json": {"category": "service", "note": "done"}},
            id="log-create",
        ),
        pytest.param(
            "patch",
            "/api/v1/fleet/printers/1/maintenance-log/1",
            {"json": {"note": "updated"}},
            id="log-update",
        ),
        pytest.param(
            "delete",
            "/api/v1/fleet/printers/1/maintenance-log/1",
            {},
            id="log-delete",
        ),
    ],
)
def test_requires_authentication_for_every_fleet_route(
    method: str, path: str, kwargs: dict, client: TestClient
) -> None:
    response = client.request(method, path, **kwargs)

    assert response.status_code == 401, response.text
