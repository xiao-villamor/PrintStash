"""Migration prerequisites validate actual archives, not catalogue claims."""

from datetime import timedelta

import pytest

from app.core.time import utcnow
from app.modules.backups.backup.creation import create_backup
from app.modules.storage import migration_backup
from app.modules.storage.storage_backend.local import LocalStorageBackend
from app.modules.storage.storage_backend.runtime import bind_backend


class TestVerifyMigrationBackup:
    def test_corrupt_archive_cannot_satisfy_catalogue_proof(
        self, backup_env, monkeypatch
    ):
        backup = create_backup()
        get_archive = migration_backup.get_backup_archive_path

        def corrupt_after_lookup(*args, **kwargs):
            archive = get_archive(*args, **kwargs)
            archive.write_bytes(b"corrupt archive")
            return archive

        monkeypatch.setattr(
            migration_backup, "get_backup_archive_path", corrupt_after_lookup
        )

        with pytest.raises(
            ValueError, match="migration_verified_compatible_backup_required"
        ):
            migration_backup.verify_migration_backup(backup.id, backup.source_ref)

    def test_archive_changed_during_verification_is_rejected(
        self, backup_env, monkeypatch
    ):
        backup = create_backup()
        archive = migration_backup.get_backup_archive_path(
            backup.id, source_ref=backup.source_ref
        )
        verify = migration_backup.verify_backup

        def changing_archive(*args, **kwargs):
            result = verify(*args, **kwargs)
            archive.write_bytes(archive.read_bytes() + b"changed after verification")
            return result

        monkeypatch.setattr(migration_backup, "verify_backup", changing_archive)

        with pytest.raises(ValueError, match="migration_backup_identity_changed"):
            migration_backup.verify_migration_backup(backup.id, backup.source_ref)

    def test_unavailable_active_namespace_refuses_backup_proof(
        self, backup_env, monkeypatch
    ):
        backup = create_backup()

        def unavailable():
            raise RuntimeError("storage_backend_not_bound")

        monkeypatch.setattr(migration_backup, "get_bound_backend", unavailable)

        with pytest.raises(ValueError, match="migration_backup_vault_mismatch"):
            migration_backup.verify_migration_backup(backup.id, backup.source_ref)

    def test_real_backup_yields_content_bound_verification(self, backup_env):
        backup = create_backup()
        proof = migration_backup.verify_migration_backup(backup.id, backup.source_ref)
        assert proof["archive_sha256"] == backup.archive_sha256
        assert proof["backup_id"] == backup.id
        assert proof["source_ref"] == backup.source_ref
        assert proof["verified_at"]

    def test_missing_backup_refuses_migration(self, backup_env):
        with pytest.raises(ValueError, match="migration_recent_backup_required"):
            migration_backup.verify_migration_backup("missing", None)

    def test_expired_backup_cannot_satisfy_preflight(self, backup_env, monkeypatch):
        backup = create_backup()
        future = utcnow() + timedelta(days=8)
        monkeypatch.setattr(migration_backup, "utcnow", lambda: future)
        with pytest.raises(ValueError, match="migration_recent_backup_required"):
            migration_backup.verify_migration_backup(backup.id, backup.source_ref)

    def test_other_vault_namespace_cannot_reuse_backup(self, backup_env, tmp_path):
        backup = create_backup()
        bind_backend(
            LocalStorageBackend(
                data_dir=tmp_path / "other", thumb_dir=tmp_path / "other-thumbs"
            )
        )
        with pytest.raises(ValueError, match="migration_backup_vault_mismatch"):
            migration_backup.verify_migration_backup(backup.id, backup.source_ref)

    def test_activation_requires_the_original_preflight_archive(self, backup_env):
        backup = create_backup()
        with pytest.raises(ValueError, match="migration_backup_identity_changed"):
            migration_backup.verify_migration_backup(
                backup.id, backup.source_ref, expected_digest="0" * 64
            )

    def test_cleanup_requires_backup_created_after_activation(self, backup_env):
        backup = create_backup()
        with pytest.raises(
            ValueError, match="migration_post_activation_backup_required"
        ):
            migration_backup.verify_migration_backup(
                backup.id, backup.source_ref, created_after=utcnow()
            )
