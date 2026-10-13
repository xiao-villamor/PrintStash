"""End-to-end request timing follows a real database-backed API request."""

from __future__ import annotations

import re

import pytest


class TestRequestTelemetry:
    @pytest.mark.asyncio
    async def test_reports_database_work_for_a_library_request(
        self, api, superuser_headers: dict[str, str]
    ) -> None:
        response = await api.get(
            "/api/v1/collections/children", headers=superuser_headers
        )

        assert response.status_code == 200
        assert response.headers["x-request-id"]
        assert re.search(r"\bapp;dur=\d+(?:\.\d+)?", response.headers["server-timing"])
        sql = re.search(
            r'\bsql;dur=\d+(?:\.\d+)?;desc="(\d+) statements"',
            response.headers["server-timing"],
        )
        assert sql is not None
        assert int(sql.group(1)) > 0
