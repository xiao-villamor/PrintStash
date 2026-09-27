"""The NOTIFY bus against a real PostgreSQL server.

In a split topology a worker has no websockets: it publishes with
``pg_notify`` and the API, holding one ``LISTEN`` connection, delivers each
notice to its local subscribers. The server is real (a container for the run),
and so are the faults: a malformed notice is one another client sent, and a
dropped listener is its backend terminated by the server. After a reconnect
every subscriber is told to ``resync``, because notices sent while the
listener was down are gone.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlmodel import create_engine

from app.db.session import (
    SQLiteSessionFactory,
    get_session_factory,
    override_session_factory,
)
from app.db.url import normalize_database_url
from app.runtime.realtime import NOTIFY_CHANNEL, PostgresNotifyBus
from tests.containers import postgres_url

pytestmark = pytest.mark.postgres

_WAIT_S = 10.0


@pytest.fixture(scope="module")
def server_url() -> str:
    return normalize_database_url(postgres_url())


@pytest.fixture
def engine(server_url: str) -> Iterator:
    engine = create_engine(server_url, pool_pre_ping=True)
    previous = get_session_factory()
    override_session_factory(SQLiteSessionFactory(engine))
    try:
        yield engine
    finally:
        override_session_factory(previous)
        engine.dispose()


class Inbox:
    def __init__(self) -> None:
        self.received: list[dict] = []

    async def __call__(self, payload: dict) -> None:
        self.received.append(payload)

    async def until(self, count: int) -> list[dict]:
        async with asyncio.timeout(_WAIT_S):
            while len(self.received) < count:
                await asyncio.sleep(0.02)
        return self.received


async def _listening(bus: PostgresNotifyBus, engine) -> None:
    """Wait until the listener's LISTEN is registered on the server."""
    async with asyncio.timeout(_WAIT_S):
        while True:
            with engine.connect() as connection:
                listening = connection.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE query = :query AND pid <> pg_backend_pid()"
                    ),
                    {"query": f"LISTEN {NOTIFY_CHANNEL}"},
                ).scalar_one()
            if listening:
                return
            await asyncio.sleep(0.05)


def _run(scenario) -> None:
    asyncio.run(scenario())


class TestDelivery:
    def test_a_workers_notice_reaches_the_apis_subscriber(
        self, engine, server_url: str
    ) -> None:
        async def scenario() -> None:
            api = PostgresNotifyBus(server_url, listen=True)
            worker = PostgresNotifyBus(server_url, listen=False)
            inbox = Inbox()
            await api.start()
            try:
                await api.subscribe("jobs:7", inbox)
                await _listening(api, engine)

                await worker.publish("jobs:7", {"type": "job", "job_id": "j1"})

                assert await inbox.until(1) == [{"type": "job", "job_id": "j1"}]
            finally:
                await api.stop()

        _run(scenario)

    def test_publishing_from_a_job_thread_is_delivered(
        self, engine, server_url: str
    ) -> None:
        # Job steps run on engine threads with no event loop of their own.
        async def scenario() -> None:
            api = PostgresNotifyBus(server_url, listen=True)
            worker = PostgresNotifyBus(server_url, listen=False)
            inbox = Inbox()
            await api.start()
            try:
                await api.subscribe("model:3", inbox)
                await _listening(api, engine)

                await asyncio.to_thread(
                    worker.publish_threadsafe, "model:3", {"type": "derivative"}
                )

                assert await inbox.until(1) == [{"type": "derivative"}]
            finally:
                await api.stop()

        _run(scenario)

    def test_a_subscriber_hears_only_its_channels(
        self, engine, server_url: str
    ) -> None:
        async def scenario() -> None:
            api = PostgresNotifyBus(server_url, listen=True)
            mine, theirs = Inbox(), Inbox()
            await api.start()
            try:
                await api.subscribe("jobs:1", mine)
                await api.subscribe("jobs:2", theirs)
                await _listening(api, engine)

                await api.publish("jobs:2", {"n": 1})
                await theirs.until(1)

                assert mine.received == []
            finally:
                await api.stop()

        _run(scenario)

    def test_an_unsubscribed_sink_hears_nothing_more(
        self, engine, server_url: str
    ) -> None:
        async def scenario() -> None:
            api = PostgresNotifyBus(server_url, listen=True)
            gone, staying = Inbox(), Inbox()
            await api.start()
            try:
                await api.subscribe("jobs:1", gone)
                await api.subscribe("jobs:1", staying)
                await _listening(api, engine)
                await api.unsubscribe("jobs:1", gone)

                await api.publish("jobs:1", {"n": 1})
                await staying.until(1)

                assert gone.received == []
            finally:
                await api.stop()

        _run(scenario)

    def test_a_malformed_notice_is_ignored(self, engine, server_url: str) -> None:
        # Another client can NOTIFY our channel with anything at all.
        async def scenario() -> None:
            api = PostgresNotifyBus(server_url, listen=True)
            inbox = Inbox()
            await api.start()
            try:
                await api.subscribe("jobs:1", inbox)
                await _listening(api, engine)
                with engine.begin() as connection:
                    for body in ("not json", '{"c": "jobs:1"}', "[1, 2]"):
                        connection.execute(
                            text("SELECT pg_notify(:channel, :body)"),
                            {"channel": NOTIFY_CHANNEL, "body": body},
                        )

                await api.publish("jobs:1", {"n": 1})

                assert await inbox.until(1) == [{"n": 1}]
            finally:
                await api.stop()

        _run(scenario)


def _drop_listener(engine) -> None:
    """Terminate the listener's backend, as a server restart or failover would."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE query = :query AND pid <> pg_backend_pid()"
            ),
            {"query": f"LISTEN {NOTIFY_CHANNEL}"},
        )


class TestReconnect:
    def test_clients_are_told_to_resync_after_a_dropped_listener(
        self, engine, server_url: str
    ) -> None:
        async def scenario() -> None:
            api = PostgresNotifyBus(server_url, listen=True)
            inbox = Inbox()
            await api.start()
            try:
                await api.subscribe("jobs:1", inbox)
                await _listening(api, engine)

                _drop_listener(engine)

                assert await inbox.until(1) == [{"type": "resync"}]
            finally:
                await api.stop()

        _run(scenario)

    def test_delivery_resumes_once_the_listener_is_back(
        self, engine, server_url: str
    ) -> None:
        async def scenario() -> None:
            api = PostgresNotifyBus(server_url, listen=True)
            inbox = Inbox()
            await api.start()
            try:
                await api.subscribe("jobs:1", inbox)
                await _listening(api, engine)
                _drop_listener(engine)
                await inbox.until(1)

                await api.publish("jobs:1", {"n": 1})

                assert (await inbox.until(2))[1] == {"n": 1}
            finally:
                await api.stop()

        _run(scenario)

    def test_stopping_releases_the_listen_connection(
        self, engine, server_url: str
    ) -> None:
        async def scenario() -> None:
            api = PostgresNotifyBus(server_url, listen=True)
            await api.start()
            await _listening(api, engine)

            await api.stop()

            async with asyncio.timeout(_WAIT_S):
                while True:
                    with engine.connect() as connection:
                        remaining = connection.execute(
                            text(
                                "SELECT count(*) FROM pg_stat_activity "
                                "WHERE query = :query AND pid <> pg_backend_pid()"
                            ),
                            {"query": f"LISTEN {NOTIFY_CHANNEL}"},
                        ).scalar_one()
                    if not remaining:
                        return
                    await asyncio.sleep(0.05)

        _run(scenario)
