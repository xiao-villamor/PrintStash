"""HTTP timing identifies slow requests and the SQL work attributable to each request."""

from __future__ import annotations

import logging
import re
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.main as app_main
from app.core.config import _overlay

_SQL_TIMING = re.compile(r'\bsql;dur=(\d+(?:\.\d+)?);desc="(\d+) statements"')


def _clock(monkeypatch, *, elapsed_ms: float) -> None:
    readings = iter((100.0, 100.0 + elapsed_ms / 1000.0))
    monkeypatch.setattr(
        app_main,
        "time",
        SimpleNamespace(perf_counter=lambda: next(readings)),
    )


class TestRequestTelemetry:
    def test_reports_sql_work_in_server_timing(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.get("/api/v1/collections/children", headers=auth_headers)

        assert response.status_code == 200
        timing = response.headers["server-timing"]
        assert re.search(r"\bapp;dur=\d+(?:\.\d+)?", timing)
        sql = _SQL_TIMING.search(timing)
        assert sql is not None
        assert float(sql.group(1)) >= 0
        assert int(sql.group(2)) > 0
        assert response.headers["x-request-id"]

    def test_keeps_sql_count_with_its_request(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        queried = client.get("/api/v1/collections/children", headers=auth_headers)
        no_sql = client.get("/openapi.json")

        assert int(_SQL_TIMING.search(queried.headers["server-timing"]).group(2)) > 0
        assert int(_SQL_TIMING.search(no_sql.headers["server-timing"]).group(2)) == 0

    def test_warns_when_a_request_exceeds_the_threshold(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        monkeypatch,
        caplog,
    ) -> None:
        _clock(monkeypatch, elapsed_ms=1200)
        monkeypatch.setitem(_overlay, "slow_request_ms", 1000)

        with caplog.at_level(logging.WARNING, logger=app_main.logger.name):
            response = client.get("/api/v1/collections/children", headers=auth_headers)

        warnings = [
            record.getMessage()
            for record in caplog.records
            if "slow request" in record.getMessage()
        ]
        assert response.status_code == 200
        assert len(warnings) == 1
        assert "duration_ms=1200.0" in warnings[0]
        assert "sql_count=" in warnings[0]
        assert "sql_ms=" in warnings[0]
        assert response.headers["x-request-id"] in warnings[0]

    def test_keeps_query_values_out_of_slow_request_logs(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        monkeypatch,
        caplog,
    ) -> None:
        _clock(monkeypatch, elapsed_ms=1200)
        monkeypatch.setitem(_overlay, "slow_request_ms", 1000)

        with caplog.at_level(logging.WARNING, logger=app_main.logger.name):
            response = client.get(
                "/api/v1/collections/search",
                params={"q": "private-marker"},
                headers=auth_headers,
            )

        warnings = [
            record.getMessage()
            for record in caplog.records
            if "slow request" in record.getMessage()
        ]
        assert response.status_code == 200
        assert len(warnings) == 1
        assert "/api/v1/collections/search" in warnings[0]
        assert "private-marker" not in warnings[0]

    def test_omits_a_slow_warning_below_threshold(
        self, client: TestClient, monkeypatch, caplog
    ) -> None:
        _clock(monkeypatch, elapsed_ms=100)
        monkeypatch.setitem(_overlay, "slow_request_ms", 1000)

        with caplog.at_level(logging.WARNING, logger=app_main.logger.name):
            response = client.get("/openapi.json")

        assert response.status_code == 200
        assert not any(
            "slow request" in record.getMessage() for record in caplog.records
        )

    def test_warns_at_the_threshold(
        self, client: TestClient, monkeypatch, caplog
    ) -> None:
        _clock(monkeypatch, elapsed_ms=1000)
        monkeypatch.setitem(_overlay, "slow_request_ms", 1000)

        with caplog.at_level(logging.WARNING, logger=app_main.logger.name):
            response = client.get("/openapi.json")

        assert response.status_code == 200
        assert any("slow request" in record.getMessage() for record in caplog.records)

    def test_reports_timing_for_an_http_error(self, client: TestClient) -> None:
        response = client.get("/api/v1/no-such-route")

        assert response.status_code == 404
        assert _SQL_TIMING.search(response.headers["server-timing"])
