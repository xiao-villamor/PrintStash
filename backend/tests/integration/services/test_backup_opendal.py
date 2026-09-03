"""OpenDAL backups remain recoverable across a lost or replaced catalog.

Replication records ownership in the current database, while disaster recovery must
also find and explicitly adopt an archive that predates that database. These tests
pin both sides to the same configured connection and exact remote object identity.
"""

import hashlib
import io
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import backup
from app.services.backup_destination import BackupTrigger, RemoteBackupDestination
from app.services.storage_backend import CreationReceipt, StorageObjectInfo
from tests.integration._backup_harness import BackupEnv


class _RemoteBackend:
    backend_name = "backup-opendal-gdrive"
    provider_id = "gdrive"
    transport = "gdrive"
    source_namespace = "gdrive/PrintStash"

    def __init__(self, key: str, payload: bytes) -> None:
        self.key = key
        self.payload = payload

    def list_prefix(self, _prefix: str) -> list[str]:
        return [self.key]

    def source_key(self, relative: str) -> str:
        return f"{self.source_namespace}/{relative.strip('/')}"

    def object_info(self, key: str) -> StorageObjectInfo | None:
        if key != self.key:
            return None
        return StorageObjectInfo(size=len(self.payload), etag="remote-etag")

    def stream_chunks(self, key: str):
        assert key == self.key
        yield self.payload[:100]
        yield self.payload[100:]

    @contextmanager
    def open_reader(self, key: str):
        assert key == self.key
        yield io.BytesIO(self.payload)


def _remote_destination(key: str, payload: bytes) -> RemoteBackupDestination:
    return RemoteBackupDestination(
        connection_id=7,
        name="Recovery Drive",
        provider="gdrive",
        backend=_RemoteBackend(key, payload),  # type: ignore[arg-type]
        provider_ref="remote-provider-ref",
    )


