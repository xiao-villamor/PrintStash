"""The Part Option schema upgrades and downgrades on an existing install."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command
from tests.paths import ALEMBIC_DIR, ALEMBIC_INI

REVISION = "547387477ce4"
PARENT = "3e7ab53ac43d"


def _config(database: Path) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    return config


class TestPartOptionsMigration:
    def test_upgrade_creates_the_normalized_option_schema(self, tmp_path: Path) -> None:
        database = tmp_path / "part-options.sqlite"
        config = _config(database)
        command.upgrade(config, PARENT)
        command.upgrade(config, REVISION)

        engine = create_engine(f"sqlite:///{database}")
        inspector = inspect(engine)
        assert {"part_groups", "part_options"} <= set(inspector.get_table_names())
        option_indexes = {row["name"] for row in inspector.get_indexes("part_options")}
        assert "uq_part_options_one_default" in option_indexes

        command.downgrade(config, PARENT)

        assert not (
            {"part_groups", "part_options"}
            & set(inspect(engine).get_table_names())
        )
        engine.dispose()
