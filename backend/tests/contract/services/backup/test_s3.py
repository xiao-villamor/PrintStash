"""Exercise the independent backup destination against a real S3 contract."""

from __future__ import annotations

import botocore.exceptions
import pytest

from tests.integration.services.backup._backup_shared import (
    BackupEnv,
    Path,
    _read_model_names,
    _seed_model_with_blob,
    backup,
    requires_s3,
)


@requires_s3
def test_create_backup_uploads_to_s3(backup_s3_env: BackupEnv) -> None:
    _seed_model_with_blob(backup_s3_env, name="Widget", content=b"solid widget\n")

    meta = backup.create_backup()

    s3 = backup._get_backup_s3()
    key = backup._backup_s3_key(Path(meta.path).name)
    head = s3.head_object(Bucket=backup.settings.backup_s3_bucket, Key=key)
    assert head["ContentLength"] == meta.size_bytes


@requires_s3
def test_list_backups_finds_s3_only_backup(backup_s3_env: BackupEnv) -> None:
    _seed_model_with_blob(backup_s3_env, name="Widget", content=b"solid widget\n")
    meta = backup.create_backup()

    Path(meta.path).unlink()

    found = backup.get_backup(meta.id)
    assert found is not None
    assert found.location == "s3"
    assert found.file_count == meta.file_count


@requires_s3
def test_restore_downloads_s3_only_backup_before_restoring(
    backup_s3_env: BackupEnv,
) -> None:
    _model_id, key = _seed_model_with_blob(
        backup_s3_env, name="Widget", content=b"solid widget\n"
    )
    meta = backup.create_backup()
    Path(key).unlink()
    Path(meta.path).unlink()

    result = backup.restore_backup(meta.id)

    assert result["backup_id"] == meta.id
    assert _read_model_names(backup_s3_env) == ["Widget"]
    assert Path(key).read_bytes() == b"solid widget\n"
    assert Path(meta.path).exists()


@requires_s3
def test_delete_backup_removes_s3_copy(backup_s3_env: BackupEnv) -> None:
    _seed_model_with_blob(backup_s3_env, name="Widget", content=b"solid widget\n")
    meta = backup.create_backup()

    s3 = backup._get_backup_s3()
    key = backup._backup_s3_key(Path(meta.path).name)
    assert s3.head_object(Bucket=backup.settings.backup_s3_bucket, Key=key)

    assert backup.delete_backup(meta.id) is True

    with pytest.raises(botocore.exceptions.ClientError):
        s3.head_object(Bucket=backup.settings.backup_s3_bucket, Key=key)
