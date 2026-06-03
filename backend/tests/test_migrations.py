from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_alembic_upgrade_creates_expected_schema(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "vault.sqlite"
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    assert "alembic_version" in tables
    assert "models" in tables
    assert "files" in tables
    assert "print_jobs" in tables
    assert "refresh_tokens" in tables

    files_columns = {col["name"]: col for col in inspector.get_columns("files")}
    assert "revision_label" in files_columns
    assert "revision_status" in files_columns
    assert "revision_notes" in files_columns
    assert "is_recommended" in files_columns
    assert files_columns["is_recommended"]["nullable"] is False
    assert files_columns["is_recommended"]["default"] is not None
