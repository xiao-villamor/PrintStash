"""Exercise the independent backup destination against a real S3 contract."""

from __future__ import annotations

from pathlib import Path

import botocore.exceptions
import pytest

import app.services.backup as backup
from tests.integration._backup_harness import (
    BackupEnv,
    read_model_names,
    seed_model_with_blob,
)

requires_s3 = pytest.mark.s3


class TestBackupS3:
    @requires_s3
    def test_create_backup_uploads_to_s3(self, backup_s3_env: BackupEnv) -> None:
        seed_model_with_blob(backup_s3_env, name="Widget", content=b"solid widget\n")

        meta = backup.create_backup()

        s3 = backup._get_backup_s3()
        key = backup._backup_s3_key(Path(meta.path).name)
        head = s3.head_object(Bucket=backup.settings.backup_s3_bucket, Key=key)
        assert head["ContentLength"] == meta.size_bytes

    @requires_s3
    def test_list_backups_finds_s3_only_backup(self, backup_s3_env: BackupEnv) -> None:
        seed_model_with_blob(backup_s3_env, name="Widget", content=b"solid widget\n")
        meta = backup.create_backup()

        Path(meta.path).unlink()

        found = backup.get_backup(meta.id)
        assert found is not None
        assert found.location == "s3"
        assert found.file_count == meta.file_count

    @requires_s3
    def test_remote_receipt_survives_a_client_restart(
        self, backup_s3_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed_model_with_blob(backup_s3_env, name="Restart receipt", content=b"bytes")
        meta = backup.create_backup()
        Path(meta.path).unlink()

        monkeypatch.setattr(backup, "_backup_s3", None)
        monkeypatch.setattr(backup, "_backup_s3_target", None)
        monkeypatch.setattr(backup, "_backup_s3_last_signature", None)

        restored = backup.get_backup(meta.id)

        assert restored is not None
        assert restored.location == "s3"
        assert restored.source_ref != meta.source_ref

    @requires_s3
    def test_restore_downloads_s3_only_backup_before_restoring(
        self, backup_s3_env: BackupEnv
    ) -> None:
        _model_id, key = seed_model_with_blob(
            backup_s3_env, name="Widget", content=b"solid widget\n"
        )
        meta = backup.create_backup()
        Path(key).unlink()
        Path(meta.path).unlink()

        result = backup.restore_backup(meta.id)

        assert result["backup_id"] == meta.id
        assert read_model_names(backup_s3_env) == ["Widget"]
        assert Path(key).read_bytes() == b"solid widget\n"
        assert not Path(meta.path).exists()
        assert not list((backup_s3_env.backup_dir / ".cloud-cache").glob("*.tar.gz"))

    @requires_s3
    def test_delete_backup_removes_s3_copy(self, backup_s3_env: BackupEnv) -> None:
        seed_model_with_blob(backup_s3_env, name="Widget", content=b"solid widget\n")
        meta = backup.create_backup()

        s3 = backup._get_backup_s3()
        key = backup._backup_s3_key(Path(meta.path).name)
        assert s3.head_object(Bucket=backup.settings.backup_s3_bucket, Key=key)

        Path(meta.path).unlink()
        remote = backup.get_backup(meta.id)
        assert remote is not None
        assert remote.location == "s3"
        cache = backup.get_backup_archive_path(meta.id, source_ref=remote.source_ref)
        assert cache.parent.name == ".cloud-cache"
        assert cache.exists()

        assert backup.delete_backup(meta.id, source_ref=remote.source_ref) is True
        assert not cache.exists()

        with pytest.raises(botocore.exceptions.ClientError):
            s3.head_object(Bucket=backup.settings.backup_s3_bucket, Key=key)
