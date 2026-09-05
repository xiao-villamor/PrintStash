"""AsyncSSH fallback operations preserve the remote storage contract.

These tests drive each async operation through a local async client seam,
covering authentication options, recursive scans, and safe missing-file
behavior without opening a network socket.
"""

from __future__ import annotations

import asyncio
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import asyncssh
import pytest

from app.services import remote_io_adapters as storage_opendal
from app.services.storage_backend import (
    StorageCollisionError,
    StorageConfigurationError,
)


class _AsyncEntries:
    def __init__(self, entries: list[SimpleNamespace]) -> None:
        self._entries = iter(entries)

    def __aiter__(self) -> "_AsyncEntries":
        return self

    async def __anext__(self) -> SimpleNamespace:
        try:
            return next(self._entries)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _AsyncWriter:
    def __init__(self) -> None:
        self.parts: list[bytes] = []

    async def write(self, data: bytes) -> None:
        self.parts.append(data)

    async def close(self) -> None:
        return None


class _AsyncReader:
    async def read(self, _size: int = -1) -> bytes:
        return b"read"

    async def close(self) -> None:
        return None


class _AsyncClient:
    def __init__(
        self,
        *,
        paths: dict[str, list[SimpleNamespace]] | None = None,
        root_exists: bool = True,
        open_error: Exception | None = None,
        files: dict[str, bytes] | None = None,
    ) -> None:
        self.paths = paths or {}
        self.root_exists = root_exists
        self.open_error = open_error
        self.files = files or {}
        self.removed: list[str] = []
        self.opened_modes: list[str] = []
        self.created_dirs: list[str] = []

    async def makedirs(self, path: str, *, exist_ok: bool) -> None:
        del exist_ok
        self.created_dirs.append(path)

    async def mkdir(self, path: str) -> None:
        if not self.root_exists:
            raise asyncssh.SFTPNoSuchFile(path)
        self.created_dirs.append(path)

    async def stat(self, path: str) -> SimpleNamespace:
        if not self.root_exists and path.startswith("vault"):
            raise asyncssh.SFTPNoSuchFile("vault")
        return SimpleNamespace(size=None)

    async def exists(self, path: str) -> bool:
        return path in self.paths or path in self.files

    async def open(self, _path: str, mode: str) -> _AsyncWriter | _AsyncReader:
        if self.open_error is not None:
            raise self.open_error
        self.opened_modes.append(mode)
        return _AsyncReader() if mode == "rb" else _AsyncWriter()

    async def rename(self, _source: str, _destination: str) -> None:
        return None

    async def remove(self, path: str) -> None:
        self.removed.append(path)

    def scandir(self, path: str) -> _AsyncEntries:
        return _AsyncEntries(self.paths.get(path, []))


def _async_operator() -> storage_opendal._AsyncSSHSFTPOperator:
    return storage_opendal._AsyncSSHSFTPOperator(
        {
            "host": "sftp.example",
            "port": 22,
            "username": "user",
            "host_key": "sftp.example ssh-ed25519 AAAA",
            "root": "vault",
        }
    )


