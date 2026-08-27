"""Defends ``test_saved_views_migration_is_additive_and_reversible`` behavior for the ``migrate`` production unit.

A failure means this boundary no longer preserves its observable contract.
"""

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command
from tests.paths import BACKEND_ROOT

REVISION = "a4c7e9b2d5f1"
PREVIOUS = "e2b6c9a4f7d3"


def _config(db_path: Path) -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def test_saved_views_migration_is_additive_and_reversible(tmp_path: Path) -> None:
    db_path = tmp_path / "saved-views.sqlite"
    cfg = _config(db_path)
    command.upgrade(cfg, PREVIOUS)
    engine = create_engine(f"sqlite:///{db_path}")
    before = set(inspect(engine).get_table_names())
    engine.dispose()

    command.upgrade(cfg, REVISION)
    engine = create_engine(f"sqlite:///{db_path}")
    upgraded = set(inspect(engine).get_table_names())
    assert before <= upgraded
    assert {"saved_views", "model_stars"} <= upgraded
    assert {"user_id", "name", "filters_json"} <= {
        column["name"] for column in inspect(engine).get_columns("saved_views")
    }
    engine.dispose()

    command.downgrade(cfg, PREVIOUS)
    engine = create_engine(f"sqlite:///{db_path}")
    downgraded = set(inspect(engine).get_table_names())
    engine.dispose()
    assert downgraded == before
