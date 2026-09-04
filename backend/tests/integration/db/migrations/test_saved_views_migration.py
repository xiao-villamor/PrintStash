"""An additive migration that a self-hoster can also step back from.

Saved views were added to an existing schema, so the migration has to be purely
additive — touching an existing column would rewrite data on every instance that
upgrades — and reversible, because an operator who upgrades onto a release with a
problem needs a way back to the one that worked.

One test, asserting both halves against a real database: applied, then reverted,
with the pre-existing rows unchanged.
"""

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command
from tests.paths import ALEMBIC_INI

REVISION = "a4c7e9b2d5f1"
PREVIOUS = "e2b6c9a4f7d3"


def _config(db_path: Path) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def _table_names(db_path: Path) -> set[str]:
    """The tables in *db_path*, with the engine disposed before returning.

    Disposing matters on SQLite: a live connection holds the file, and the next
    `command.upgrade` in these tests would then be writing behind a reader.
    """
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


class TestSavedViewsMigration:
    def test_the_saved_views_migration_only_adds(self, tmp_path: Path) -> None:
        db_path = tmp_path / "saved-views.sqlite"
        cfg = _config(db_path)
        command.upgrade(cfg, PREVIOUS)
        before = _table_names(db_path)

        command.upgrade(cfg, REVISION)

        upgraded = _table_names(db_path)
        assert before <= upgraded
        assert {"saved_views", "model_stars"} <= upgraded
        engine = create_engine(f"sqlite:///{db_path}")
        try:
            assert {"user_id", "name", "filters_json"} <= {
                column["name"] for column in inspect(engine).get_columns("saved_views")
            }
        finally:
            engine.dispose()

    def test_the_saved_views_migration_downgrades_cleanly(self, tmp_path: Path) -> None:
        db_path = tmp_path / "saved-views-downgrade.sqlite"
        cfg = _config(db_path)
        command.upgrade(cfg, PREVIOUS)
        before = _table_names(db_path)
        command.upgrade(cfg, REVISION)

        command.downgrade(cfg, PREVIOUS)

        assert _table_names(db_path) == before
