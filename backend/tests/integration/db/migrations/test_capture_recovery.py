"""Existing capture manifests and slots survive recovery indexes and their rollback."""

import hashlib
import json

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from app.db.url import normalize_database_url
from tests.containers import fresh_postgres_database
from tests.factories.migration_rows import (
    RELEASED_V0121_REVISION,
    create_released_v0121_postgres_schema,
    seed_schema_row,
)
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI


class TestCaptureRecoveryMigration:
    @pytest.mark.parametrize(
        "dialect", ["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)]
    )
    def test_existing_capture_survives_recovery_upgrade(self, tmp_path, dialect):
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("script_location", str(ALEMBIC_DIR))
        url = (
            f"sqlite:///{tmp_path / 'captures.sqlite'}"
            if dialect == "sqlite"
            else normalize_database_url(fresh_postgres_database("capture_recovery"))
        )
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url)
        if dialect == "postgresql":
            with engine.begin() as connection:
                create_released_v0121_postgres_schema(connection)
            command.stamp(config, RELEASED_V0121_REVISION)
        command.upgrade(config, "e2115db53e8c")
        manifest = json.dumps(
            {"source": {"provider": "makerworld", "source_item_id": "12345"}}
        )
        with engine.begin() as connection:
            seed_schema_row(
                connection,
                "users",
                id=1,
                username="capture-owner",
                hashed_password="test-only",
            )
            seed_schema_row(
                connection,
                "inbox_items",
                id=1,
                owner_user_id=1,
                source_kind="BROWSER",
                state="REVIEW",
                manifest_json=manifest,
            )
            seed_schema_row(
                connection,
                "capture_upload_slots",
                id="retained-slot",
                inbox_item_id=1,
                source_file_id="package",
                role="file",
                state="UPLOADED",
                filename="part.3mf",
                media_type="model/3mf",
                sha256="a" * 64,
                size_bytes=123,
            )
        command.upgrade(config, "748a97ada8ec")
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT manifest_json, source_identity_key, capture_request_key FROM inbox_items WHERE id=1"
                )
            ).one()
            assert row.manifest_json == manifest
            assert (
                row.source_identity_key
                == hashlib.sha256(b"makerworld:12345").hexdigest()
            )
            assert row.capture_request_key is None
            assert (
                connection.execute(
                    text(
                        "SELECT sha256 FROM capture_upload_slots WHERE id='retained-slot'"
                    )
                ).scalar_one()
                == "a" * 64
            )
        assert any(
            c["name"] == "uq_capture_slot_source_file"
            for c in inspect(engine).get_unique_constraints("capture_upload_slots")
        )
        command.downgrade(config, "e2115db53e8c")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT manifest_json FROM inbox_items WHERE id=1")
                ).scalar_one()
                == manifest
            )
            assert (
                connection.execute(
                    text(
                        "SELECT size_bytes FROM capture_upload_slots WHERE id='retained-slot'"
                    )
                ).scalar_one()
                == 123
            )
        command.upgrade(config, "748a97ada8ec")
        engine.dispose()
