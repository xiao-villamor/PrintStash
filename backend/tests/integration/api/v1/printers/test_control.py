"""Pausing, resuming, and cancelling a print that is already running.

These are the only endpoints that change what a machine is physically doing, so every one
of them is gated on the operator role and every one of them can fail at the printer rather
than in the app. Those two failure sources answer differently on purpose: a 403 is the
app's decision and retrying will not help, while a provider error is the printer's and it
might. Collapsing them into one code would leave an operator unable to tell "not allowed"
from "not reachable" while a print is burning filament.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.models import (
    FileType,
    PrinterProvider,
    PrintJobState,
)
from app.services.printer_provider import ProviderError
from tests.factories import build_file, build_model, build_print_job, build_printer


class TestPrinterControl:
    def test_pause_requires_auth(self, client: TestClient, db_session: Session):
        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )
        resp = client.post(f"/api/v1/printers/{p.id}/pause")
        assert resp.status_code == 401

    def test_pause_sends_to_moonraker(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )

        with patch(
            "app.services.printer_provider.MoonrakerProvider.pause",
            new_callable=AsyncMock,
        ) as mock_pause:
            mock_pause.return_value = {"result": "ok"}
            resp = client.post(f"/api/v1/printers/{p.id}/pause", headers=auth_headers)
            assert resp.status_code == 200
            assert resp.json() == {"ok": True}
            mock_pause.assert_called_once()

    def test_resume_sends_to_moonraker(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )

        with patch(
            "app.services.printer_provider.MoonrakerProvider.resume",
            new_callable=AsyncMock,
        ) as mock_resume:
            mock_resume.return_value = {"result": "ok"}
            resp = client.post(f"/api/v1/printers/{p.id}/resume", headers=auth_headers)
            assert resp.status_code == 200
            assert resp.json() == {"ok": True}

    def test_cancel_sends_to_moonraker(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )

        with patch(
            "app.services.printer_provider.MoonrakerProvider.cancel",
            new_callable=AsyncMock,
        ) as mock_cancel:
            mock_cancel.return_value = {"result": "ok"}
            resp = client.post(f"/api/v1/printers/{p.id}/cancel", headers=auth_headers)
            assert resp.status_code == 200
            assert resp.json() == {"ok": True}

    def test_cancel_marks_active_job_cancelled_without_polling_transition(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = build_printer(
            db_session,
            name="OctoPrint",
            provider="octoprint",
            octoprint_url="http://octo",
            octoprint_api_key="key",
        )
        model = build_model(db_session, name="Cancel", slug="cancel-job", hash="c" * 64)
        file = build_file(
            db_session,
            model,
            path="/data/cube.gcode",
            filename="cube.gcode",
            file_type=FileType.GCODE,
            version=1,
            size_bytes=10,
            sha256="d" * 64,
        )
        job = build_print_job(
            db_session,
            file,
            printer_id=p.id,
            remote_filename="cube.gcode",
            state=PrintJobState.PRINTING,
        )

        with patch(
            "app.services.printer_provider.OctoPrintProvider.cancel",
            new_callable=AsyncMock,
            return_value={"ok": True},
        ):
            resp = client.post(f"/api/v1/printers/{p.id}/cancel", headers=auth_headers)

        assert resp.status_code == 200
        db_session.refresh(job)
        assert job.state == PrintJobState.CANCELLED
        assert job.finished_at is not None

    def test_set_temperature_builds_gcode(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )

        with patch(
            "app.services.printer_provider.MoonrakerProvider.run_gcode",
            new_callable=AsyncMock,
        ) as mock_gcode:
            mock_gcode.return_value = {"result": "ok"}
            resp = client.post(
                f"/api/v1/printers/{p.id}/temperature",
                json={"heater": "bed", "target": 60},
                headers=auth_headers,
            )
            assert resp.status_code == 200
            mock_gcode.assert_called_once_with("M140 S60")

    def test_home_subset_axes_builds_gcode(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )

        with patch(
            "app.services.printer_provider.MoonrakerProvider.run_gcode",
            new_callable=AsyncMock,
        ) as mock_gcode:
            mock_gcode.return_value = {"result": "ok"}
            resp = client.post(
                f"/api/v1/printers/{p.id}/home",
                json={"axes": "xy"},
                headers=auth_headers,
            )
            assert resp.status_code == 200
            mock_gcode.assert_called_once_with("G28 X Y")

    def test_emergency_stop_calls_provider(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )

        with patch(
            "app.services.printer_provider.MoonrakerProvider.emergency_stop",
            new_callable=AsyncMock,
        ) as mock_estop:
            mock_estop.return_value = {"result": "ok"}
            resp = client.post(
                f"/api/v1/printers/{p.id}/emergency_stop", headers=auth_headers
            )
            assert resp.status_code == 200
            mock_estop.assert_called_once()

    def test_set_temperature_rejected_for_bambu(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = build_printer(
            db_session,
            name="Bambu",
            provider=PrinterProvider.BAMBU_LAN,
            moonraker_url="",
            bambu_host="192.168.1.50",
            bambu_serial="SN123",
            bambu_access_code="access",
        )

        resp = client.post(
            f"/api/v1/printers/{p.id}/temperature",
            json={"heater": "extruder", "target": 200},
            headers=auth_headers,
        )
        assert resp.status_code == 409

    def test_pause_provider_error_502(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )

        with patch(
            "app.services.printer_provider.MoonrakerProvider.pause",
            new_callable=AsyncMock,
            side_effect=ProviderError("boom", code="printer_offline"),
        ):
            resp = client.post(f"/api/v1/printers/{p.id}/pause", headers=auth_headers)
        assert resp.status_code == 502
        assert resp.json()["detail"] == "printer_offline"

    def test_resume_generic_exception_502(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )

        with patch(
            "app.services.printer_provider.MoonrakerProvider.resume",
            new_callable=AsyncMock,
            side_effect=RuntimeError("secret stack"),
        ):
            resp = client.post(f"/api/v1/printers/{p.id}/resume", headers=auth_headers)
        assert resp.status_code == 502
        assert resp.json()["detail"] == "provider_error"
        assert "secret stack" not in resp.text

    def test_home_404_deleted_printer(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = build_printer(
            db_session, "Gone", moonraker_url="http://gone.local", trashed=True
        )
        db_session.refresh(p)

        resp = client.post(
            f"/api/v1/printers/{p.id}/home", json={}, headers=auth_headers
        )
        assert resp.status_code == 404

    def test_home_provider_error_502(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )

        with patch(
            "app.services.printer_provider.MoonrakerProvider.run_gcode",
            new_callable=AsyncMock,
            side_effect=ProviderError("boom", code="printer_offline"),
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/home", json={}, headers=auth_headers
            )
        assert resp.status_code == 502
        assert resp.json()["detail"] == "printer_offline"

    def test_temperature_generic_exception_502(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )

        with patch(
            "app.services.printer_provider.MoonrakerProvider.run_gcode",
            new_callable=AsyncMock,
            side_effect=RuntimeError("secret stack"),
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/temperature",
                json={"heater": "bed", "target": 50},
                headers=auth_headers,
            )
        assert resp.status_code == 502
        assert resp.json()["detail"] == "provider_error"

    def test_emergency_stop_provider_error_502(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )

        with patch(
            "app.services.printer_provider.MoonrakerProvider.emergency_stop",
            new_callable=AsyncMock,
            side_effect=ProviderError("boom", code="printer_offline"),
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/emergency_stop", headers=auth_headers
            )
        assert resp.status_code == 502
        assert resp.json()["detail"] == "printer_offline"

    def test_emergency_stop_generic_exception_502(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )

        with patch(
            "app.services.printer_provider.MoonrakerProvider.emergency_stop",
            new_callable=AsyncMock,
            side_effect=RuntimeError("secret stack"),
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/emergency_stop", headers=auth_headers
            )
        assert resp.status_code == 502
        assert resp.json()["detail"] == "provider_error"

    def test_refuses_an_action_the_provider_cannot_perform(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        from dataclasses import replace

        from app.services.printer_provider import MoonrakerProvider

        p = build_printer(
            db_session, name="No pause", moonraker_url="http://10.0.0.9:7125"
        )
        no_pause = replace(MoonrakerProvider.capabilities, supported=frozenset())

        with patch.object(MoonrakerProvider, "capabilities", no_pause):
            response = client.post(
                f"/api/v1/printers/{p.id}/pause", headers=auth_headers
            )

        # 409, not 502: the machine is fine, it simply cannot do this.
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "operation_not_supported_for_provider"

    def test_refuses_a_gcode_action_for_a_printer_that_was_deleted(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        from app.core.time import utcnow

        p = build_printer(
            db_session, name="Deleted", moonraker_url="http://10.0.0.8:7125"
        )
        p.deleted_at = utcnow()
        db_session.add(p)
        db_session.commit()

        response = client.post(
            f"/api/v1/printers/{p.id}/home", headers=auth_headers, json={"axes": ""}
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "printer_not_found"
