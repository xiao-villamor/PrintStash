"""Guards that keep a tier honest about what it touches.

The tier of a test is its directory (see ``create-tests``), and a directory only means
something if it is enforced. These two guards are the enforcement: a test that reaches
for the database from ``unit/``, or opens a real connection from ``unit/`` or
``integration/``, fails on the spot with a message that names the tier it belongs in.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Iterator

import pytest

DB_FIXTURES = frozenset(
    {
        "db_session",
        "client",
        "app",
        "hub",
        "threaded_hub_db",
        "auth_headers",
        "db_factory",
    }
)


def forbid_db_fixtures(request: pytest.FixtureRequest) -> None:
    """Fail a ``unit/`` test that asks for the database or the app."""
    used = DB_FIXTURES.intersection(request.fixturenames)
    if used:
        pytest.fail(
            f"{request.node.nodeid} is under tests/unit/ but requests "
            f"{', '.join(sorted(used))}. A test that needs a real engine, router or "
            "session is an integration test: move it to tests/integration/"
            f"{_mirror_hint(request)}.",
            pytrace=False,
        )


def _mirror_hint(request: pytest.FixtureRequest) -> str:
    parts = request.node.path.parts
    if "unit" in parts:
        tail = parts[parts.index("unit") + 1 : -1]
        if tail:
            return "/" + "/".join(tail)
    return ""


_LOCAL_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})


def _resolves_without_dns(host: str) -> bool:
    """True for a name the resolver answers from the host itself.

    An IP literal performs no lookup at all, and ``localhost`` is answered from
    ``/etc/hosts``. The SSRF guard resolves both — ``127.0.0.1``, ``169.254.169.254``,
    ``localhost`` — to prove it *rejects* them, so blocking those would break the very
    tests that keep the guard honest. Anything else is a DNS query over the network.
    """
    name = host.strip("[]").rstrip(".").lower()
    if name in _LOCAL_NAMES or name.endswith(".localhost"):
        return True
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


class RealNetworkAccess(RuntimeError):
    """Raised when a unit or integration test tries to reach the network."""


def _blocked(node_id: str, what: str, target: object) -> RealNetworkAccess:
    return RealNetworkAccess(
        f"{node_id} tried to {what} ({target!r}). Nothing under tests/unit/ or "
        "tests/integration/ may open a real connection — loopback included. Drive a "
        "contract-enforcing fake over a real socket from tests/contract/ instead, or "
        "stand in for the egress boundary (patch get_http_client where it is used)."
    )


# Resource markers *mean* "this test needs a real server", so exempting them from
# the network guard is the point rather than a loophole: the server is a container
# `tests/containers.py` started for the run, and a test that may not reach it
# cannot assert anything about it. The exemption stays narrow because the markers
# do — two of them, both naming a service the suite owns.
_RESOURCE_MARKERS = frozenset({"postgres", "s3"})


@pytest.fixture(autouse=True)
def block_real_network(request: pytest.FixtureRequest) -> Iterator[None]:
    """Fail the test on any real connect or DNS lookup.

    Exempts tests carrying a resource marker — a `postgres` or `s3` test is an
    integration test whose whole point is a real server on loopback.
    """
    if any(request.node.get_closest_marker(name) for name in _RESOURCE_MARKERS):
        yield
        return
    node_id = request.node.nodeid
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def connect(self: socket.socket, address: object) -> None:
        raise _blocked(node_id, "open a socket", address)

    def connect_ex(self: socket.socket, address: object) -> int:
        raise _blocked(node_id, "open a socket", address)

    def getaddrinfo(
        host: object, port: object, *args: object, **kwargs: object
    ) -> object:
        if isinstance(host, str) and _resolves_without_dns(host):
            return real_getaddrinfo(host, port, *args, **kwargs)
        raise _blocked(node_id, "resolve a hostname", host)

    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = getaddrinfo  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket.connect = real_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = real_connect_ex  # type: ignore[method-assign]
        socket.getaddrinfo = real_getaddrinfo  # type: ignore[assignment]
