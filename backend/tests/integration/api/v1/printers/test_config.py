"""Reading and writing a printer's runtime configuration.

The configuration a printer reports is not the configuration PrintStash stores: some of it
comes from the machine and some is the operator's override. Reading has to merge the two
without letting a stale stored value shadow what the machine currently reports, and
writing has to reject a value the machine cannot hold rather than storing a setting that
will never take effect.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.services.printer_provider import ProviderError
from tests.factories import build_printer


class TestPrinterConfig:
    def test_moonraker_config_returns_both_config_documents(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )

        with (
            patch(
                "app.services.printer_provider.MoonrakerProvider.server_info",
                new_callable=AsyncMock,
            ) as mock_server_info,
            patch(
                "app.services.printer_provider.MoonrakerProvider.info",
                new_callable=AsyncMock,
            ) as mock_printer_info,
            patch(
                "app.services.printer_provider.MoonrakerProvider.server_config",
                new_callable=AsyncMock,
            ) as mock_server_config,
            patch(
                "app.services.printer_provider.MoonrakerProvider.printer_config",
                new_callable=AsyncMock,
            ) as mock_printer_config,
        ):
            mock_server_info.return_value = {"result": {"klippy_state": "ready"}}
            mock_printer_info.return_value = {"result": {"software_version": "v1"}}
            mock_server_config.return_value = {
                "result": {"server": {"host": "0.0.0.0"}}
            }
            mock_printer_config.return_value = {
                "result": {"status": {"configfile": {"config": {"printer": {}}}}}
            }
            resp = client.get(f"/api/v1/printers/{p.id}/config", headers=auth_headers)

        assert resp.status_code == 200
        body = resp.json()
        assert body["server_info"]["klippy_state"] == "ready"
        assert body["printer_info"]["software_version"] == "v1"
        assert body["moonraker_config"]["server"]["host"] == "0.0.0.0"
        assert "configfile" in body["klipper_config"]

    def test_config_unsupported_for_bambu(
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

        resp = client.get(f"/api/v1/printers/{p.id}/config", headers=auth_headers)

        assert resp.status_code == 409
        assert resp.json()["detail"] == "operation_not_supported_for_provider"

    def test_config_404_deleted_printer(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = build_printer(
            db_session, "Gone", moonraker_url="http://gone.local", trashed=True
        )
        db_session.refresh(p)

        resp = client.get(f"/api/v1/printers/{p.id}/config", headers=auth_headers)
        assert resp.status_code == 404

    def test_config_provider_error_502(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = build_printer(
            db_session, name="Ender 3", moonraker_url="http://10.0.0.1:7125"
        )

        with patch(
            "app.services.printer_provider.MoonrakerProvider.server_info",
            new_callable=AsyncMock,
            side_effect=ProviderError("boom", code="printer_offline"),
        ):
            resp = client.get(f"/api/v1/printers/{p.id}/config", headers=auth_headers)
        assert resp.status_code == 502
        assert resp.json()["detail"] == "printer_offline"

    def test_reports_a_printer_that_was_deleted(
        self, client: TestClient, auth_headers, db_session: Session
    ) -> None:
        from app.core.time import utcnow

        printer = build_printer(
            db_session, name="Gone", moonraker_url="http://gone.local:7125"
        )
        printer.deleted_at = utcnow()
        db_session.add(printer)
        db_session.commit()

        response = client.get(
            f"/api/v1/printers/{printer.id}/config", headers=auth_headers
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "printer_not_found"
