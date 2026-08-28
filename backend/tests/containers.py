"""The real PostgreSQL and S3 services a slice of this suite runs against.

Containers are how those tests get their service — the only way. There is no
environment variable to set, no `docker run` to remember, and no second code path
that behaves differently in CI than it does on a laptop. That is the point: the
previous arrangement had three definitions of the same two services (a GitHub
`services:` block, a `docker run` in the workflow, and a documented invocation in
the README), which is three things to keep in step and three ways for a local run
to disagree with CI. Now the image, the command and the readiness check are
written once, here.

What that buys is that `./scripts/test.sh full` runs the *whole* suite. It used to
be green with 21 tests skipped, and they were not incidental ones: the
dialect-sensitive SQL, the migration path a self-hoster upgrades through, and the
S3 storage and backup destinations. A local run that skips those is not the suite
CI runs, so "it passed for me" and "it passed in CI" stopped meaning the same
thing.

Containers start **lazily** — on the first selected test that carries the marker,
never at collection — and stop once at session end. A run that touches neither
resource starts nothing, so the fast lane pays nothing for this.

**No Docker is an error, not a skip.** A run that selected these tests and could
not start their services did not verify what it was asked to verify, and
reporting that as green is the exact degradation this suite is built to prevent:
the previous arrangement passed locally with 21 tests quietly absent, and those 21
were the dialect SQL, the upgrade path and the object store. So the session stops
with a message naming the prerequisite. Selecting a subset that needs neither
service — the `fast` lane, or any single unit file — is unaffected, because the
check only fires for markers a *selected* test carries.
"""

from __future__ import annotations

from typing import Any, Callable, NoReturn

import pytest

# SeaweedFS in `mini` mode: master, volume server and S3 gateway in one process.
# Pinned by digest so a green run here and a green run in CI are the same run, and
# given a development-sized volume limit so it allocates in seconds rather than
# reserving gigabytes.
SEAWEEDFS_IMAGE = (
    "chrislusf/seaweedfs:4.41"
    "@sha256:43b768cd62b00d132439cda881b93fd1adebf1b315e996e794087743821d771d"
)
SEAWEEDFS_S3_PORT = 8333
SEAWEEDFS_COMMAND = (
    "mini -dir=/data -master.volumeSizeLimitMB=64 -master.telemetry=false"
)
# The gateway's own readiness line. SeaweedFS binds the port before the S3 API can
# answer, so a port check races and the first request comes back as a connection
# reset.
SEAWEEDFS_READY_LOG = "Start Seaweed S3 API"

S3_RESOURCE = "SeaweedFS (the S3 endpoint)"
POSTGRES_RESOURCE = "PostgreSQL"

POSTGRES_IMAGE = "postgres:16-alpine"
POSTGRES_USER = "printstash"
POSTGRES_PASSWORD = "printstash"
POSTGRES_DB = "printstash"

# Obviously-fake credentials. SeaweedFS accepts whatever it is given; nothing here
# may resemble a real key.
S3_ACCESS_KEY = "printstash"
S3_SECRET_KEY = "printstash-secret"

# Generous, because these run on a cold image pull in CI and on a laptop that may
# be starting Docker at the same time. A tight timeout here is a flake.
STARTUP_TIMEOUT_S = 180

_started: list[Any] = []
_resolved: dict[str, str | None] = {}


def docker_available() -> bool:
    """Whether a Docker daemon is reachable, without raising if it is not.

    Checked rather than assumed so that a machine with no daemon gets the message
    below instead of a connection error out of the middle of a plugin stack.
    """
    try:
        from testcontainers.core.docker_client import DockerClient
    except ImportError:
        return False
    try:
        DockerClient().client.ping()
    except Exception:
        return False
    return True


def _resolve(key: str, resource: str, start: Callable[[], str]) -> str:
    """Start the container for *key* once per session and return its URL.

    Raises rather than returning `None`: a caller that reached here needs the
    service, and handing back nothing would let the test skip itself.
    """
    if key not in _resolved:
        require_docker(resource)
        _resolved[key] = start()
    return _resolved[key]


def require_docker(resource: str) -> None | NoReturn:
    """Stop the run when *resource* cannot be started.

    A hard stop rather than a skip. The session was asked to verify something it
    cannot verify, and a green result with the tests quietly absent is worse than
    no result — that is how a suite degrades without anybody deciding to let it.

    `pytest.exit` rather than an exception so the reader gets the message and a
    non-zero status instead of a traceback through the plugin stack.
    """
    if docker_available():
        return None
    pytest.exit(
        f"cannot start {resource}: no Docker daemon is reachable. "
        "These tests run against the real service — the dialect-sensitive SQL, the "
        "migration path self-hosters upgrade through, and the S3 storage and backup "
        "destinations — so there is nothing to fall back to, and skipping them would "
        "report a green run that verified none of it. "
        "Start Docker and re-run, or select a subset that needs neither service "
        "(./scripts/test.sh fast).",
        returncode=1,
    )


def _start_postgres() -> str:
    from testcontainers.community.postgres import PostgresContainer

    container = PostgresContainer(
        POSTGRES_IMAGE,
        username=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        dbname=POSTGRES_DB,
    )
    container.start()
    _started.append(container)
    # `driver=None` asks for the plain `postgresql://` form. The app normalises the
    # scheme to whichever driver it needs, so handing it a pre-selected one would
    # test a URL shape production never sees.
    return container.get_connection_url(driver=None)


def _start_seaweedfs() -> str:
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.wait_strategies import LogMessageWaitStrategy

    container = (
        DockerContainer(SEAWEEDFS_IMAGE)
        .with_env("AWS_ACCESS_KEY_ID", S3_ACCESS_KEY)
        .with_env("AWS_SECRET_ACCESS_KEY", S3_SECRET_KEY)
        .with_exposed_ports(SEAWEEDFS_S3_PORT)
        .with_command(SEAWEEDFS_COMMAND)
        .waiting_for(
            LogMessageWaitStrategy(SEAWEEDFS_READY_LOG).with_startup_timeout(
                STARTUP_TIMEOUT_S
            )
        )
    )
    container.start()
    _started.append(container)
    host = container.get_container_host_ip()
    port = container.get_exposed_port(SEAWEEDFS_S3_PORT)
    return f"http://{host}:{port}"


def postgres_url() -> str:
    """A real PostgreSQL URL. Raises when Docker is not running."""
    return _resolve("postgres", POSTGRES_RESOURCE, _start_postgres)


def s3_endpoint() -> str:
    """A real S3-compatible endpoint URL. Raises when Docker is not running."""
    return _resolve("s3", S3_RESOURCE, _start_seaweedfs)


def shutdown_containers() -> None:
    """Stop whatever was started, once, at the end of the session."""
    while _started:
        container = _started.pop()
        try:
            container.stop()
        except Exception:
            # A container that already died takes nothing with it, and raising here
            # would turn a clean run into a session-teardown error.
            pass
