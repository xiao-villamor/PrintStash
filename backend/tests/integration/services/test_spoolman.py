"""Tests for the Spoolman client and consumption write-back helper."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlmodel import Session

from app.db.models import PrintJob, PrintJobState
from app.services import print_results, runtime_config
from app.services.spoolman import (
    SpoolmanClient,
    SpoolmanError,
    active_spool_sync,
    get_spoolman_client,
    use_spool_weight_sync,
)


def _mock_resp(status_code: int, json_value=None, text: str = ""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_value if json_value is not None else {}
    resp.text = text
    return resp


class TestSpoolmanClient:
    def test_list_spools_passes_archive_flag_and_unwraps(self):
        client = SpoolmanClient("http://spoolman.local:7912")
        resp = _mock_resp(200, [{"id": 1}, {"id": 2}])
        with patch("app.services.spoolman.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(return_value=resp)
            result = asyncio.run(client.list_spools())
            assert [s["id"] for s in result] == [1, 2]
            call = mock_get_client.return_value.request.call_args
            assert call.kwargs["params"] == {"allow_archived": "false"}

    def test_use_spool_weight_puts_use_weight(self):
        client = SpoolmanClient("http://spoolman.local:7912")
        resp = _mock_resp(200, {"id": 5, "remaining_weight": 900})
        with patch("app.services.spoolman.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(return_value=resp)
            asyncio.run(client.use_spool_weight(5, 12.5))
            call = mock_get_client.return_value.request.call_args
            assert call.args[0] == "PUT"
            assert call.args[1].endswith("/api/v1/spool/5/use")
            assert call.kwargs["json"] == {"use_weight": 12.5}

    def test_http_error_becomes_spoolman_error(self):
        client = SpoolmanClient("http://spoolman.local:7912")
        resp = _mock_resp(500, text="boom")
        with patch("app.services.spoolman.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(return_value=resp)
            with pytest.raises(SpoolmanError, match="spoolman 500"):
                asyncio.run(client.health_check())

    def test_api_key_sets_auth_headers(self):
        client = SpoolmanClient("http://spoolman.local:7912", api_key="secret")
        headers = client._headers()
        assert headers["Authorization"] == "Bearer secret"
        assert headers["X-Api-Key"] == "secret"

    def test_active_spool_parses_setting(self):
        client = SpoolmanClient("http://spoolman.local:7912")
        resp = _mock_resp(200, {"value": "7"})
        with patch("app.services.spoolman.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(return_value=resp)
            assert asyncio.run(client.active_spool()) == 7

    def test_active_spool_none_when_unset(self):
        client = SpoolmanClient("http://spoolman.local:7912")
        resp = _mock_resp(200, {"value": "null"})
        with patch("app.services.spoolman.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(return_value=resp)
            assert asyncio.run(client.active_spool()) is None

    def test_active_spool_swallows_spoolman_error(self):
        client = SpoolmanClient("http://spoolman.local:7912")
        with patch("app.services.spoolman.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(
                return_value=_mock_resp(500, text="down")
            )
            assert asyncio.run(client.active_spool()) is None

    def test_active_spool_unparseable_value_returns_none(self):
        client = SpoolmanClient("http://spoolman.local:7912")
        resp = _mock_resp(200, {"value": "not-an-int"})
        with patch("app.services.spoolman.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(return_value=resp)
            assert asyncio.run(client.active_spool()) is None

    def test_transport_error_becomes_spoolman_error(self):
        client = SpoolmanClient("http://spoolman.local:7912")
        with patch("app.services.spoolman.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(
                side_effect=httpx.ConnectError("connection refused")
            )
            with pytest.raises(SpoolmanError, match="transport error") as exc:
                asyncio.run(client.health_check())
            assert exc.value.code == "transport"

    def test_transport_error_falls_back_to_exception_class_name(self):
        # httpx connect errors sometimes stringify to "" — the client should
        # fall back to the exception class name so the UI shows something.
        client = SpoolmanClient("http://spoolman.local:7912")
        with patch("app.services.spoolman.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(
                side_effect=httpx.ConnectError("")
            )
            with pytest.raises(SpoolmanError, match="ConnectError"):
                asyncio.run(client.health_check())

    def test_non_json_response_wraps_raw_text(self):
        client = SpoolmanClient("http://spoolman.local:7912")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = ValueError("not json")
        resp.text = "plain text body"
        with patch("app.services.spoolman.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(return_value=resp)
            result = asyncio.run(client.health_check())
            assert result == {"raw": "plain text body"}

    def test_health_check_wraps_non_dict_response(self):
        client = SpoolmanClient("http://spoolman.local:7912")
        resp = _mock_resp(200, ["unexpected", "list"])
        with patch("app.services.spoolman.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(return_value=resp)
            result = asyncio.run(client.health_check())
            assert result == {"raw": ["unexpected", "list"]}

    def test_list_vendors_unwraps_list(self):
        client = SpoolmanClient("http://spoolman.local:7912")
        resp = _mock_resp(200, [{"id": 1, "name": "Prusament"}])
        with patch("app.services.spoolman.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(return_value=resp)
            result = asyncio.run(client.list_vendors())
            assert result == [{"id": 1, "name": "Prusament"}]

    def test_list_vendors_defensive_default_on_non_list(self):
        client = SpoolmanClient("http://spoolman.local:7912")
        resp = _mock_resp(200, {"unexpected": "dict"})
        with patch("app.services.spoolman.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(return_value=resp)
            assert asyncio.run(client.list_vendors()) == []

    def test_list_filaments_unwraps_list(self):
        client = SpoolmanClient("http://spoolman.local:7912")
        resp = _mock_resp(200, [{"id": 5, "material": "PLA"}])
        with patch("app.services.spoolman.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(return_value=resp)
            result = asyncio.run(client.list_filaments())
            assert result == [{"id": 5, "material": "PLA"}]

    def test_get_spool_unwraps_dict(self):
        client = SpoolmanClient("http://spoolman.local:7912")
        resp = _mock_resp(200, {"id": 9, "remaining_weight": 500})
        with patch("app.services.spoolman.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(return_value=resp)
            result = asyncio.run(client.get_spool(9))
            assert result == {"id": 9, "remaining_weight": 500}

    def test_get_spool_defensive_default_on_non_dict(self):
        client = SpoolmanClient("http://spoolman.local:7912")
        resp = _mock_resp(200, ["not", "a", "dict"])
        with patch("app.services.spoolman.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(return_value=resp)
            result = asyncio.run(client.get_spool(9))
            assert result == {"raw": ["not", "a", "dict"]}


class TestGetSpoolmanClient:
    def test_raises_not_configured_without_base_url(self, db_session: Session):
        with pytest.raises(SpoolmanError) as exc:
            get_spoolman_client(db_session)
        assert exc.value.code == "not_configured"

    def test_builds_from_config(self, db_session: Session):
        runtime_config.set_spoolman_config(
            db_session, base_url="http://spoolman.local:7912", api_key="k"
        )
        client = get_spoolman_client(db_session)
        assert client.base_url == "http://spoolman.local:7912"
        assert client.api_key == "k"


def _completed_job(spool_id=None, grams=None) -> PrintJob:
    return PrintJob(
        id=42,
        printer_id=1,
        file_id=1,
        model_id=1,
        remote_filename="part.gcode",
        state=PrintJobState.COMPLETED,
        spool_id=spool_id,
        filament_used_g=grams,
    )


class TestRecordSpoolUsage:
    def _enable(self, session: Session, *, write=True):
        runtime_config.set_spoolman_config(session, base_url="http://spoolman.local")
        runtime_config.set_spoolman_enabled(session, True)
        runtime_config.set_spoolman_write_enabled(session, write)

    def test_decrements_when_configured(self, db_session: Session):
        self._enable(db_session)
        job = _completed_job(spool_id=3, grams=10.0)
        with (
            patch("app.services.spoolman.active_spool_sync", return_value=None),
            patch("app.services.spoolman.use_spool_weight_sync") as mock_use,
        ):
            assert print_results.record_spool_usage(db_session, job) is True
            mock_use.assert_called_once()
            args = mock_use.call_args.args
            assert args[2] == 3 and args[3] == 10.0

    def test_skips_when_native_hook_active(self, db_session: Session):
        # Moonraker's native hook is decrementing the active spool — PrintStash
        # must not write its own decrement (would double-count).
        self._enable(db_session)
        job = _completed_job(spool_id=3, grams=10.0)
        with (
            patch("app.services.spoolman.active_spool_sync", return_value=7),
            patch("app.services.spoolman.use_spool_weight_sync") as mock_use,
        ):
            assert print_results.record_spool_usage(db_session, job) is False
            mock_use.assert_not_called()

    def test_force_overrides_native_hook(self, db_session: Session):
        # With write-force set, the operator has disabled Moonraker's decrement,
        # so PrintStash writes back even when an active spool is reported.
        self._enable(db_session)
        runtime_config.set_spoolman_write_force(db_session, True)
        job = _completed_job(spool_id=3, grams=10.0)
        with (
            patch(
                "app.services.spoolman.active_spool_sync", return_value=7
            ) as mock_active,
            patch("app.services.spoolman.use_spool_weight_sync") as mock_use,
        ):
            assert print_results.record_spool_usage(db_session, job) is True
            mock_use.assert_called_once()
            # Forced path short-circuits the probe entirely.
            mock_active.assert_not_called()

    def test_noop_without_spool(self, db_session: Session):
        self._enable(db_session)
        job = _completed_job(spool_id=None, grams=10.0)
        with patch("app.services.spoolman.use_spool_weight_sync") as mock_use:
            assert print_results.record_spool_usage(db_session, job) is False
            mock_use.assert_not_called()

    def test_noop_without_grams(self, db_session: Session):
        self._enable(db_session)
        job = _completed_job(spool_id=3, grams=None)
        with patch("app.services.spoolman.use_spool_weight_sync") as mock_use:
            assert print_results.record_spool_usage(db_session, job) is False
            mock_use.assert_not_called()

    def test_noop_when_disabled(self, db_session: Session):
        runtime_config.set_spoolman_config(db_session, base_url="http://spoolman.local")
        runtime_config.set_spoolman_enabled(db_session, False)
        job = _completed_job(spool_id=3, grams=10.0)
        with patch("app.services.spoolman.use_spool_weight_sync") as mock_use:
            assert print_results.record_spool_usage(db_session, job) is False
            mock_use.assert_not_called()

    def test_noop_when_write_disabled(self, db_session: Session):
        self._enable(db_session, write=False)
        job = _completed_job(spool_id=3, grams=10.0)
        with patch("app.services.spoolman.use_spool_weight_sync") as mock_use:
            assert print_results.record_spool_usage(db_session, job) is False
            mock_use.assert_not_called()

    def test_swallows_spoolman_error(self, db_session: Session):
        self._enable(db_session)
        job = _completed_job(spool_id=3, grams=10.0)
        with (
            patch("app.services.spoolman.active_spool_sync", return_value=None),
            patch(
                "app.services.spoolman.use_spool_weight_sync",
                side_effect=SpoolmanError("down", code="transport"),
            ),
        ):
            # Never raises — a Spoolman outage must not block the print path.
            assert print_results.record_spool_usage(db_session, job) is False


class TestUseSpoolWeightSync:
    def test_raises_on_non_2xx(self):
        with patch("app.services.spoolman.httpx.put") as mock_put:
            mock_put.return_value = _mock_resp(404, text="no spool")
            with pytest.raises(SpoolmanError, match="spoolman 404"):
                use_spool_weight_sync("http://spoolman.local", None, 9, 5.0)

    def test_raises_on_transport_error(self):
        with patch("app.services.spoolman.httpx.put") as mock_put:
            mock_put.side_effect = httpx.ConnectError("refused")
            with pytest.raises(SpoolmanError, match="transport error") as exc:
                use_spool_weight_sync("http://spoolman.local", None, 9, 5.0)
            assert exc.value.code == "transport"

    def test_sets_auth_headers_when_api_key_given(self):
        with patch("app.services.spoolman.httpx.put") as mock_put:
            mock_put.return_value = _mock_resp(200, {"id": 9})
            use_spool_weight_sync("http://spoolman.local", "secret", 9, 5.0)
            call = mock_put.call_args
            assert call.kwargs["headers"]["Authorization"] == "Bearer secret"
            assert call.kwargs["headers"]["X-Api-Key"] == "secret"


class TestActiveSpoolSync:
    def test_returns_parsed_value(self):
        with patch("app.services.spoolman.httpx.get") as mock_get:
            mock_get.return_value = _mock_resp(200, {"value": "12"})
            assert active_spool_sync("http://spoolman.local", None) == 12

    def test_returns_none_on_non_2xx(self):
        with patch("app.services.spoolman.httpx.get") as mock_get:
            mock_get.return_value = _mock_resp(500, text="down")
            assert active_spool_sync("http://spoolman.local", None) is None

    def test_returns_none_on_transport_error(self):
        with patch("app.services.spoolman.httpx.get") as mock_get:
            mock_get.side_effect = httpx.ConnectError("refused")
            assert active_spool_sync("http://spoolman.local", None) is None

    def test_returns_none_on_invalid_json(self):
        with patch("app.services.spoolman.httpx.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 200
            resp.json.side_effect = ValueError("bad json")
            mock_get.return_value = resp
            assert active_spool_sync("http://spoolman.local", None) is None

    def test_returns_none_when_value_unset(self):
        with patch("app.services.spoolman.httpx.get") as mock_get:
            mock_get.return_value = _mock_resp(200, {"value": None})
            assert active_spool_sync("http://spoolman.local", None) is None

    def test_returns_none_on_unparseable_value(self):
        with patch("app.services.spoolman.httpx.get") as mock_get:
            mock_get.return_value = _mock_resp(200, {"value": "garbage"})
            assert active_spool_sync("http://spoolman.local", None) is None

    def test_sets_auth_headers_when_api_key_given(self):
        with patch("app.services.spoolman.httpx.get") as mock_get:
            mock_get.return_value = _mock_resp(200, {"value": "3"})
            active_spool_sync("http://spoolman.local", "secret")
            call = mock_get.call_args
            assert call.kwargs["headers"]["Authorization"] == "Bearer secret"
            assert call.kwargs["headers"]["X-Api-Key"] == "secret"
