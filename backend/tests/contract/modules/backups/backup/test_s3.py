"""Exercise the independent backup destination against a real S3 contract."""

from __future__ import annotations

from pathlib import Path

import botocore.exceptions
import pytest

import app.modules.backups.backup.catalogue as backup_catalogue
import app.modules.backups.backup.creation as backup_creation
import app.modules.backups.backup.deletion as backup_deletion
import app.modules.backups.backup.restore as backup_restore
import app.modules.backups.backup.targets as backup_targets
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

        meta = backup_creation.create_backup()

        s3 = backup_targets._get_backup_s3()
        key = backup_targets._backup_s3_key(Path(meta.path).name)
        head = s3.head_object(Bucket=backup_targets.settings.backup_s3_bucket, Key=key)
        assert head["ContentLength"] == meta.size_bytes

    @requires_s3
    def test_list_backups_finds_s3_only_backup(self, backup_s3_env: BackupEnv) -> None:
        seed_model_with_blob(backup_s3_env, name="Widget", content=b"solid widget\n")
        meta = backup_creation.create_backup()

        Path(meta.path).unlink()

        found = backup_catalogue.get_backup(meta.id)
        assert found is not None
        assert found.location == "s3"
        assert found.file_count == meta.file_count

    @requires_s3
    def test_remote_receipt_survives_a_client_restart(
        self, backup_s3_env: BackupEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed_model_with_blob(backup_s3_env, name="Restart receipt", content=b"bytes")
        meta = backup_creation.create_backup()
        Path(meta.path).unlink()

        monkeypatch.setattr(backup_targets, "_backup_s3", None)
        monkeypatch.setattr(backup_targets, "_backup_s3_target", None)
        monkeypatch.setattr(backup_targets, "_backup_s3_last_signature", None)

        restored = backup_catalogue.get_backup(meta.id)

        assert restored is not None
        assert restored.location == "s3"
        assert restored.source_ref != meta.source_ref

    @requires_s3
    @pytest.mark.critical
    def test_restore_downloads_s3_only_backup_before_restoring(
        self, backup_s3_env: BackupEnv
    ) -> None:
        _model_id, key = seed_model_with_blob(
            backup_s3_env, name="Widget", content=b"solid widget\n"
        )
        meta = backup_creation.create_backup()
        Path(key).unlink()
        Path(meta.path).unlink()

        result = backup_restore.restore_backup(meta.id)

        assert result["backup_id"] == meta.id
        assert read_model_names(backup_s3_env) == ["Widget"]
        assert Path(key).read_bytes() == b"solid widget\n"
        assert not Path(meta.path).exists()
        assert not list((backup_s3_env.backup_dir / ".cloud-cache").glob("*.tar.gz"))

    @requires_s3
    def test_delete_backup_removes_s3_copy(self, backup_s3_env: BackupEnv) -> None:
        seed_model_with_blob(backup_s3_env, name="Widget", content=b"solid widget\n")
        s3 = backup_targets._get_backup_s3()
        s3.put_bucket_versioning(
            Bucket=backup_targets.settings.backup_s3_bucket,
            VersioningConfiguration={"Status": "Enabled"},
        )
        meta = backup_creation.create_backup()
        key = backup_targets._backup_s3_key(Path(meta.path).name)
        published = s3.head_object(
            Bucket=backup_targets.settings.backup_s3_bucket, Key=key
        )
        assert published.get("VersionId") not in (None, "", "null")

        Path(meta.path).unlink()
        remote = backup_catalogue.get_backup(meta.id)
        assert remote is not None
        assert remote.location == "s3"
        cache = backup_catalogue.get_backup_archive_path(
            meta.id, source_ref=remote.source_ref
        )
        assert cache.parent.name == ".cloud-cache"
        assert cache.exists()

        assert (
            backup_deletion.delete_backup(meta.id, source_ref=remote.source_ref) is True
        )
        assert not cache.exists()

        with pytest.raises(botocore.exceptions.ClientError):
            s3.head_object(Bucket=backup_targets.settings.backup_s3_bucket, Key=key)

    @requires_s3
    def test_refuses_deletion_without_a_physical_generation(
        self, backup_s3_env: BackupEnv
    ) -> None:
        import hashlib

        from app.modules.backups.backup.contracts import BackupDeleteUnsupportedError

        seed_model_with_blob(backup_s3_env, name="Widget", content=b"solid widget\n")
        meta = backup_creation.create_backup()
        s3 = backup_targets._get_backup_s3()
        bucket = backup_targets.settings.backup_s3_bucket
        key = backup_targets._backup_s3_key(Path(meta.path).name)
        Path(meta.path).unlink()
        remote = backup_catalogue.get_backup(meta.id)
        assert remote is not None
        assert remote.location == "s3"

        with pytest.raises(BackupDeleteUnsupportedError):
            backup_deletion.delete_backup(meta.id, source_ref=remote.source_ref)

        response = s3.get_object(Bucket=bucket, Key=key)
        assert response.get("VersionId") in (None, "null")
        body = response["Body"]
        try:
            assert hashlib.sha256(body.read()).hexdigest() == meta.archive_sha256
        finally:
            body.close()
