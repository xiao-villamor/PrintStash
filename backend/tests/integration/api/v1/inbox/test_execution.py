"""Capture commands keep the ASGI loop available through response projection."""

import json

import httpx
import pytest
from sqlalchemy import event

from tests.integration.api.v1.inbox.conftest import CANONICAL_URL, capture_source


class TestCaptureExecution:
    @pytest.mark.asyncio
    async def test_keeps_loop_responsive_during_resolver_submission(
        self, app, auth_headers, work_engine, monkeypatch, no_egress, loop_handshake
    ):
        wait_for_loop, observations = loop_handshake
        submit = work_engine.submit

        def delayed_submit(submission):
            wait_for_loop()
            return submit(submission)

        monkeypatch.setattr(work_engine, "submit", delayed_submit)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/inbox",
                headers=auth_headers,
                json={"url": "https://example.com/model"},
            )

        assert response.status_code == 202, response.text
        assert response.json()["state"] == "captured"
        assert observations == [True]

    @pytest.mark.asyncio
    async def test_keeps_loop_responsive_during_browser_response(
        self, app, auth_headers, db_session, no_egress, loop_handshake
    ):
        wait_for_loop, observations = loop_handshake
        engine = db_session.get_bind()

        def delayed_query(connection, cursor, statement, parameters, context, many):
            if "inbox_item_results" in statement.lower():
                wait_for_loop()

        event.listen(engine, "before_cursor_execute", delayed_query)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/inbox/browser-upload",
                    headers=auth_headers,
                    data={
                        "source_url": CANONICAL_URL,
                        "capture_source": json.dumps(capture_source()),
                    },
                    files={
                        "file": (
                            "widget.3mf",
                            b"browser-owned",
                            "application/octet-stream",
                        )
                    },
                )
        finally:
            event.remove(engine, "before_cursor_execute", delayed_query)

        assert response.status_code == 201, response.text
        assert response.json()["manifest"]["files"][0]["size"] == 13
        assert observations and all(observations)
