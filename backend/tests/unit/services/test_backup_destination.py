"""Provider-independent safety rules for OpenDAL backup destinations."""

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.db.models import (
    LibrarySourceKind,
    OwnedStorageObject,
    StorageConnection,
    StorageConnectionPurpose,
    StorageObjectState,
)
from app.services import backup_destination
from app.services.backup_destination import (
    BackupDestinationError,
    RemoteBackupDestination,
)
from app.services.storage_backend import StorageConfigurationError, StorageObjectInfo


class _Backend:
    backend_name = "backup-opendal-gdrive"
    source_namespace = "gdrive/PrintStash"

    def __init__(
        self, payload: bytes = b"archive", *, check_error: Exception | None = None
    ) -> None:
        self.payload = payload
        self.info = StorageObjectInfo(size=len(payload), etag="etag")
        self.check_error = check_error
        self.deleted_keys: list[str] = []

    def check(self) -> None:
        if self.check_error is not None:
            raise self.check_error

    def object_info(self, _key: str):
        return self.info

    def stream_chunks(self, _key: str):
        yield self.payload[:3]
        yield self.payload[3:]

    @contextmanager
    def open_reader(self, _key: str):
        from io import BytesIO

        yield BytesIO(self.payload)

    @property
    def operator_capabilities(self):
        return SimpleNamespace(delete_with_version=False)

    def delete_versioned(self, _key: str, _version: str) -> None:
        raise StorageConfigurationError("conditional_delete_unavailable")

    def delete_owned_unversioned(
        self, key: str, *, expected_size: int, expected_etag: str | None
    ) -> None:
        assert expected_size == len(self.payload)
        assert expected_etag == "etag"
        self.deleted_keys.append(key)


def _destination(backend: _Backend) -> RemoteBackupDestination:
    return RemoteBackupDestination(
        connection_id=1,
        name="Drive",
        provider="gdrive",
        backend=backend,  # type: ignore[arg-type]
        provider_ref="provider-ref",
    )


def _row(payload: bytes = b"archive") -> OwnedStorageObject:
    import hashlib

    return OwnedStorageObject(
        backend="backup-opendal-gdrive",
        namespace="gdrive/PrintStash",
        key="gdrive/PrintStash/printstash-backups/a.tar.gz",
        provider_ref="provider-ref",
        object_kind="backup",
        state=StorageObjectState.COMMITTED,
        token="token",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        etag="etag",
    )


class TestDownloadOwned:
    def test_requires_verified_ownership(self, tmp_path: Path) -> None:
        destination = _destination(_Backend())
        output = tmp_path / "backup.tar.gz"

        destination.download_owned(_row(), output)

        assert output.read_bytes() == b"archive"

    def test_rejects_changed_remote_identity(self, tmp_path: Path) -> None:
        backend = _Backend()
        backend.info = StorageObjectInfo(size=7, etag="replacement")

        with pytest.raises(BackupDestinationError, match="identity_mismatch"):
            _destination(backend).download_owned(_row(), tmp_path / "backup.tar.gz")

    def test_requires_a_persisted_content_hash(self, tmp_path: Path) -> None:
        row = _row()
        row.sha256 = None

        with pytest.raises(BackupDestinationError, match="ownership_unverified"):
            _destination(_Backend()).download_owned(row, tmp_path / "backup.tar.gz")


class TestDeleteOwned:
    def test_unguarded_destination_is_never_deleted_by_retention(self) -> None:
        row = _row()
        row.version_id = "version"
        backend = _Backend()
        backend.info = StorageObjectInfo(size=7, etag="etag", version_id="version")

        assert _destination(backend).delete_owned(row) is False

    def test_explicitly_deletes_an_owned_unversioned_backup(self) -> None:
        row = _row()
        backend = _Backend()

        deleted = _destination(backend).delete_owned(row, allow_unversioned=True)

        assert deleted is True
        assert backend.deleted_keys == [row.key]


class TestProbe:
    def test_reports_a_remote_connection_failure(self) -> None:
        destination = _destination(_Backend(check_error=RuntimeError("oauth rejected")))

        with pytest.raises(
            BackupDestinationError, match="storage_connection_probe_failed"
        ):
            destination.probe()


class TestDestinationFromConnection:
    def test_identity_is_bound_to_the_saved_profile(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            backup_destination,
            "OpenDALStorageBackend",
            lambda _spec: SimpleNamespace(source_namespace="gdrive/PrintStash"),
        )
        monkeypatch.setattr(
            backup_destination,
            "provider_ref_for_backend",
            lambda _backend, *, namespace: f"transport:{namespace}",
        )

        def connection(connection_id: int) -> StorageConnection:
            return StorageConnection(
                id=connection_id,
                name=f"Drive {connection_id}",
                kind=LibrarySourceKind.GDRIVE,
                purpose=StorageConnectionPurpose.BACKUP,
                config_json=json.dumps(
                    {"client_id": "client", "root": "PrintStash"}
                ),
                secret_json=json.dumps(
                    {"client_secret": "secret", "refresh_token": "refresh"}
                ),
            )

        first = backup_destination.destination_from_connection(connection(1))
        second = backup_destination.destination_from_connection(connection(2))

        assert first.provider_ref != second.provider_ref
        assert len(first.provider_ref) == 64

    def test_accepts_a_shared_connection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            backup_destination,
            "OpenDALStorageBackend",
            lambda _spec: SimpleNamespace(source_namespace="gdrive/PrintStash"),
        )
        monkeypatch.setattr(
            backup_destination,
            "provider_ref_for_backend",
            lambda _backend, *, namespace: f"transport:{namespace}",
        )
        connection = StorageConnection(
            id=9,
            name="Shared Drive",
            kind=LibrarySourceKind.GDRIVE,
            purpose=StorageConnectionPurpose.BOTH,
            config_json=json.dumps({"client_id": "client", "root": "PrintStash"}),
            secret_json=json.dumps(
                {"client_secret": "secret", "refresh_token": "refresh"}
            ),
        )

        destination = backup_destination.destination_from_connection(connection)

        assert destination.connection_id == 9


class TestConfiguredDestinations:
    def test_one_invalid_profile_does_not_hide_other_destinations(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [
            StorageConnection(
                id=1,
                name="Broken",
                kind=LibrarySourceKind.GDRIVE,
                purpose=StorageConnectionPurpose.BACKUP,
            ),
            StorageConnection(
                id=2,
                name="Working",
                kind=LibrarySourceKind.GDRIVE,
                purpose=StorageConnectionPurpose.BACKUP,
            ),
        ]

        class Factory:
            @contextmanager
            def scoped_session(self):
                result = SimpleNamespace(all=lambda: rows)
                yield SimpleNamespace(exec=lambda _statement: result)

        working = object()

        def build(row: StorageConnection):
            if row.id == 1:
                raise BackupDestinationError("invalid")
            return working

        monkeypatch.setattr(backup_destination, "get_session_factory", Factory)
        monkeypatch.setattr(backup_destination, "destination_from_connection", build)

        assert backup_destination.configured_destinations() == [working]
