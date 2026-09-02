"""The standalone multipart migration backfills without touching legacy rows."""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
from typing import Iterator

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from alembic import command
from app.db.migrate import run_migrations
from app.db.url import normalize_database_url
from tests.containers import postgres_url
from tests.factories.migration_rows import (
    RELEASED_V0121_REVISION,
    create_released_v0121_postgres_schema,
    seed_schema_row,
)
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI

PARENT = "54b21089d3af"
REVISION = "30c1c4a321a5"


def _config(database: Path | str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    database_url = (
        database
        if isinstance(database, str) and "://" in database
        else f"sqlite:///{database}"
    )
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _reset_schema(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP SCHEMA public CASCADE")
        connection.exec_driver_sql("CREATE SCHEMA public")


@pytest.fixture(scope="module")
def postgres_migration_engine() -> Iterator[Engine]:
    """A real PostgreSQL database at the legacy parent revision."""
    url = postgres_url()
    engine = create_engine(normalize_database_url(url), pool_pre_ping=True)
    _reset_schema(engine)
    config = _config(
        engine.url.render_as_string(hide_password=False).replace("%", "%%")
    )
    with engine.begin() as connection:
        create_released_v0121_postgres_schema(connection)
    command.stamp(config, RELEASED_V0121_REVISION)
    command.upgrade(config, PARENT)
    try:
        yield engine
    finally:
        _reset_schema(engine)
        run_migrations(url)
        engine.dispose()


class TestMultipartModelsMigration:
    def test_backfill_preserves_legacy_part_options(self, tmp_path: Path) -> None:
        database = tmp_path / "multipart-models.sqlite"
        config = _config(database)
        command.upgrade(config, PARENT)
        engine = create_engine(f"sqlite:///{database}")
        with engine.begin() as connection:
            for model_id, name, model_hash in (
                (1, "Assembly", "a" * 64),
                (2, "Handle short", "b" * 64),
                (3, "Handle long", "c" * 64),
                (4, "File-only assembly", "d" * 64),
            ):
                connection.execute(
                    text(
                        """
                        INSERT INTO models (
                            id, name, slug, hash, next_file_version,
                            created_at, updated_at
                        ) VALUES (
                            :id, :name, :slug, :hash, 2,
                            '2026-09-01 00:00:00', '2026-09-01 00:00:00'
                        )
                        """
                    ),
                    {
                        "id": model_id,
                        "name": name,
                        "slug": name.lower().replace(" ", "-"),
                        "hash": model_hash,
                    },
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO files (
                            id, model_id, path, original_filename, file_type, version,
                            size_bytes, sha256, is_recommended, is_external, uploaded_at
                        ) VALUES (
                            :id, :model_id, :path, :filename, 'STL', 1,
                            10, :sha, 0, 0, '2026-09-01 00:00:00'
                        )
                        """
                    ),
                    {
                        "id": model_id,
                        "model_id": model_id,
                        "path": f"model-{model_id}.stl",
                        "filename": f"model-{model_id}.stl",
                        "sha": str(model_id) * 64,
                    },
                )
            # Two distinct legacy files belonging to one Model must remain
            # separate choices after the migration.  This is the data shape
            # produced by the original (547) file-target contract.
            for version, (file_id, filename) in enumerate(
                ((10, "handle-short.stl"), (11, "handle-long.stl")), start=2
            ):
                connection.execute(
                    text(
                        """
                        INSERT INTO files (
                            id, model_id, path, original_filename, file_type, version,
                            size_bytes, sha256, is_recommended, is_external, uploaded_at
                        ) VALUES (
                            :id, 2, :path, :filename, 'STL', :version,
                            10, :sha, 0, 0, '2026-09-01 00:00:00'
                        )
                        """
                    ),
                    {
                        "id": file_id,
                        "path": f"{filename}-path",
                        "filename": filename,
                        "sha": str(file_id) * 64,
                        "version": version,
                    },
                )
            for version, (file_id, filename) in enumerate(
                ((12, "file-only-a.stl"), (13, "file-only-b.stl")), start=4
            ):
                connection.execute(
                    text(
                        """
                        INSERT INTO files (
                            id, model_id, path, original_filename, file_type, version,
                            size_bytes, sha256, is_recommended, is_external, uploaded_at
                        ) VALUES (
                            :id, 2, :path, :filename, 'STL', :version,
                            10, :sha, 0, 0, '2026-09-01 00:00:00'
                        )
                        """
                    ),
                    {
                        "id": file_id,
                        "path": f"{filename}-path",
                        "filename": filename,
                        "sha": str(file_id) * 64,
                        "version": version,
                    },
                )
            connection.execute(
                text(
                    """
                    INSERT INTO part_groups
                      (id, model_id, name, name_key, sort_order, created_at)
                    VALUES (1, 1, 'Primary', 'primary', 0, '2026-09-01 00:00:00')
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO part_groups
                      (id, model_id, name, name_key, sort_order, created_at)
                    VALUES (2, 1, 'Cap', 'cap', 1, '2026-09-01 00:00:00')
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO part_groups
                      (id, model_id, name, name_key, sort_order, created_at)
                    VALUES (3, 4, 'File-only', 'file-only', 0, '2026-09-01 00:00:00')
                    """
                )
            )
            # The first option targets the parent Model itself.  It must be
            # represented once as the original choice, without a synthetic
            # parent part added by the migration.
            # The default is deliberately second: the new choice order must
            # still put it first after migration.
            for option_id, model_id, is_default in ((1, 1, 0), (2, 3, 1)):
                connection.execute(
                    text(
                        """
                        INSERT INTO part_options
                          (id, part_group_id, file_id, model_id, name, name_key,
                           sort_order, is_default, created_at)
                        VALUES (:id, 1, NULL, :model_id, :name, :name_key,
                                :sort_order, :is_default, '2026-09-01 00:00:00')
                        """
                    ),
                    {
                        "id": option_id,
                        "model_id": model_id,
                        "name": f"Option {model_id}",
                        "name_key": f"option-{model_id}",
                        "sort_order": option_id - 1,
                        "is_default": is_default,
                    },
                )
            for option_id, file_id, name, is_default in (
                (3, 10, "Short file", 0),
                (4, 11, "Long file", 1),
            ):
                connection.execute(
                    text(
                        """
                        INSERT INTO part_options
                          (id, part_group_id, file_id, model_id, name, name_key,
                           sort_order, is_default, created_at)
                        VALUES (:id, 2, :file_id, NULL, :name, :name_key,
                                :sort_order, :is_default, '2026-09-01 00:00:00')
                        """
                    ),
                    {
                        "id": option_id,
                        "file_id": file_id,
                        "name": name,
                        "name_key": name.lower().replace(" ", "-"),
                        "sort_order": option_id - 3,
                        "is_default": is_default,
                    },
                )
            for option_id, file_id, name, is_default in (
                (5, 12, "File only A", 1),
                (6, 13, "File only B", 0),
            ):
                connection.execute(
                    text(
                        """
                        INSERT INTO part_options
                          (id, part_group_id, file_id, model_id, name, name_key,
                           sort_order, is_default, created_at)
                        VALUES (:id, 3, :file_id, NULL, :name, :name_key,
                                :sort_order, :is_default, '2026-09-01 00:00:00')
                        """
                    ),
                    {
                        "id": option_id,
                        "file_id": file_id,
                        "name": name,
                        "name_key": name.lower().replace(" ", "-"),
                        "sort_order": option_id - 5,
                        "is_default": is_default,
                    },
                )

        command.upgrade(config, REVISION)
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT COUNT(*) FROM part_groups")
                ).scalar_one()
                == 3
            )
            assert (
                connection.execute(
                    text("SELECT COUNT(*) FROM part_options")
                ).scalar_one()
                == 6
            )
            assert (
                connection.execute(
                    text("SELECT COUNT(*) FROM multipart_models")
                ).scalar_one()
                == 2
            )
            assert connection.execute(
                text(
                    "SELECT id, part_group_id, file_id, model_id, name "
                    "FROM part_options ORDER BY id"
                )
            ).all() == [
                (1, 1, None, 1, "Option 1"),
                (2, 1, None, 3, "Option 3"),
                (3, 2, 10, None, "Short file"),
                (4, 2, 11, None, "Long file"),
                (5, 3, 12, None, "File only A"),
                (6, 3, 13, None, "File only B"),
            ]
            aggregate = connection.execute(
                text("SELECT id FROM multipart_models WHERE name = 'Assembly'")
            ).scalar_one()
            part_names = (
                connection.execute(
                    text(
                        "SELECT name FROM multipart_parts WHERE multipart_model_id = :id ORDER BY sort_order"
                    ),
                    {"id": aggregate},
                )
                .scalars()
                .all()
            )
            assert part_names == ["Primary", "Cap"]
            choices = connection.execute(
                text(
                    "SELECT model_id, source_file_id, label "
                    "FROM multipart_model_choices "
                    "WHERE multipart_model_id = :id "
                    "ORDER BY multipart_part_id, sort_order"
                ),
                {"id": aggregate},
            ).all()
            assert choices == [
                (3, None, "Option 3"),
                (1, None, "Option 1"),
                (2, 11, "Long file"),
                (2, 10, "Short file"),
            ]
            file_only_aggregate = connection.execute(
                text(
                    "SELECT id FROM multipart_models "
                    "WHERE slug = 'legacy-parts-' || :hash"
                ),
                {"hash": "d" * 64},
            ).scalar_one()
            assert connection.execute(
                text(
                    "SELECT name FROM multipart_parts "
                    "WHERE multipart_model_id = :id ORDER BY sort_order"
                ),
                {"id": file_only_aggregate},
            ).scalars().all() == ["File-only"]
            assert connection.execute(
                text(
                    "SELECT model_id, source_file_id, label "
                    "FROM multipart_model_choices "
                    "WHERE multipart_model_id = :id ORDER BY sort_order"
                ),
                {"id": file_only_aggregate},
            ).all() == [
                (2, 12, "File only A"),
                (2, 13, "File only B"),
            ]

        command.downgrade(config, PARENT)
        assert "multipart_models" not in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT COUNT(*) FROM part_groups")
                ).scalar_one()
                == 3
            )
            assert (
                connection.execute(
                    text("SELECT COUNT(*) FROM part_options")
                ).scalar_one()
                == 6
            )
        engine.dispose()

    def test_backfill_is_present_in_offline_postgres_sql(self) -> None:
        config = _config("postgresql+psycopg://user:pass@localhost/printstash")

        with contextlib.redirect_stdout(io.StringIO()) as output:
            command.upgrade(config, f"{PARENT}:{REVISION}", sql=True)

        rendered = output.getvalue()
        assert "INSERT INTO multipart_models" in rendered
        assert "INSERT INTO multipart_parts" in rendered
        assert "INSERT INTO multipart_model_choices" in rendered
        assert "legacy-parts-" in rendered

    @pytest.mark.postgres
    def test_backfill_on_real_postgres_preserves_legacy_rows(
        self, postgres_migration_engine: Engine
    ) -> None:
        """The upgrade works on PostgreSQL's real legacy schema and data."""
        engine = postgres_migration_engine
        with engine.begin() as connection:
            seed_schema_row(
                connection,
                "models",
                id=1,
                name="Assembly",
                slug="assembly",
                hash="a" * 64,
            )
            seed_schema_row(
                connection,
                "models",
                id=2,
                name="Alternate body",
                slug="alternate-body",
                hash="b" * 64,
            )
            seed_schema_row(
                connection,
                "files",
                id=1,
                model_id=1,
                path="assembly.stl",
                original_filename="assembly.stl",
                file_type="STL",
                version=1,
                sha256="1" * 64,
            )
            seed_schema_row(
                connection,
                "part_groups",
                id=1,
                model_id=1,
                name="Body",
                name_key="body",
                sort_order=0,
            )
            seed_schema_row(
                connection,
                "part_groups",
                id=2,
                model_id=1,
                name="Base",
                name_key="base",
                sort_order=1,
            )
            seed_schema_row(
                connection,
                "part_options",
                id=1,
                part_group_id=1,
                model_id=1,
                file_id=None,
                name="Original body",
                name_key="original-body",
                sort_order=0,
                is_default=False,
            )
            seed_schema_row(
                connection,
                "part_options",
                id=2,
                part_group_id=1,
                model_id=2,
                file_id=None,
                name="Alternate body",
                name_key="alternate-body",
                sort_order=1,
                is_default=True,
            )
            seed_schema_row(
                connection,
                "part_options",
                id=3,
                part_group_id=2,
                model_id=None,
                file_id=1,
                name="Pinned assembly file",
                name_key="pinned-assembly-file",
                sort_order=0,
                is_default=True,
            )

            legacy_groups = connection.execute(
                text(
                    "SELECT id, model_id, name, name_key, sort_order "
                    "FROM part_groups ORDER BY id"
                )
            ).all()
            legacy_options = connection.execute(
                text(
                    "SELECT id, part_group_id, file_id, model_id, name, "
                    "name_key, sort_order, is_default "
                    "FROM part_options ORDER BY id"
                )
            ).all()

        config = _config(
            engine.url.render_as_string(hide_password=False).replace("%", "%%")
        )
        command.upgrade(config, REVISION)

        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT COUNT(*) FROM multipart_models")
                ).scalar_one()
                == 1
            )
            aggregate_id = connection.execute(
                text("SELECT id FROM multipart_models WHERE name = 'Assembly'")
            ).scalar_one()
            assert connection.execute(
                text(
                    "SELECT name FROM multipart_parts "
                    "WHERE multipart_model_id = :aggregate_id ORDER BY sort_order"
                ),
                {"aggregate_id": aggregate_id},
            ).scalars().all() == ["Body", "Base"]
            assert connection.execute(
                text(
                    "SELECT p.name, c.model_id, c.source_file_id, c.label "
                    "FROM multipart_parts p "
                    "JOIN multipart_model_choices c ON c.multipart_part_id = p.id "
                    "WHERE p.multipart_model_id = :aggregate_id "
                    "ORDER BY p.sort_order, c.sort_order"
                ),
                {"aggregate_id": aggregate_id},
            ).all() == [
                ("Body", 2, None, "Alternate body"),
                ("Body", 1, None, "Original body"),
                ("Base", 1, 1, "Pinned assembly file"),
            ]
            assert (
                connection.execute(
                    text(
                        "SELECT id, model_id, name, name_key, sort_order FROM part_groups ORDER BY id"
                    )
                ).all()
                == legacy_groups
            )
            assert (
                connection.execute(
                    text(
                        "SELECT id, part_group_id, file_id, model_id, name, "
                        "name_key, sort_order, is_default FROM part_options ORDER BY id"
                    )
                ).all()
                == legacy_options
            )
