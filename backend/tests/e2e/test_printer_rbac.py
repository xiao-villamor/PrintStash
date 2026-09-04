"""E2E: admin grants one printer and API enforcement changes immediately."""

from __future__ import annotations

import pytest

from app.services.auth import create_access_token
from tests.factories import (
    build_user,
    printer_config,
)


class TestPrinter:
    @pytest.mark.asyncio
    async def test_admin_grants_print_access_to_one_printer(
        self, api, superuser_headers, e2e_db
    ) -> None:
        allowed = printer_config("Shared", moonraker_url="http://shared.local:7125")
        hidden = printer_config("Private", moonraker_url="http://private.local:7125")
        operator = build_user(
            e2e_db, username="e2e-operator", password="Password123", active=True
        )
        e2e_db.add_all([allowed, hidden, operator])
        e2e_db.commit()
        e2e_db.refresh(allowed)
        e2e_db.refresh(operator)
        token = create_access_token(operator.id, operator.username, scope="write")
        operator_headers = {"Authorization": f"Bearer {token}"}

        grant = await api.put(
            f"/api/v1/printers/{allowed.id}/permissions/{operator.id}",
            json={"role": "print"},
            headers=superuser_headers,
        )
        listing = await api.get("/api/v1/printers", headers=operator_headers)
        print_attempt = await api.post(
            f"/api/v1/printers/{allowed.id}/send",
            json={"file_id": 999, "start_print": False},
            headers=operator_headers,
        )
        control_attempt = await api.post(
            f"/api/v1/printers/{allowed.id}/pause", headers=operator_headers
        )

        assert grant.status_code == 200
        assert [row["name"] for row in listing.json()] == ["Shared"]
        assert listing.json()[0]["access"]["role"] == "print"
        assert print_attempt.status_code == 404
        assert print_attempt.json()["detail"] == "file_not_found"
        assert control_attempt.status_code == 403
        assert control_attempt.json()["detail"] == "printer_permission_denied"
