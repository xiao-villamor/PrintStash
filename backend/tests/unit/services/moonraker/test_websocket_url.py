"""Moonraker HTTP endpoints derive the matching WebSocket transport URL."""

from __future__ import annotations

import pytest

from app.services.moonraker import MoonrakerClient


class TestMoonrakerWsUrl:
    @pytest.mark.parametrize(
        "base, expected",
        [
            ("https://printer:7125", "wss://printer:7125/websocket"),
            ("http://printer:7125", "ws://printer:7125/websocket"),
            ("http://printer:7125/", "ws://printer:7125/websocket"),  # trailing slash
            ("http://10.0.0.5", "ws://10.0.0.5/websocket"),
        ],
    )
    def test_scheme_swap(self, base: str, expected: str) -> None:
        assert MoonrakerClient(base)._ws_url() == expected
