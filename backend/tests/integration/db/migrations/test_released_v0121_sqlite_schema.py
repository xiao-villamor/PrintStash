"""The exact v0.12.1 SQLite create_all schema can upgrade to head."""

from __future__ import annotations

import hashlib
from pathlib import Path

from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from alembic import command
from app.db import migrate as migrate_mod
from tests.factories.migration_rows import (
    RELEASED_V0121_REVISION,
    create_released_v0121_sqlite_schema,
    seed_released_v0121_rows,
    seed_schema_row,
)


def test_exact_released_sqlite_create_all_schema_upgrades_without_data_loss(
    tmp_path: Path,
) -> None:
    """A fresh-install database from the released models is an upgrade input."""
    external_bytes = b"released external artifact\x00with stable bytes"
    external_path = tmp_path / "external" / "released-model.stl"
    external_path.parent.mkdir()
    external_path.write_bytes(external_bytes)
    external_sha256 = hashlib.sha256(external_bytes).hexdigest()
    url = f"sqlite:///{tmp_path / 'released-v0.12.1-create-all.sqlite'}"
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            create_released_v0121_sqlite_schema(connection)
            seed_released_v0121_rows(connection)
            seed_schema_row(
                connection,
                "external_libraries",
                id=1,
                name="Released NAS",
                root_path="/mnt/printstash",
            )
            connection.execute(
                text(
                    "UPDATE files SET path = :path, "
                    "original_filename = 'released-model.stl', size_bytes = :size_bytes, "
                    "sha256 = :sha256, is_external = 1, external_library_id = 1 "
                    "WHERE id = 1"
                ),
                {
                    "path": str(external_path),
                    "size_bytes": len(external_bytes),
                    "sha256": external_sha256,
                },
            )
            external_identity_before = connection.execute(
                text(
                    "SELECT path, size_bytes, sha256, is_external, external_library_id "
                    "FROM files WHERE id = 1"
                )
            ).one()
            before = {
                table: connection.execute(
                    text(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
                ).scalar_one()
                for table in (
                    "collections",
                    "files",
                    "metadata",
                    "models",
                    "owned_storage_objects",
                    "storage_delete_intents",
                    "tags",
                )
            }
    finally:
        engine.dispose()

    command.stamp(migrate_mod._alembic_config(url), RELEASED_V0121_REVISION)  # noqa: SLF001
    command.upgrade(migrate_mod._alembic_config(url), "head")  # noqa: SLF001

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            after = {
                table: connection.execute(
                    text(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
                ).scalar_one()
                for table in before
            }
            assert after == before
            assert (
                connection.execute(
                    text(
                        "SELECT path, size_bytes, sha256, is_external, external_library_id "
                        "FROM files WHERE id = 1"
                    )
                ).one()
                == external_identity_before
            )
            assert Path(external_identity_before[0]).read_bytes() == external_bytes
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == ScriptDirectory.from_config(
                    migrate_mod._alembic_config(url)  # noqa: SLF001
                ).get_current_head()
            )
            assert migrate_mod._orphan_schema_issues(engine) == []  # noqa: SLF001

            for table in (
                "collections",
                "files",
                "models",
                "print_jobs",
                "printers",
                "tags",
                "users",
            ):
                foreign_keys = inspect(connection).get_foreign_keys(table)
                signatures = {
                    (
                        tuple(key["constrained_columns"]),
                        key["referred_table"],
                        tuple(key["referred_columns"]),
                    )
                    for key in foreign_keys
                }
                assert len(signatures) == len(foreign_keys)

            metadata_indexes = {
                index["name"] for index in inspect(connection).get_indexes("metadata")
            }
            assert "ix_metadata_material_type" not in metadata_indexes
            assert "ix_metadata_printer_model" not in metadata_indexes
            assert "ix_metadata_slicer_name" not in metadata_indexes
    finally:
        engine.dispose()
