"""Storage-ledger migrations preserve previously proven ownership."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command


def test_storage_intent_migration_backfills_existing_proofs(tmp_path: Path) -> None:
    database = tmp_path / "storage-intents.sqlite"
    url = f"sqlite:///{database}"
    config = Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))
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
