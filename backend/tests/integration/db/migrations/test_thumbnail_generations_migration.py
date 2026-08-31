"""Defend reversible installation of durable thumbnail coordination tables.

Self-hosters must be able to cross this schema boundary on SQLite without
losing the existing library or leaving coordination tables behind on rollback.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command
from tests.paths import ALEMBIC_INI

_REVISION = "f3a4f173d948"
_PARENT = "f0bde833e466"


def _config(path: Path) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    return config


class TestThumbnailGenerationMigration:
    @staticmethod
    def test_thumbnail_generation_schema_round_trips(tmp_path: Path) -> None:
        database = tmp_path / "thumbnail-generations.sqlite"
        config = _config(database)

        command.upgrade(config, _REVISION)

        engine = create_engine(f"sqlite:///{database}")
        try:
            inspector = inspect(engine)
            assert {
                "thumbnail_generations",
                "thumbnail_render_slots",
            } <= set(inspector.get_table_names())
            assert any(
                constraint["column_names"]
                == ["file_id", "source_sha256", "recipe_fingerprint"]
                for constraint in inspector.get_unique_constraints(
                    "thumbnail_generations"
                )
            )
            assert inspector.get_foreign_keys("thumbnail_generations")[0][
                "options"
            ] == {"ondelete": "CASCADE"}
        finally:
            engine.dispose()

        command.downgrade(config, _PARENT)

        engine = create_engine(f"sqlite:///{database}")
        try:
            tables = set(inspect(engine).get_table_names())
            assert "thumbnail_generations" not in tables
            assert "thumbnail_render_slots" not in tables
        finally:
            engine.dispose()
