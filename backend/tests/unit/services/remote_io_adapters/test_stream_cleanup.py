"""A streaming SFTP read owns resources even when acquisition only partly succeeds.

Transport faults are injected before each acquisition and during reads/cleanup;
the observed outcome is that every acquired resource is closed, including the
private event loop, without replacing the streaming implementation.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace

import asyncssh
import pytest

from app.services.remote_io_adapters import _AsyncSSHSFTPOperator


@dataclass
class StreamResources:
    observations: int = 0
    fail_at: str = ""
    acquired: set[str] = field(default_factory=set)
    closed: set[str] = field(default_factory=set)
    chunks: list[bytes] = field(default_factory=lambda: [b"first", b"last", b""])

    def fail(self, stage: str) -> None:
        if self.fail_at == stage:
            raise OSError(f"stream fault: {stage}")


class Reader:
    def __init__(self, resources: StreamResources) -> None:
        self.resources = resources

    async def read(self, size: int) -> bytes:
        self.resources.fail("read")
        return self.resources.chunks.pop(0)

    async def close(self) -> None:
        self.resources.closed.add("reader")
        self.resources.fail("reader_close")


class Client:
    def __init__(self, resources: StreamResources) -> None:
        self.resources = resources

    async def open(self, path: str, mode: str) -> Reader:
        self.resources.fail("open")
        self.resources.acquired.add("reader")
        return Reader(self.resources)

    async def scandir(self, path):
        self.resources.acquired.add("listing")
        try:
            for index in range(100_000):
                self.resources.fail("listing")
                self.resources.observations += 1
                yield SimpleNamespace(
                    filename=f"{index}.stl",
                    attrs=SimpleNamespace(type=1, size=3, mtime=None),
                )
        finally:
            self.resources.closed.add("listing")

    def exit(self) -> None:
        self.resources.closed.add("client")

    async def wait_closed(self) -> None:
        self.resources.fail("client_close")


class Connection:
    def __init__(self, resources: StreamResources) -> None:
        self.resources = resources

    async def start_sftp_client(self) -> Client:
        self.resources.fail("client")
        self.resources.acquired.add("client")
        return Client(self.resources)

    def close(self) -> None:
        self.resources.closed.add("connection")

    def abort(self) -> None:
        self.close()

    async def wait_closed(self) -> None:
        self.resources.fail("connection_close")


@pytest.fixture
def stream_resources(monkeypatch: pytest.MonkeyPatch):
    resources = StreamResources()
    loop = asyncio.new_event_loop()

    async def connect(**options: object) -> Connection:
        resources.fail("connect")
        resources.acquired.add("connection")
        return Connection(resources)

    monkeypatch.setattr(asyncssh, "connect", connect)
    monkeypatch.setattr(asyncio, "new_event_loop", lambda: loop)
    operator = _AsyncSSHSFTPOperator(
        {
            "host": "example.invalid",
            "port": 22,
            "username": "contract-only",
            "root": "vault",
            "host_key": "contract-only",
        }
    )
    monkeypatch.setattr(operator, "_connection_options", lambda: {})
    try:
        yield operator, resources, loop
    finally:
        loop.close()


class TestStreamChunks:
    def test_reader_reaches_eof_without_losing_chunk_boundaries(
        self, stream_resources
    ) -> None:
        operator, resources, loop = stream_resources

        with operator.open("archive.tar.gz", "rb") as reader:
            assert reader.read(7) == b"firstla"
            assert reader.read() == b"st"
            assert reader.read(1) == b""

        assert resources.closed == resources.acquired
        assert loop.is_closed()

    def test_reader_rejects_a_write_mode_without_connecting(
        self, stream_resources
    ) -> None:
        from app.services.storage_backend import StorageConfigurationError

        operator, resources, _ = stream_resources
        with pytest.raises(
            StorageConfigurationError, match="sftp_stream_mode_unsupported"
        ):
            operator.open("archive.tar.gz", "wb")

        assert not resources.acquired

    def test_reader_normalizes_cleanup_failures(self, stream_resources) -> None:
        from app.services.storage_backend import StorageConfigurationError

        operator, resources, loop = stream_resources
        with pytest.raises(
            StorageConfigurationError, match="remote_storage_read_failed"
        ):
            with operator.open("archive.tar.gz", "rb") as reader:
                reader.read(1)
                resources.fail_at = "reader_close"

        assert resources.closed == resources.acquired
        assert loop.is_closed()

    def test_reader_leaves_unconsumed_archive_bytes_remote(
        self, stream_resources
    ) -> None:
        operator, resources, loop = stream_resources
        resources.chunks = [b"x" * 65536, b"unconsumed", b""]

        with operator.open("archive.tar.gz", "rb") as reader:
            assert reader.read(10) == b"x" * 10
            assert resources.chunks == [b"unconsumed", b""]

        assert resources.closed == resources.acquired
        assert loop.is_closed()

    def test_reader_normalizes_transport_failures(self, stream_resources) -> None:
        from app.services.storage_backend import StorageConfigurationError

        operator, resources, loop = stream_resources
        resources.fail_at = "read"

        with pytest.raises(
            StorageConfigurationError, match="remote_storage_read_failed"
        ):
            with operator.open("archive.tar.gz", "rb") as reader:
                reader.read(10)

        assert resources.closed == resources.acquired
        assert loop.is_closed()

    def test_closes_resources_after_early_consumer_exit(self, stream_resources) -> None:
        operator, resources, loop = stream_resources
        stream = operator.stream_chunks("file", 1024)

        assert next(stream) == b"first"
        stream.close()

        assert resources.closed == resources.acquired
        assert loop.is_closed()

    def test_closes_resources_after_cancellation(self, stream_resources) -> None:
        operator, resources, loop = stream_resources
        stream = operator.stream_chunks("file", 1024)
        next(stream)

        with pytest.raises(asyncio.CancelledError):
            stream.throw(asyncio.CancelledError())

        assert resources.closed == resources.acquired
        assert loop.is_closed()

    @pytest.mark.parametrize(
        "stage",
        ["connect", "client", "open", "read"],
        ids=str,
    )
    def test_closes_acquired_resources_after_transport_failure(
        self,
        stream_resources,
        stage: str,
    ) -> None:
        operator, resources, loop = stream_resources
        resources.fail_at = stage

        with pytest.raises(OSError, match=f"stream fault: {stage}"):
            list(operator.stream_chunks("file", 1024))

        assert resources.closed == resources.acquired
        assert loop.is_closed()

    @pytest.mark.parametrize(
        "stage",
        ["reader_close", "client_close", "connection_close"],
        ids=str,
    )
    def test_finishes_remaining_cleanup_after_a_close_failure(
        self,
        stream_resources,
        stage: str,
    ) -> None:
        operator, resources, loop = stream_resources
        resources.fail_at = stage

        with pytest.raises(OSError, match=f"stream fault: {stage}"):
            list(operator.stream_chunks("file", 1024))

        assert resources.closed == resources.acquired
        assert loop.is_closed()


class TestDirectoryStream:
    @pytest.mark.parametrize("exit_kind", ["early", "cancel", "failure"])
    def test_incremental_listing_releases_every_acquired_resource(
        self, stream_resources, exit_kind
    ):
        from app.services.remote_io_adapters import remote_io_for
        from app.services.storage_backend import StorageConfigurationError
        from app.services.storage_providers import TransportKind, TransportSpec

        operator, resources, loop = stream_resources
        remote = remote_io_for(
            TransportSpec(
                kind=TransportKind.SFTP, provider="sftp", namespace="vault", options={}
            ),
            operator=operator,
        )

        def consume():
            with remote.iter_directory("models") as entries:
                assert next(entries).key == "models/0.stl"
                assert resources.observations == 1
                if exit_kind == "cancel":
                    raise asyncio.CancelledError()
                if exit_kind == "failure":
                    resources.fail_at = "listing"
                    next(entries)

        if exit_kind == "early":
            consume()
        elif exit_kind == "cancel":
            with pytest.raises(asyncio.CancelledError):
                consume()
        else:
            with pytest.raises(
                StorageConfigurationError, match="remote_storage_list_failed"
            ):
                consume()
        assert resources.observations == 1
        assert resources.closed == resources.acquired
        assert loop.is_closed()
