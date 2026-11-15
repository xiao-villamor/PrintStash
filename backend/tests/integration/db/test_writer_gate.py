"""The SQLite writer gate on a real database file.

The contract: a connection holds the gate from its first write until SQLite
has ended that transaction, by commit, rollback, close or autocommit; a
second writer starts as soon as the first ends instead of polling; and a wait
longer than the busy timeout falls back to SQLite's own locking rather than
deadlocking.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from app.db.writer_gate import WriterGate, gated_connection_class, install


def _engine(path: Path, gate: WriterGate) -> Engine:
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={
            "check_same_thread": False,
            "factory": gated_connection_class(gate),
            "timeout": 5,
        },
    )
    install(engine, gate)
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        conn.exec_driver_sql("CREATE TABLE t (x INTEGER)")
    return engine


@pytest.fixture
def gate() -> WriterGate:
    return WriterGate(timeout_seconds=5)


@pytest.fixture
def engine(tmp_path, gate) -> Engine:
    built = _engine(tmp_path / "gate.db", gate)
    yield built
    built.dispose()


def _held(gate: WriterGate) -> bool:
    if gate._lock.acquire(blocking=False):
        gate._lock.release()
        return False
    return True


class TestGateLifetime:
    def test_a_write_takes_the_gate(self, engine, gate) -> None:
        with engine.connect() as conn:
            conn.execute(text("INSERT INTO t VALUES (1)"))

            assert _held(gate)
            conn.rollback()

    def test_a_read_leaves_the_gate_free(self, engine, gate) -> None:
        with engine.connect() as conn:
            conn.execute(text("SELECT count(*) FROM t")).scalar_one()

            assert not _held(gate)

    def test_commit_returns_the_gate(self, engine, gate) -> None:
        with engine.connect() as conn:
            conn.execute(text("INSERT INTO t VALUES (1)"))
            conn.commit()

            assert not _held(gate)

    def test_rollback_returns_the_gate(self, engine, gate) -> None:
        with engine.connect() as conn:
            conn.execute(text("INSERT INTO t VALUES (1)"))
            conn.rollback()

            assert not _held(gate)

    def test_returning_the_connection_returns_the_gate(self, engine, gate) -> None:
        conn = engine.connect()
        conn.execute(text("INSERT INTO t VALUES (1)"))

        conn.close()

        assert not _held(gate)

    def test_an_immediate_begin_takes_the_gate(self, engine, gate) -> None:
        with engine.connect() as conn:
            conn.exec_driver_sql("BEGIN IMMEDIATE")

            assert _held(gate)
            conn.rollback()

    def test_a_failed_write_inside_a_transaction_keeps_the_gate(
        self, engine, gate
    ) -> None:
        with engine.connect() as conn:
            conn.execute(text("INSERT INTO t VALUES (1)"))
            with pytest.raises(OperationalError, match="no such table"):
                conn.execute(text("INSERT INTO missing VALUES (1)"))

            assert _held(gate)
            conn.rollback()

    def test_an_autocommit_write_returns_the_gate(self, engine, gate) -> None:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text("INSERT INTO t VALUES (1)"))

            assert not _held(gate)


class TestHandover:
    def test_a_waiting_writer_starts_when_the_holder_commits(self, engine) -> None:
        holder = engine.connect()
        holder.execute(text("INSERT INTO t VALUES (1)"))
        started = threading.Event()
        finished: list[float] = []

        def second() -> None:
            with engine.connect() as conn:
                started.set()
                conn.execute(text("INSERT INTO t VALUES (2)"))
                finished.append(time.monotonic())
                conn.commit()

        thread = threading.Thread(target=second)
        thread.start()
        started.wait(5)
        time.sleep(0.2)
        assert finished == []
        released = time.monotonic()
        holder.commit()
        holder.close()
        thread.join(5)

        assert len(finished) == 1 and finished[0] - released < 0.05

    def test_a_wait_past_the_busy_timeout_falls_back_to_sqlite(self, tmp_path) -> None:
        gate = WriterGate(timeout_seconds=0.1)
        engine = _engine(tmp_path / "fallback.db", gate)
        try:
            first = engine.connect()
            first.execute(text("INSERT INTO t VALUES (1)"))
            # Same thread, second connection: the gate times out instead of
            # deadlocking, and SQLite's own lock refuses the write.
            second = engine.connect().execution_options()
            second.exec_driver_sql("PRAGMA busy_timeout=50")
            with pytest.raises(OperationalError, match="locked"):
                second.execute(text("INSERT INTO t VALUES (2)"))
            second.close()
            first.rollback()
            first.close()

            assert not _held(gate)
        finally:
            engine.dispose()