class TestOpenDalBackupReplication:
    def test_create_keeps_local_backup_and_replicates_to_each_destination(
        self,
        backup_env: BackupEnv,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        writes: list[tuple[str, bytes]] = []

        class Backend:
            backend_name = "backup-opendal-gdrive"
            provider_id = "gdrive"
            transport = "gdrive"

            def namespace_for(self, key: str) -> str:
                assert key.startswith("gdrive/PrintStash/")
                return "gdrive/PrintStash"

            def create_stream(self, source, key: str) -> CreationReceipt:
                payload = source.read()
                writes.append((key, payload))
                return CreationReceipt(
                    key=key,
                    size=len(payload),
                    token="created",
                    backend=self.backend_name,
                    namespace="gdrive/PrintStash",
                    etag="gdrive-etag",
                )

        destination = SimpleNamespace(
            name="Drive copies",
            provider="gdrive",
            provider_ref="drive-profile-ref",
            backend=Backend(),
            key=lambda archive_name: f"gdrive/PrintStash/{archive_name}",
        )
        triggers: list[BackupTrigger] = []

        def destinations(trigger: BackupTrigger) -> list[object]:
            triggers.append(trigger)
            return [destination]

        monkeypatch.setattr(backup, "configured_destinations", destinations)

        meta = backup.create_backup()

        assert triggers == [BackupTrigger.MANUAL]
        assert Path(meta.path).is_file()
        assert len(writes) == 1
        assert writes[0][0].endswith(f"-{meta.id}.tar.gz")
        assert writes[0][1] == Path(meta.path).read_bytes()

    def test_remote_failure_does_not_discard_the_local_backup(
        self,
        backup_env: BackupEnv,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        failing = SimpleNamespace(
            name="Offline Drive",
            provider="gdrive",
            provider_ref="offline-profile-ref",
            backend=SimpleNamespace(),
            key=lambda archive_name: archive_name,
        )
        monkeypatch.setattr(
            backup, "configured_destinations", lambda _trigger: [failing]
        )

        meta = backup.create_backup()

        assert Path(meta.path).is_file()


class TestDiscoverUnownedOpenDalBackups:
    def test_discovers_a_valid_archive_with_its_exact_connection_identity(
        self,
        backup_env: BackupEnv,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        meta = backup.create_backup()
        payload = Path(meta.path).read_bytes()
        key = f"gdrive/PrintStash/printstash-backups/{Path(meta.path).name}"
        destination = _remote_destination(key, payload)
        monkeypatch.setattr(backup, "configured_destinations", lambda: [destination])

        candidates = backup.discover_unowned_opendal_backups()

        assert len(candidates) == 1
        assert candidates[0]["connection_id"] == 7
        assert candidates[0]["key"] == key
        assert candidates[0]["archive_sha256"] == hashlib.sha256(payload).hexdigest()
        assert candidates[0]["source_ref"]

    def test_omits_a_malformed_remote_archive(
        self,
        backup_env: BackupEnv,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        key = "gdrive/PrintStash/printstash-backups/printstash-backup-bad.tar.gz"
        destination = _remote_destination(key, b"not a backup")
        monkeypatch.setattr(backup, "configured_destinations", lambda: [destination])

        candidates = backup.discover_unowned_opendal_backups()

        assert candidates == []

    def test_omits_an_archive_outside_the_direct_reserved_root(
        self,
        backup_env: BackupEnv,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        key = "gdrive/PrintStash/printstash-backups/nested/printstash-backup-old.tar.gz"
        destination = _remote_destination(key, b"unused")
        monkeypatch.setattr(backup, "configured_destinations", lambda: [destination])

        assert backup.discover_unowned_opendal_backups() == []


class TestAdoptOpenDalBackup:
    def test_rejects_an_unavailable_connection(
        self,
        backup_env: BackupEnv,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(backup, "configured_destinations", lambda: [])

        with pytest.raises(RuntimeError, match="backup_remote_connection_unavailable"):
            backup.adopt_opendal_backup(
                7,
                "gdrive/PrintStash/printstash-backups/printstash-backup-old.tar.gz",
                source_ref="unused",
                expected_archive_sha256="a" * 64,
            )

    def test_rejects_a_key_outside_the_reserved_backup_root(
        self,
        backup_env: BackupEnv,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        key = "gdrive/PrintStash/printstash-backups/../printstash-backup-escaped.tar.gz"
        destination = _remote_destination(key, b"unused")
        monkeypatch.setattr(backup, "configured_destinations", lambda: [destination])

        with pytest.raises(ValueError, match="backup_key_invalid"):
            backup.adopt_opendal_backup(
                7,
                key,
                source_ref="unused",
                expected_archive_sha256="a" * 64,
            )

    def test_adopts_the_selected_archive_as_a_restorable_source(
        self,
        backup_env: BackupEnv,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        meta = backup.create_backup()
        payload = Path(meta.path).read_bytes()
        key = f"gdrive/PrintStash/printstash-backups/{Path(meta.path).name}"
        destination = _remote_destination(key, payload)
        monkeypatch.setattr(backup, "configured_destinations", lambda: [destination])
        candidate = backup.discover_unowned_opendal_backups()[0]

        adopted = backup.adopt_opendal_backup(
            7,
            key,
            source_ref=str(candidate["source_ref"]),
            expected_archive_sha256=str(candidate["archive_sha256"]),
        )

        sources = backup.list_backup_sources()
        assert adopted.source_ref in {source.source_ref for source in sources}

    def test_omits_an_archive_after_it_is_adopted(
        self,
        backup_env: BackupEnv,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        meta = backup.create_backup()
        payload = Path(meta.path).read_bytes()
        key = f"gdrive/PrintStash/printstash-backups/{Path(meta.path).name}"
        destination = _remote_destination(key, payload)
        monkeypatch.setattr(backup, "configured_destinations", lambda: [destination])
        candidate = backup.discover_unowned_opendal_backups()[0]
        backup.adopt_opendal_backup(
            7,
            key,
            source_ref=str(candidate["source_ref"]),
            expected_archive_sha256=str(candidate["archive_sha256"]),
        )

        candidates = backup.discover_unowned_opendal_backups()

        assert candidates == []

    def test_refuses_to_adopt_the_same_archive_twice(
        self,
        backup_env: BackupEnv,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        meta = backup.create_backup()
        payload = Path(meta.path).read_bytes()
        key = f"gdrive/PrintStash/printstash-backups/{Path(meta.path).name}"
        destination = _remote_destination(key, payload)
        monkeypatch.setattr(backup, "configured_destinations", lambda: [destination])
        candidate = backup.discover_unowned_opendal_backups()[0]
        arguments = {
            "source_ref": str(candidate["source_ref"]),
            "expected_archive_sha256": str(candidate["archive_sha256"]),
        }
        backup.adopt_opendal_backup(7, key, **arguments)

        with pytest.raises(ValueError, match="backup_already_adopted"):
            backup.adopt_opendal_backup(7, key, **arguments)

    def test_rejects_a_remote_object_that_changes_during_download(
        self,
        backup_env: BackupEnv,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        meta = backup.create_backup()
        payload = Path(meta.path).read_bytes()
        key = f"gdrive/PrintStash/printstash-backups/{Path(meta.path).name}"
        destination = _remote_destination(key, payload)
        calls = 0

        def changing_info(candidate_key: str) -> StorageObjectInfo:
            nonlocal calls
            assert candidate_key == key
            calls += 1
            return StorageObjectInfo(size=len(payload), etag=f"etag-{calls}")

        monkeypatch.setattr(destination.backend, "object_info", changing_info)
        monkeypatch.setattr(backup, "configured_destinations", lambda: [destination])
        source_ref = backup._source_ref(
            location=destination.location,
            namespace=destination.namespace,
            path=key,
            provider_ref=destination.provider_ref,
        )

        with pytest.raises(RuntimeError, match="backup_remote_changed"):
            backup.adopt_opendal_backup(
                7,
                key,
                source_ref=source_ref,
                expected_archive_sha256=hashlib.sha256(payload).hexdigest(),
            )

        assert not list(backup_env.backup_dir.glob(".printstash-opendal-adopt-*"))

    def test_rejects_a_changed_source_reference(
        self,
        backup_env: BackupEnv,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        meta = backup.create_backup()
        payload = Path(meta.path).read_bytes()
        key = f"gdrive/PrintStash/printstash-backups/{Path(meta.path).name}"
        destination = _remote_destination(key, payload)
        monkeypatch.setattr(backup, "configured_destinations", lambda: [destination])

        with pytest.raises(ValueError, match="backup_source_ref_mismatch"):
            backup.adopt_opendal_backup(
                7,
                key,
                source_ref="wrong-source",
                expected_archive_sha256=hashlib.sha256(payload).hexdigest(),
            )

    def test_rejects_a_changed_archive_digest(
        self,
        backup_env: BackupEnv,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        meta = backup.create_backup()
        payload = Path(meta.path).read_bytes()
        key = f"gdrive/PrintStash/printstash-backups/{Path(meta.path).name}"
        destination = _remote_destination(key, payload)
        monkeypatch.setattr(backup, "configured_destinations", lambda: [destination])
        candidate = backup.discover_unowned_opendal_backups()[0]

        with pytest.raises(RuntimeError, match="backup_archive_digest_mismatch"):
            backup.adopt_opendal_backup(
                7,
                key,
                source_ref=str(candidate["source_ref"]),
                expected_archive_sha256="f" * 64,
            )
