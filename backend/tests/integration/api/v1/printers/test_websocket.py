"""Authenticating the live printer status socket.

A browser cannot set an `Authorization` header on a WebSocket, so this endpoint takes a
short-lived ticket instead — and a ticket is a bearer credential in a URL, which lands in
proxy logs and browser history. That is why it is single-use and short-lived, and why
these tests care as much about what is refused as about what connects: a replayed ticket,
an expired one, one issued for a different printer, and a normal access token presented in
its place all have to fail closed.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session
from starlette.websockets import WebSocketDisconnect

from tests.factories import build_printer


class TestPrinterWebSocketAuth:
    def test_one_time_ticket_replaces_access_token_in_websocket_url(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ):
        printer = build_printer(
            db_session, name="Ticketed", moonraker_url="http://printer.local"
        )

        response = client.post(
            f"/api/v1/printers/{printer.id}/ws-ticket", headers=auth_headers
        )
        assert response.status_code == 200
        ticket = response.json()["ticket"]
        assert response.json()["expires_in"] <= 30

        with client.websocket_connect(
            f"/api/v1/printers/{printer.id}/ws?ticket={ticket}"
        ):
            pass

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/api/v1/printers/{printer.id}/ws?ticket={ticket}"
            ):
                pass

        raw_token = auth_headers["Authorization"].split(" ", 1)[1]
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/api/v1/printers/{printer.id}/ws?token={raw_token}"
            ):
                pass

    def test_bearer_header_token_authenticates(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ):
        printer = build_printer(
            db_session, name="Bearer", moonraker_url="http://printer.local"
        )

        with client.websocket_connect(
            f"/api/v1/printers/{printer.id}/ws", headers=auth_headers
        ):
            pass

    def test_bearer_header_invalid_token_closes(
        self, client: TestClient, db_session: Session
    ):
        printer = build_printer(
            db_session, name="BadToken", moonraker_url="http://printer.local"
        )

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/api/v1/printers/{printer.id}/ws",
                headers={"Authorization": "Bearer not-a-real-token"},
            ):
                pass


class TestWsTicket:
    def test_ws_ticket_404_unknown_printer(self, client: TestClient, auth_headers):
        resp = client.post("/api/v1/printers/99999/ws-ticket", headers=auth_headers)
        assert resp.status_code == 404
        assert resp.json()["detail"] == "printer_not_found"

    def test_ws_ticket_404_deleted_printer(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = build_printer(
            db_session, "Gone", moonraker_url="http://gone.local", trashed=True
        )
        db_session.refresh(p)

        resp = client.post(f"/api/v1/printers/{p.id}/ws-ticket", headers=auth_headers)
        assert resp.status_code == 404

    def test_refuses_a_ticket_for_a_printer_that_was_deleted(
        self, client: TestClient, auth_headers, db_session: Session
    ) -> None:
        from app.core.time import utcnow

        printer = build_printer(
            db_session, name="Gone", moonraker_url="http://gone.local:7125"
        )
        printer.deleted_at = utcnow()
        db_session.add(printer)
        db_session.commit()

        response = client.post(
            f"/api/v1/printers/{printer.id}/ws-ticket", headers=auth_headers
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "printer_not_found"

    def test_refuses_a_bearer_token_whose_subject_is_not_an_account_id(
        self, client: TestClient, db_session: Session
    ) -> None:
        from app.services.auth import create_access_token

        printer = build_printer(
            db_session, name="Socket", moonraker_url="http://socket.local:7125"
        )
        forged = create_access_token("not-an-id", "ghost", scope="write")  # type: ignore[arg-type]

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/api/v1/printers/{printer.id}/ws",
                headers={"Authorization": f"Bearer {forged}"},
            ):
                pass
