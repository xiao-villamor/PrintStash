"""Deletion-intent hash evidence is additive and leaves legacy outbox rows intact.

Released rows predate ``sha256`` and therefore carry no hash evidence. They still
represent durable work, so both the forward migration and a rollback round trip must
preserve the row rather than rebuilding the outbox destructively.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from app.db import migrate as migrate_mod
from tests.factories.migration_rows import seed_schema_row

PREVIOUS = "5c777075c95b"
REVISION = "303a72ca9ff4"


@pytest.fixture
def legacy_delete_intent(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'legacy-delete-intent.sqlite'}"
    command.upgrade(migrate_mod._alembic_config(url), PREVIOUS)  # noqa: SLF001
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            seed_schema_row(
                connection,
                "storage_delete_intents",
                id=1,
                backend="local",
                namespace="released-vault",
                key="trash/legacy.stl",
                object_kind="artifact",
                token="legacy-delete-token",
                size_bytes=50,
                status="pending",
                attempts=0,
            )
    finally:
        engine.dispose()
    return url


class TestUpgrade:
    def test_preserves_an_intent_without_hash_evidence(
        self, legacy_delete_intent: str
    ) -> None:
        command.upgrade(
            migrate_mod._alembic_config(legacy_delete_intent),  # noqa: SLF001
            REVISION,
        )

        engine = create_engine(legacy_delete_intent)
        try:
            with engine.connect() as connection:
                row = connection.execute(
                    text(
                        "SELECT key, token, size_bytes, sha256 "
                        "FROM storage_delete_intents WHERE id = 1"
                    )
                ).one()
        finally:
            engine.dispose()

        assert row == ("trash/legacy.stl", "legacy-delete-token", 50, None)


class TestRoundTrip:
    def test_preserves_the_legacy_row_across_rollback(
        self, legacy_delete_intent: str
    ) -> None:
        config = migrate_mod._alembic_config(legacy_delete_intent)  # noqa: SLF001
        command.upgrade(config, REVISION)
        command.downgrade(config, PREVIOUS)
        command.upgrade(config, REVISION)

        engine = create_engine(legacy_delete_intent)
        try:
            with engine.connect() as connection:
                row = connection.execute(
                    text(
                        "SELECT key, token, size_bytes, sha256 "
                        "FROM storage_delete_intents WHERE id = 1"
                    )
                ).one()
        finally:
            engine.dispose()

        assert row == ("trash/legacy.stl", "legacy-delete-token", 50, None)
