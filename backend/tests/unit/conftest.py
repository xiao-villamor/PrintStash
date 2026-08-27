"""Unit-tier guards keep database, app, and network behavior out of unit tests."""

from __future__ import annotations

import socket

import pytest

_INTEGRATION_FIXTURES = frozenset({"app", "client", "db_session"})


@pytest.fixture(autouse=True)
def _reject_integration_fixtures(request: pytest.FixtureRequest) -> None:
    forbidden = _INTEGRATION_FIXTURES.intersection(request.fixturenames)
    if forbidden:
        names = ", ".join(sorted(forbidden))
        pytest.fail(f"unit test requested integration fixture(s): {names}")


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("unit tests may not open sockets, including loopback")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
