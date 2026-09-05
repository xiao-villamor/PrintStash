"""Upgrades preserve backup locators without manufacturing GC trust."""

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI


class TestGcIdentityUpgrade:
    def test_preserves_historical_locators_without_backfilling_trust(
        self, tmp_path: Path
    ) -> None:
        url = f"sqlite:///{tmp_path / 'gc-evidence.sqlite'}"
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("script_location", str(ALEMBIC_DIR))
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "e916c791628c")
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text("""
                    INSERT INTO owned_storage_objects
                    (id, backend, namespace, key, provider_ref, object_kind, state,
                     token, size_bytes, sha256, etag, version_id, created_at)
                    VALUES (1, 'backup-s3', 'bucket/printstash-backups',
                            'printstash-backups/archive.tar.gz', :provider,
                            'backup', 'committed', 'original-token', 123, :digest,
                            'original-etag', 'original-version', CURRENT_TIMESTAMP)
                """),
                    {"provider": "a" * 64, "digest": "b" * 64},
                )
                connection.execute(
                    text("""
                    INSERT INTO gc_runs
                    (id, active_slot, state, digest, retention_days, cutoff_at,
                     resource_count, candidate_pool_count, key_count, size_bytes,
                     scheduled, backup_id, backup_source_ref, backup_provider_ref,
                     backup_archive_sha256, active_provider_ref, restore_generation,
                     created_at, updated_at)
                    VALUES (1, 1, 'quarantined', :digest, 30, CURRENT_TIMESTAMP,
                            1, 1, 1, 123, 0, 'archive', :source, :provider,
                            :digest, :active, '', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """),
                    {
                        "provider": "a" * 64,
                        "digest": "b" * 64,
                        "source": "c" * 64,
                        "active": "d" * 64,
                    },
                )

            command.upgrade(config, "0aece77e15cf")

            with engine.connect() as connection:
                locator = connection.execute(
                    text(
                        "SELECT provider_ref, namespace, key, token, etag, version_id FROM owned_storage_objects WHERE id = 1"
                    )
                ).one()
                evidence = connection.execute(
                    text(
                        "SELECT backup_id, backup_source_ref, backup_provider_ref, active_identity_evidence, backup_identity_evidence FROM gc_runs WHERE id = 1"
                    )
                ).one()
                declarations = connection.execute(
                    text("SELECT COUNT(*) FROM storage_failure_domain_declarations")
                ).scalar_one()
            assert locator == (
                "a" * 64,
                "bucket/printstash-backups",
                "printstash-backups/archive.tar.gz",
                "original-token",
                "original-etag",
                "original-version",
            )
            assert evidence == ("archive", "c" * 64, "a" * 64, None, None)
            assert declarations == 0
        finally:
            engine.dispose()
