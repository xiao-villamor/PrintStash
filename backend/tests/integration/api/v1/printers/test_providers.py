"""The same printer API across four very different machines.

Bambu, PrusaLink, OctoPrint and Elegoo Centauri expose completely different protocols —
MQTT, a REST API with a digest login, an API key, a proprietary websocket — and the point
of this router is that a caller never sees the difference. So these tests drive the same
endpoints against each provider and assert the same *shape* of answer, with the provider
seam standing in for the machine.

What is provider-specific and does show through: the connection details each one requires,
and the operations only some of them support. A machine that cannot do something must say
so clearly rather than accepting the request and quietly doing nothing.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import (
    Printer,
)
from tests.factories import build_file, build_model, build_printer


class TestBambuPrinter:
    def test_create_bambu_with_required_fields(self, client: TestClient, auth_headers):
        resp = client.post(
            "/api/v1/printers",
            json={
                "name": "Bambu P1S",
                "provider": "bambu_lan",
                "bambu_host": "192.168.1.50",
                "bambu_serial": "SN123",
                "bambu_access_code": "access",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["provider"] == "bambu_lan"
        assert body["capabilities"]["can_upload"] is True
        assert body["capabilities"]["can_start"] is True
        assert body["capabilities"]["can_pause"] is True
        assert body["capabilities"]["support_level"] == "beta"
        assert "list_files" in body["capabilities"]["unsupported_actions"]

    def test_create_bambu_missing_fields_422(self, client: TestClient, auth_headers):
        resp = client.post(
            "/api/v1/printers",
            json={"name": "Bambu", "provider": "bambu_lan"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_bambu_send_uploads_when_ready(
        self, client: TestClient, db_session: Session, auth_headers, tmp_path
    ):

        m = build_model(db_session, name="Model", slug="model-bambu", hash="x" * 64)

        f = build_file(
            db_session,
            m,
            path="/data/model.gcode",
            filename="model.gcode",
            file_type="gcode",
            version=1,
            size_bytes=100,
            sha256="y" * 64,
        )

        p = build_printer(
            db_session,
            name="Bambu",
            provider="bambu_lan",
            moonraker_url="",
            bambu_host="192.168.1.50",
            bambu_serial="SN123",
            bambu_access_code="access",
        )

        local = tmp_path / "model.gcode"
        local.write_text("G28\n")

        class FakeBackend:
            def exists(self, _path):
                return True

            def download_to_path(self, _path, _target):
                return local

        with (
            patch("app.api.v1.printers.get_backend", return_value=FakeBackend()),
            patch(
                "app.services.printer_provider.BambuLanProvider.query_status",
                new_callable=AsyncMock,
                return_value={
                    "result": {"status": {"print_stats": {"state": "standby"}}}
                },
            ),
            patch(
                "app.services.printer_provider.BambuLanProvider.upload",
                new_callable=AsyncMock,
                return_value={"ok": True},
            ) as upload,
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/send",
                json={"file_id": f.id, "start_print": False},
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["state"] == "completed"
        upload.assert_awaited_once()

    def test_bambu_send_rejects_busy_printer(
        self, client: TestClient, db_session: Session, auth_headers
    ):
        p = build_printer(
            db_session,
            name="Bambu",
            provider="bambu_lan",
            moonraker_url="",
            bambu_host="192.168.1.50",
            bambu_serial="SN123",
            bambu_access_code="access",
        )
        with patch(
            "app.services.printer_provider.BambuLanProvider.query_status",
            new_callable=AsyncMock,
            return_value={"result": {"status": {"print_stats": {"state": "printing"}}}},
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/send",
                json={"file_id": 999, "start_print": False},
                headers=auth_headers,
            )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "printer_not_ready"

    def test_send_rejects_binary_bgcode(
        self, client: TestClient, db_session: Session, auth_headers
    ):
        # A .bgcode file is indexed (file_type "gcode") for its metadata, but
        # Moonraker/Klipper can't print binary G-code — the send must 400.

        m = build_model(db_session, name="Model", slug="model-bgcode", hash="b" * 64)

        f = build_file(
            db_session,
            m,
            path="/data/model.bgcode",
            filename="model.bgcode",
            file_type="gcode",
            version=1,
            size_bytes=100,
            sha256="z" * 64,
        )

        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )

        resp = client.post(
            f"/api/v1/printers/{p.id}/send",
            json={"file_id": f.id, "start_print": False},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "binary_gcode_not_printable"

    def test_bambu_pause_calls_provider(
        self, client: TestClient, db_session: Session, auth_headers
    ):
        p = build_printer(
            db_session,
            name="Bambu",
            provider="bambu_lan",
            moonraker_url="",
            bambu_host="192.168.1.50",
            bambu_serial="SN123",
            bambu_access_code="access",
        )
        with patch(
            "app.services.printer_provider.BambuLanProvider.pause",
            new_callable=AsyncMock,
        ) as mock_pause:
            mock_pause.return_value = {"ok": True}
            resp = client.post(f"/api/v1/printers/{p.id}/pause", headers=auth_headers)
            assert resp.status_code == 200
            assert resp.json() == {"ok": True}

    def test_bambu_diagnostics_reports_beta_capabilities(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = build_printer(
            db_session,
            name="Bambu",
            provider="bambu_lan",
            moonraker_url="",
            bambu_host="192.168.1.50",
            bambu_serial="SN123",
            bambu_access_code="access",
        )

        with patch(
            "app.services.printer_provider.BambuLanProvider.query_status",
            new_callable=AsyncMock,
        ) as mock_status:
            mock_status.return_value = {"result": {"status": {}}}
            resp = client.get(
                f"/api/v1/printers/{p.id}/diagnostics", headers=auth_headers
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["support_level"] == "beta"
        assert body["capabilities"]["can_upload"] is True
        assert body["capabilities"]["can_start"] is True
        assert "list_files" in body["unsupported_actions"]
        assert body["ok"] is True
        assert [check["name"] for check in body["checks"]] == [
            "configuration",
            "provider_info",
            "live_status",
        ]

    def test_diagnostics_timeout_returns_check_failure(
        self, client: TestClient, auth_headers, db_session: Session, monkeypatch
    ):
        p = build_printer(
            db_session,
            name="Bambu",
            provider="bambu_lan",
            moonraker_url="",
            bambu_host="192.168.1.50",
            bambu_serial="SN123",
            bambu_access_code="access",
        )
        monkeypatch.setattr(
            "app.api.v1.printers._DIAGNOSTIC_CHECK_TIMEOUT_SECONDS", 0.01
        )

        async def slow_status(_self):
            await asyncio.sleep(1)
            return {"result": {"status": {}}}

        with patch(
            "app.services.printer_provider.BambuLanProvider.query_status",
            new=slow_status,
        ):
            resp = client.get(
                f"/api/v1/printers/{p.id}/diagnostics", headers=auth_headers
            )

        assert resp.status_code == 200
        body = resp.json()
        live_status = next(
            check for check in body["checks"] if check["name"] == "live_status"
        )
        assert body["ok"] is False
        assert live_status["ok"] is False
        assert live_status["code"] == "provider_timeout"


class TestPrusaLinkPrinter:
    def test_create_digest_credentials_are_redacted(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        resp = client.post(
            "/api/v1/printers",
            json={
                "name": "Prusa MK4",
                "provider": "prusalink",
                "prusalink_url": "http://mk4.local/",
                "prusalink_auth_mode": "digest",
                "prusalink_username": "maker",
                "prusalink_password": "secret",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["provider"] == "prusalink"
        assert body["prusalink_url"] == "http://mk4.local"
        assert body["prusalink_username"] == "maker"
        assert body["has_prusalink_password"] is True
        assert body["has_prusalink_api_key"] is False
        assert "prusalink_password" not in body
        assert body["capabilities"]["support_level"] == "beta"
        row = db_session.exec(select(Printer).where(Printer.name == "Prusa MK4")).one()
        assert row.prusalink_password == "secret"

    def test_create_api_key_credentials_are_redacted(
        self, client: TestClient, auth_headers
    ):
        resp = client.post(
            "/api/v1/printers",
            json={
                "name": "Prusa MINI",
                "provider": "prusalink",
                "prusalink_url": "http://mini.local",
                "prusalink_auth_mode": "api_key",
                "prusalink_api_key": "legacy-key",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["has_prusalink_api_key"] is True
        assert "prusalink_api_key" not in body

    def test_create_requires_credentials_for_selected_mode(
        self, client: TestClient, auth_headers
    ):
        resp = client.post(
            "/api/v1/printers",
            json={
                "name": "Prusa",
                "provider": "prusalink",
                "prusalink_url": "http://prusa.local",
                "prusalink_auth_mode": "digest",
                "prusalink_username": "maker",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_elegoo_neptune_variant_uses_moonraker(
        self, client: TestClient, auth_headers
    ):
        resp = client.post(
            "/api/v1/printers",
            json={
                "name": "Neptune 4 Plus",
                "provider": "moonraker",
                "provider_variant": "elegoo_neptune4",
                "moonraker_url": "http://neptune.local:7125",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["provider"] == "moonraker"
        assert body["provider_variant"] == "elegoo_neptune4"
        assert body["capabilities"]["support_level"] == "stable"


class TestOctoPrintPrinter:
    def test_create_credentials_are_redacted(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        resp = client.post(
            "/api/v1/printers",
            json={
                "name": "OctoPi",
                "provider": "octoprint",
                "octoprint_url": "http://octopi.local/",
                "octoprint_api_key": "secret-key",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["provider"] == "octoprint"
        assert body["octoprint_url"] == "http://octopi.local"
        assert body["has_octoprint_api_key"] is True
        assert "octoprint_api_key" not in body
        assert body["capabilities"]["support_level"] == "beta"
        row = db_session.exec(select(Printer).where(Printer.name == "OctoPi")).one()
        assert row.octoprint_api_key == "secret-key"

    def test_create_requires_every_api_key_field(
        self, client: TestClient, auth_headers
    ):
        resp = client.post(
            "/api/v1/printers",
            json={"name": "OctoPi", "provider": "octoprint"},
            headers=auth_headers,
        )
        assert resp.status_code == 422


class TestElegooCentauriPrinter:
    def test_create_original_carbon_without_access_code(
        self, client: TestClient, auth_headers
    ):
        resp = client.post(
            "/api/v1/printers",
            json={
                "name": "Centauri Carbon",
                "provider": "elegoo_centauri",
                "provider_variant": "elegoo_centauri_carbon",
                "elegoo_centauri_host": "192.168.1.50",
                "elegoo_centauri_mainboard_id": "mainboard-123",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["provider"] == "elegoo_centauri"
        assert body["provider_variant"] == "elegoo_centauri_carbon"
        assert body["elegoo_centauri_host"] == "192.168.1.50"
        assert body["elegoo_centauri_mainboard_id"] == "mainboard-123"
        assert body["capabilities"]["can_live_status"] is True
        assert body["capabilities"]["can_upload"] is True

    def test_create_carbon_2_redacts_access_code(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        resp = client.post(
            "/api/v1/printers",
            json={
                "name": "Centauri Carbon 2",
                "provider": "elegoo_centauri",
                "provider_variant": "elegoo_centauri_carbon_2",
                "elegoo_centauri_host": "192.168.1.51",
                "elegoo_centauri_access_code": "ABC123",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["has_elegoo_centauri_access_code"] is True
        assert "elegoo_centauri_access_code" not in body
        row = db_session.exec(
            select(Printer).where(Printer.name == "Centauri Carbon 2")
        ).one()
        assert row.elegoo_centauri_access_code == "ABC123"

    def test_carbon_2_requires_access_code(self, client: TestClient, auth_headers):
        resp = client.post(
            "/api/v1/printers",
            json={
                "name": "Centauri Carbon 2",
                "provider": "elegoo_centauri",
                "provider_variant": "elegoo_centauri_carbon_2",
                "elegoo_centauri_host": "192.168.1.51",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422