class TestAsyncSSHSFTPOperator:
    @pytest.mark.parametrize(
        "mtime,nanoseconds", [(None, None), (0, None), (10, 500_000_000)]
    )
    def test_preserves_nullable_sftp_modification_time(
        self, mtime, nanoseconds
    ) -> None:
        observed = storage_opendal._sftp_modified_at(  # noqa: SLF001
            SimpleNamespace(mtime=mtime, mtime_ns=nanoseconds)
        )
        if mtime is None:
            assert observed is None
        else:
            assert observed is not None
            assert observed.timestamp() == mtime + (nanoseconds or 0) / 1_000_000_000

    def test_rejects_missing_asyncssh(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "asyncssh", None)

        with pytest.raises(StorageConfigurationError, match="full image"):
            storage_opendal._AsyncSSHSFTPOperator(
                {"host": "host", "port": 22, "username": "user", "root": "vault"}
            )

    def test_builds_password_connection_options(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        operator = storage_opendal._AsyncSSHSFTPOperator(
            {
                "host": "host",
                "port": 22,
                "username": "user",
                "host_key": "host ssh-ed25519 AAAA",
                "password": "secret",
                "root": "vault",
            }
        )
        monkeypatch.setattr(operator, "_known_hosts", lambda: "verified-hosts")

        assert operator._connection_options() == {
            "host": "host",
            "port": 22,
            "username": "user",
            "known_hosts": "verified-hosts",
            "password": "secret",
            "client_keys": None,
        }

    def test_builds_key_connection_options_with_passphrase(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        operator = storage_opendal._AsyncSSHSFTPOperator(
            {
                "host": "host",
                "port": 22,
                "username": "user",
                "host_key": "host ssh-ed25519 AAAA",
                "private_key_path": "/tmp/key",
                "passphrase": "phrase",
                "root": "vault",
            }
        )
        monkeypatch.setattr(operator, "_known_hosts", lambda: "verified-hosts")

        assert operator._connection_options()["passphrase"] == "phrase"

    def test_rejects_missing_host_key_verification(self) -> None:
        operator = storage_opendal._AsyncSSHSFTPOperator(
            {
                "host": "host",
                "port": 22,
                "username": "user",
                "password": "secret",
                "root": "vault",
            }
        )

        with pytest.raises(StorageConfigurationError, match="sftp_host_key_required"):
            operator._connection_options()

    def test_checks_the_remote_root(self, monkeypatch: pytest.MonkeyPatch) -> None:
        operator = _async_operator()
        client = _AsyncClient()
        monkeypatch.setattr(
            operator, "_run", lambda operation: asyncio.run(operation(client))
        )

        assert operator.check() is None

    def test_provisions_the_remote_root_only_through_explicit_setup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        operator = _async_operator()
        client = _AsyncClient()
        monkeypatch.setattr(
            operator, "_run", lambda operation: asyncio.run(operation(client))
        )

        assert operator.provision_root() is None
        assert client.created_dirs == ["vault"]

    def test_checks_whether_a_remote_path_exists(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        operator = _async_operator()
        client = _AsyncClient(paths={"vault/file": []})
        monkeypatch.setattr(
            operator, "_run", lambda operation: asyncio.run(operation(client))
        )

        assert operator.exists("file") is True

    def test_writes_a_stream_when_the_root_has_no_parent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        operator = storage_opendal._AsyncSSHSFTPOperator(
            {"host": "host", "port": 22, "username": "user", "root": ""}
        )
        client = _AsyncClient()
        monkeypatch.setattr(
            operator, "_run", lambda operation: asyncio.run(operation(client))
        )

        assert operator.write_stream("file", BytesIO(b"payload")) is None

    def test_writes_bytes_through_the_stream_method(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        operator = _async_operator()
        client = _AsyncClient()
        monkeypatch.setattr(
            operator, "_run", lambda operation: asyncio.run(operation(client))
        )

        assert operator.write("file", b"payload") is None

    def test_writes_exclusively_with_sftp_x_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        operator = _async_operator()
        client = _AsyncClient()
        monkeypatch.setattr(
            operator, "_run", lambda operation: asyncio.run(operation(client))
        )

        operator.write_exclusive("file", BytesIO(b"payload"))

        assert client.opened_modes == ["xb"]

    def test_does_not_recreate_a_missing_enrolled_root_on_write(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        operator = _async_operator()
        client = _AsyncClient(root_exists=False)
        monkeypatch.setattr(
            operator, "_run", lambda operation: asyncio.run(operation(client))
        )

        with pytest.raises(asyncssh.SFTPNoSuchFile):
            operator.write_exclusive("nested/file", BytesIO(b"payload"))

        assert client.created_dirs == []

    def test_rejects_root_loss_between_preflight_descendant_creation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        operator = _async_operator()

        class _RootLossClient(_AsyncClient):
            async def stat(self, path: str) -> SimpleNamespace:
                result = await super().stat(path)
                if path == "vault":
                    self.root_exists = False
                return result

        client = _RootLossClient()
        monkeypatch.setattr(
            operator, "_run", lambda operation: asyncio.run(operation(client))
        )

        with pytest.raises(asyncssh.SFTPNoSuchFile):
            operator.write_exclusive("nested/file", BytesIO(b"payload"))

        assert client.created_dirs == []
        assert client.opened_modes == []

    def test_maps_generic_exclusive_failure_only_when_destination_exists(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        operator = _async_operator()
        client = _AsyncClient(
            files={"vault/nested/file": b"existing"},
            open_error=asyncssh.SFTPFailure("Failure"),
        )
        monkeypatch.setattr(
            operator, "_run", lambda operation: asyncio.run(operation(client))
        )

        with pytest.raises(StorageCollisionError):
            operator.write_exclusive("nested/file", BytesIO(b"payload"))

    def test_propagates_generic_exclusive_failure_when_destination_is_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        operator = _async_operator()
        client = _AsyncClient(open_error=asyncssh.SFTPFailure("Failure"))
        monkeypatch.setattr(
            operator, "_run", lambda operation: asyncio.run(operation(client))
        )

        with pytest.raises(asyncssh.SFTPFailure, match="Failure"):
            operator.write_exclusive("nested/file", BytesIO(b"payload"))

    def test_renames_a_file_when_the_root_has_no_parent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        operator = storage_opendal._AsyncSSHSFTPOperator(
            {"host": "host", "port": 22, "username": "user", "root": ""}
        )
        client = _AsyncClient()
        monkeypatch.setattr(
            operator, "_run", lambda operation: asyncio.run(operation(client))
        )

        assert operator.rename("source", "destination") is None

    def test_reads_remote_size(self, monkeypatch: pytest.MonkeyPatch) -> None:
        operator = _async_operator()
        client = _AsyncClient()
        monkeypatch.setattr(
            operator, "_run", lambda operation: asyncio.run(operation(client))
        )

        assert operator.stat("file").content_length == 0

    def test_reads_remote_bytes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        operator = _async_operator()
        client = _AsyncClient()
        monkeypatch.setattr(
            operator, "_run", lambda operation: asyncio.run(operation(client))
        )

        assert operator.read("file") == b"read"

    def test_deletes_an_existing_remote_file(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        operator = _async_operator()
        client = _AsyncClient(paths={"vault/file": []})
        monkeypatch.setattr(
            operator, "_run", lambda operation: asyncio.run(operation(client))
        )

        assert operator.delete("file") is None
        assert client.removed == ["vault/file"]

    def test_leaves_a_missing_remote_file_untouched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        operator = _async_operator()
        client = _AsyncClient()
        monkeypatch.setattr(
            operator, "_run", lambda operation: asyncio.run(operation(client))
        )

        assert operator.delete("file") is None
        assert client.removed == []

    def test_scans_nested_remote_files(self, monkeypatch: pytest.MonkeyPatch) -> None:
        operator = _async_operator()
        client = _AsyncClient(
            paths={
                "vault": [
                    SimpleNamespace(
                        filename="folder",
                        attrs=SimpleNamespace(type=asyncssh.FILEXFER_TYPE_DIRECTORY),
                    ),
                    SimpleNamespace(
                        filename="top.stl",
                        attrs=SimpleNamespace(type=asyncssh.FILEXFER_TYPE_REGULAR),
                    ),
                ],
                "vault/folder": [
                    SimpleNamespace(
                        filename="nested.stl",
                        attrs=SimpleNamespace(type=asyncssh.FILEXFER_TYPE_REGULAR),
                    )
                ],
            }
        )
        monkeypatch.setattr(
            operator, "_run", lambda operation: asyncio.run(operation(client))
        )

        assert {entry.path for entry in operator.scan("")} == {
            "folder/nested.stl",
            "top.stl",
        }

    def test_scans_nothing_when_the_remote_root_is_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        operator = _async_operator()
        client = _AsyncClient()
        monkeypatch.setattr(
            operator, "_run", lambda operation: asyncio.run(operation(client))
        )

        assert operator.scan("") == []

    def test_reads_a_known_hosts_file(self, tmp_path: Path) -> None:
        operator = _async_operator()
        hosts = tmp_path / "known_hosts"
        hosts.write_text("host ssh-ed25519 AAAA\n")
        operator._host_key = str(hosts)

        assert operator._known_hosts() == str(hosts)

    def test_rejects_an_invalid_known_hosts_catalogue(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        operator = _async_operator()
        monkeypatch.setattr(
            asyncssh,
            "import_known_hosts",
            lambda _value: (_ for _ in ()).throw(ValueError("invalid")),
        )
        operator._host_key = "invalid catalogue"

        with pytest.raises(StorageConfigurationError, match="host_key_invalid"):
            operator._known_hosts()

    def test_builds_key_options_without_a_passphrase(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        operator = storage_opendal._AsyncSSHSFTPOperator(
            {
                "host": "host",
                "port": 22,
                "username": "user",
                "host_key": "host ssh-ed25519 AAAA",
                "private_key_path": "/tmp/key",
                "root": "vault",
            }
        )
        monkeypatch.setattr(operator, "_known_hosts", lambda: "known")

        assert operator._connection_options()["client_keys"] == ["/tmp/key"]

    def test_runs_an_asyncssh_operation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class ClientContext:
            async def __aenter__(self):
                return "client"

            async def __aexit__(self, *_args):
                return None

        class ConnectionContext:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            def start_sftp_client(self):
                return ClientContext()

        monkeypatch.setattr(asyncssh, "connect", lambda **_kwargs: ConnectionContext())
        operator = _async_operator()
        monkeypatch.setattr(operator, "_connection_options", lambda: {})

        async def operation(client):
            return client

        assert asyncio.run(operator._perform(operation)) == "client"

    def test_runs_an_asyncssh_runner(self, monkeypatch: pytest.MonkeyPatch) -> None:
        operator = _async_operator()
        monkeypatch.setattr(
            operator, "_perform", lambda _operation: asyncio.sleep(0, result="done")
        )

        assert operator._run(lambda _client: None) == "done"

    def test_closes_asyncssh_resources_after_streaming(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Reader:
            def __init__(self) -> None:
                self.chunks = iter([b"ab", b"c", b""])
                self.closed = False

            async def read(self, _size: int) -> bytes:
                return next(self.chunks)

            async def close(self) -> None:
                self.closed = True

        class Client:
            def __init__(self, reader: Reader) -> None:
                self.reader = reader
                self.exited = False

            async def open(self, _path: str, _mode: str) -> Reader:
                return self.reader

            def exit(self) -> None:
                self.exited = True

            async def wait_closed(self) -> None:
                return None

        class Connection:
            def __init__(self, client: Client) -> None:
                self.client = client
                self.closed = False

            async def start_sftp_client(self) -> Client:
                return self.client

            def close(self) -> None:
                self.closed = True

            def abort(self) -> None:
                self.close()

            async def wait_closed(self) -> None:
                return None

        reader = Reader()
        client = Client(reader)
        connection = Connection(client)
        monkeypatch.setattr(
            asyncssh, "connect", lambda **_kwargs: asyncio.sleep(0, result=connection)
        )
        operator = _async_operator()
        monkeypatch.setattr(operator, "_connection_options", lambda: {})

        assert list(operator.stream_chunks("file", 2)) == [b"ab", b"c"]
        assert reader.closed is True
        assert client.exited is True
        assert connection.closed is True

    def test_skips_non_file_scan_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class Entries:
            def __init__(self, values: list[SimpleNamespace]) -> None:
                self.values = iter(values)

            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(self.values)
                except StopIteration as exc:
                    raise StopAsyncIteration from exc

        class Client:
            async def exists(self, path: str) -> bool:
                return path == "vault"

            def scandir(self, _path: str):
                return Entries(
                    [
                        SimpleNamespace(filename="", attrs=SimpleNamespace(type=0)),
                        SimpleNamespace(filename=".", attrs=SimpleNamespace(type=0)),
                        SimpleNamespace(filename="..", attrs=SimpleNamespace(type=0)),
                        SimpleNamespace(
                            filename="folder", attrs=SimpleNamespace(type=0)
                        ),
                    ]
                )

        operator = _async_operator()
        monkeypatch.setattr(
            operator, "_run", lambda operation: asyncio.run(operation(Client()))
        )
        original_join = storage_opendal.posixpath.join
        monkeypatch.setattr(
            storage_opendal.posixpath,
            "join",
            lambda *parts: "" if parts == ("", "folder") else original_join(*parts),
        )

        assert operator.scan("") == []

    def test_skips_an_already_visited_scan_directory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        operator = _async_operator()
        visited = {"folder"}

        class Client:
            async def exists(self, _path: str) -> bool:
                return True

            def scandir(self, _path: str):
                return _AsyncEntries(
                    [
                        SimpleNamespace(
                            filename="loop",
                            attrs=SimpleNamespace(
                                type=asyncssh.FILEXFER_TYPE_DIRECTORY
                            ),
                        )
                    ]
                )

        monkeypatch.setattr(
            operator, "_run", lambda operation: asyncio.run(operation(Client()))
        )
        monkeypatch.setattr(storage_opendal.posixpath, "join", lambda *_parts: "folder")

        assert operator.scan("folder") == []
        assert visited == {"folder"}
