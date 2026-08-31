"""Storage-ledger migrations preserve previously proven ownership."""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlmodel import SQLModel

import app.db.models  # noqa: F401 - register every table on SQLModel.metadata
from alembic import command
from app.db import migrate as migrate_mod
from tests.factories.migration_rows import seed_legacy_backup_s3_receipt
from tests.paths import ALEMBIC_INI


class TestStorageObjectIntentMigration:
    def test_offline_downgrade_renders_quarantine_restore(self, tmp_path: Path) -> None:
        cfg = migrate_mod._alembic_config(f"sqlite:///{tmp_path / 'offline.sqlite'}")
        rendered = StringIO()
        with redirect_stdout(rendered):
            command.downgrade(cfg, "c3ec006ced6a:8c44c3bfef74", sql=True)

        assert (
            "UPDATE owned_storage_objects SET state = 'committed'"
            in rendered.getvalue()
        )

    def test_downgrade_refuses_provider_distinct_delete_intent_collision(
        self, tmp_path: Path
    ) -> None:
        url = f"sqlite:///{tmp_path / 'delete-intent-collision.sqlite'}"
        config = migrate_mod._alembic_config(url)
        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                for provider_ref in ("a" * 64, "b" * 64):
                    connection.execute(
                        text(
                            "INSERT INTO storage_delete_intents "
                            "(backend, namespace, key, provider_ref, object_kind, "
                            "token, size_bytes, authorization_mode, authorized_at, "
                            "quarantine_state, status, attempts, created_at, updated_at) "
                            "VALUES ('s3', 'bucket/root', 'shared.stl', :provider_ref, "
                            "'artifact', 'shared-token', 12, 'verified', "
                            "'2026-01-01 00:00:00', 'none', 'pending', 0, "
                            "'2026-01-01 00:00:00', '2026-01-01 00:00:00')"
                        ),
                        {"provider_ref": provider_ref},
                    )
        finally:
            engine.dispose()

        with pytest.raises(
            RuntimeError, match="cannot downgrade storage deletion intents"
        ):
            command.downgrade(config, "8c44c3bfef74")

    def test_quarantines_incomplete_legacy_backup_s3_receipts(
        self, tmp_path: Path
    ) -> None:
        database = tmp_path / "legacy-backup-receipt.sqlite"
        url = f"sqlite:///{database}"
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "8c44c3bfef74")
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                seed_legacy_backup_s3_receipt(
                    connection,
                    key="nexus3d-backups/nexus3d-backup-20260101-a.tar.gz",
                    token="legacy-token-a",
                    size_bytes=12,
                )
                seed_legacy_backup_s3_receipt(
                    connection,
                    key="nexus3d-backups/nexus3d-backup-20260101-b.tar.gz",
                    token="legacy-token-b",
                    size_bytes=13,
                    sha256="b" * 64,
                )
        finally:
            engine.dispose()

        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            with engine.connect() as connection:
                row = connection.execute(
                    text(
                        "SELECT key, object_kind, token, size_bytes, sha256, state, "
                        "committed_at, last_error, provider_ref "
                        "FROM owned_storage_objects ORDER BY key"
                    )
                ).all()
            assert row == [
                (
                    "nexus3d-backups/nexus3d-backup-20260101-a.tar.gz",
                    "backup",
                    "legacy-token-a",
                    12,
                    None,
                    "blocked",
                    None,
                    "backup_s3_adoption_required",
                    None,
                ),
                (
                    "nexus3d-backups/nexus3d-backup-20260101-b.tar.gz",
                    "backup",
                    "legacy-token-b",
                    13,
                    "b" * 64,
                    "blocked",
                    None,
                    "backup_s3_adoption_required",
                    None,
                ),
            ]
        finally:
            engine.dispose()

    def test_provider_aware_locator_uniqueness_matches_fresh_upgrade(
        self, tmp_path: Path
    ) -> None:
        fresh_url = f"sqlite:///{tmp_path / 'fresh.sqlite'}"
        fresh_engine = create_engine(fresh_url)
        try:
            SQLModel.metadata.create_all(fresh_engine)
            fresh_unique = {
                tuple(item["column_names"])
                for item in inspect(fresh_engine).get_unique_constraints(
                    "owned_storage_objects"
                )
            }
            fresh_intent_unique = {
                tuple(item["column_names"])
                for item in inspect(fresh_engine).get_unique_constraints(
                    "storage_delete_intents"
                )
            }
        finally:
            fresh_engine.dispose()

        upgraded_url = f"sqlite:///{tmp_path / 'upgraded.sqlite'}"
        command.upgrade(migrate_mod._alembic_config(upgraded_url), "head")  # noqa: SLF001
        upgraded_engine = create_engine(upgraded_url)
        try:
            upgraded_unique = {
                tuple(item["column_names"])
                for item in inspect(upgraded_engine).get_unique_constraints(
                    "owned_storage_objects"
                )
            }
            upgraded_intent_unique = {
                tuple(item["column_names"])
                for item in inspect(upgraded_engine).get_unique_constraints(
                    "storage_delete_intents"
                )
            }
        finally:
            upgraded_engine.dispose()

        expected = {("backend", "provider_ref", "namespace", "key")}
        assert fresh_unique == expected
        assert upgraded_unique == fresh_unique
        expected_intents = {("backend", "provider_ref", "namespace", "key", "token")}
        assert fresh_intent_unique == expected_intents
        assert upgraded_intent_unique == fresh_intent_unique

        for engine in (fresh_engine, upgraded_engine):
            # A nullable provider identity must not make two historical rows
            # with the same locator look distinct.  The provider-aware unique
            # constraint handles modern rows; this partial index handles the
            # legacy NULL case on both supported SQL dialects.
            indexes = {
                item["name"]: item
                for item in inspect(engine).get_indexes("owned_storage_objects")
            }
            assert indexes["uq_owned_storage_legacy_locator"]["unique"] == 1
            intent_indexes = {
                item["name"]: item
                for item in inspect(engine).get_indexes("storage_delete_intents")
            }
            assert (
                intent_indexes["uq_storage_delete_intent_legacy_receipt"]["unique"] == 1
            )

    def test_backfills_existing_proofs(self, tmp_path: Path) -> None:
        database = tmp_path / "storage-intents.sqlite"
        url = f"sqlite:///{database}"
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "a7c9e1b5d3f2")
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO owned_storage_objects "
                        "(backend, namespace, key, object_kind, token, size_bytes, "
                        "created_at) VALUES "
                        "('local', 'data:/vault', '/vault/part.stl', 'artifact', "
                        "'proof-token', 12, '2026-01-01 00:00:00')"
                    )
                )
        finally:
            engine.dispose()

        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            with engine.connect() as connection:
                row = connection.execute(
                    text(
                        "SELECT state, token, size_bytes, committed_at "
                        "FROM owned_storage_objects"
                    )
                ).one()
            assert row == ("committed", "proof-token", 12, "2026-01-01 00:00:00")
        finally:
            engine.dispose()
