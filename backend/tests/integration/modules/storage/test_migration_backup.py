"""Migration prerequisites validate actual archives, not catalogue claims."""

from datetime import timedelta

import pytest

from app.core.time import utcnow
from app.modules.backups.backup.creation import create_backup
from app.modules.storage import migration_backup
from app.modules.storage.storage_backend.local import LocalStorageBackend
from app.modules.storage.storage_backend.runtime import bind_backend


def test_real_backup_yields_content_bound_verification(backup_env):
    backup = create_backup()
    proof = migration_backup.verify_migration_backup(backup.id, backup.source_ref)
    assert proof["archive_sha256"] == backup.archive_sha256
    assert proof["backup_id"] == backup.id
    assert proof["source_ref"] == backup.source_ref
    assert proof["verified_at"]


def test_missing_backup_refuses_migration(backup_env):
    with pytest.raises(ValueError, match="migration_recent_backup_required"):
        migration_backup.verify_migration_backup("missing", None)


def test_expired_backup_cannot_satisfy_preflight(backup_env, monkeypatch):
    backup = create_backup()
    future = utcnow() + timedelta(days=8)
    monkeypatch.setattr(migration_backup, "utcnow", lambda: future)
    with pytest.raises(ValueError, match="migration_recent_backup_required"):
        migration_backup.verify_migration_backup(backup.id, backup.source_ref)


def test_other_vault_namespace_cannot_reuse_backup(backup_env, tmp_path):
    backup = create_backup()
    bind_backend(
        LocalStorageBackend(
            data_dir=tmp_path / "other", thumb_dir=tmp_path / "other-thumbs"
        )
    )
    with pytest.raises(ValueError, match="migration_backup_vault_mismatch"):
        migration_backup.verify_migration_backup(backup.id, backup.source_ref)


def test_activation_requires_the_original_preflight_archive(backup_env):
    backup = create_backup()
    with pytest.raises(ValueError, match="migration_backup_identity_changed"):
        migration_backup.verify_migration_backup(
            backup.id, backup.source_ref, expected_digest="0" * 64
        )


def test_cleanup_requires_backup_created_after_activation(backup_env):
    backup = create_backup()
    with pytest.raises(ValueError, match="migration_post_activation_backup_required"):
        migration_backup.verify_migration_backup(
            backup.id, backup.source_ref, created_after=utcnow()
        )
