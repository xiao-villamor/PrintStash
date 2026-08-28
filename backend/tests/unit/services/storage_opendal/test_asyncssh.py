"""AsyncSSH fallback operations preserve the remote storage contract.

These tests drive each async operation through a local async client seam,
covering authentication options, recursive scans, and safe missing-file
behavior without opening a network socket.
"""

from __future__ import annotations

import asyncio
import sys
from io import BytesIO
from types import SimpleNamespace

import asyncssh
import pytest

from app.services import storage_opendal
from app.services.storage_backend import StorageConfigurationError


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


class _AsyncClient:
    def __init__(
        self, *, paths: dict[str, list[SimpleNamespace]] | None = None
    ) -> None:
        self.paths = paths or {}
        self.files: dict[str, bytes] = {}
        self.removed: list[str] = []

    async def makedirs(self, _path: str, *, exist_ok: bool) -> None:
        del exist_ok

    async def stat(self, _path: str) -> SimpleNamespace:
        return SimpleNamespace(size=None)

    async def exists(self, path: str) -> bool:
        return path in self.paths or path in self.files

    async def open(self, _path: str, _mode: str) -> _AsyncWriter:
        return _AsyncWriter()

    async def rename(self, _source: str, _destination: str) -> None:
        return None

    async def read(self, _path: str) -> bytes:
        return b"read"

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
            "root": "vault",
        }
    )


class TestAsyncSSHSFTPOperator:
    def test_rejects_missing_asyncssh(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "asyncssh", None)

        with pytest.raises(StorageConfigurationError, match="full image"):
            storage_opendal._AsyncSSHSFTPOperator(
                {"host": "host", "port": 22, "username": "user", "root": "vault"}
            )

    def test_builds_password_connection_options(self) -> None:
        operator = storage_opendal._AsyncSSHSFTPOperator(
            {
                "host": "host",
                "port": 22,
                "username": "user",
                "password": "secret",
                "root": "vault",
            }
        )

        assert operator._connection_options() == {
            "host": "host",
            "port": 22,
            "username": "user",
            "known_hosts": None,
            "password": "secret",
            "client_keys": None,
        }

    def test_builds_key_connection_options_with_passphrase(self) -> None:
        operator = storage_opendal._AsyncSSHSFTPOperator(
            {
                "host": "host",
                "port": 22,
                "username": "user",
                "private_key_path": "/tmp/key",
                "passphrase": "phrase",
                "root": "vault",
            }
        )

        assert operator._connection_options()["passphrase"] == "phrase"

    def test_checks_the_remote_root(self, monkeypatch: pytest.MonkeyPatch) -> None:
        operator = _async_operator()
        client = _AsyncClient()
        monkeypatch.setattr(
            operator, "_run", lambda operation: asyncio.run(operation(client))
        )

        assert operator.check() is None

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
