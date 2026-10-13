"""How the engine opens its own SQLite file.

DBOS leaves that file in rollback-journal mode with full synchronisation, so
every queue poll and checkpoint excluded every other and forced a journal to
disk. The engine opens it the way the application opens its own database.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.core.config import _overlay
from app.modules.work.catalog import WorkCatalog, default_lanes
from app.runtime.engine.dbos_engine import DbosJobEngine, _sqlite_system_connection


class TestSqliteSystemConnection:
    def test_opens_the_file_in_write_ahead_mode(self, tmp_path) -> None:
        connection = _sqlite_system_connection(f"sqlite:///{tmp_path / 'e.sqlite'}")()
        try:
            assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        finally:
            connection.close()

    @pytest.mark.parametrize(("setting", "level"), [("NORMAL", 1), ("FULL", 2)])
    def test_follows_the_configured_synchronisation(
        self, tmp_path, monkeypatch, setting, level
    ) -> None:
        monkeypatch.setitem(_overlay, "sqlite_synchronous", setting)
        connection = _sqlite_system_connection(f"sqlite:///{tmp_path / 'e.sqlite'}")()
        try:
            assert connection.execute("PRAGMA synchronous").fetchone() == (level,)
        finally:
            connection.close()

    def test_waits_out_a_locked_file(self, tmp_path) -> None:
        connection = _sqlite_system_connection(f"sqlite:///{tmp_path / 'e.sqlite'}")()
        try:
            assert connection.execute("PRAGMA busy_timeout").fetchone() == (30_000,)
        finally:
            connection.close()

    def test_converts_a_file_left_in_rollback_journal_mode(self, tmp_path) -> None:
        path = tmp_path / "old.sqlite"
        old = sqlite3.connect(path)
        old.execute("CREATE TABLE kept (value TEXT)")
        old.execute("INSERT INTO kept VALUES ('row')")
        old.commit()
        old.close()

        connection = _sqlite_system_connection(f"sqlite:///{path}")()
        try:
            assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
            assert connection.execute("SELECT value FROM kept").fetchall() == [("row",)]
        finally:
            connection.close()

    def test_is_usable_from_another_thread(self, tmp_path) -> None:
        import threading

        connection = _sqlite_system_connection(f"sqlite:///{tmp_path / 'e.sqlite'}")()
        seen: list[tuple[int]] = []
        worker = threading.Thread(
            target=lambda: seen.append(connection.execute("SELECT 1").fetchone())
        )
        worker.start()
        worker.join()
        connection.close()

        assert seen == [(1,)]

    def test_refuses_a_database_without_a_file(self) -> None:
        with pytest.raises(ValueError, match="file_backed_sqlite"):
            _sqlite_system_connection("sqlite://")

    def test_a_sqlite_engine_hands_dbos_that_connection(self, tmp_path) -> None:
        engine = DbosJobEngine(
            WorkCatalog(lanes=default_lanes()),
            system_database_url=f"sqlite:///{tmp_path / 'e.sqlite'}",
            schema=None,
            executor_id="test",
        )

        connection = engine._config()["db_engine_kwargs"]["creator"]()
        try:
            assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        finally:
            connection.close()

    def test_a_postgres_engine_keeps_the_library_connection(self) -> None:
        engine = DbosJobEngine(
            WorkCatalog(lanes=default_lanes()),
            system_database_url="postgresql+psycopg://vault:pw@db:5432/vault",
            schema="dbos",
            executor_id="test",
        )

        assert "db_engine_kwargs" not in engine._config()
