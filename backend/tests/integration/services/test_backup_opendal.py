"""OpenDAL backup replication integrated with the durable ownership ledger."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import backup
from app.services.storage_backend import CreationReceipt
from tests.integration._backup_harness import BackupEnv


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
        monkeypatch.setattr(backup, "configured_destinations", lambda: [destination])

        meta = backup.create_backup()

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
        monkeypatch.setattr(backup, "configured_destinations", lambda: [failing])

        meta = backup.create_backup()

        assert Path(meta.path).is_file()
