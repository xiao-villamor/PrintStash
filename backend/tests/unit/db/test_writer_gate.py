"""Which statements queue at the SQLite writer gate.

The contract: every statement that makes SQLite take its write lock (an
immediate or exclusive ``BEGIN``, DML, DDL, or a CTE that writes) is gated,
and nothing that only reads is, so readers never wait behind a writer.
"""

from __future__ import annotations

import pytest

from app.db.writer_gate import takes_write_lock


class TestTakesWriteLock:
    @pytest.mark.parametrize(
        "statement",
        [
            "BEGIN IMMEDIATE",
            "begin exclusive",
            "INSERT INTO jobs (id) VALUES (?)",
            "  update jobs SET state=? WHERE id=?",
            "DELETE FROM work_fences WHERE name = ?",
            "REPLACE INTO t VALUES (1)",
            "CREATE INDEX ix ON t (x)",
            "DROP TABLE t",
            "ALTER TABLE t ADD COLUMN y",
            "WITH picked AS (SELECT id FROM t) UPDATE t SET x = 1",
            "WITH rows(x) AS (VALUES (1)) INSERT INTO t SELECT x FROM rows",
        ],
    )
    def test_gates_a_statement_that_writes(self, statement) -> None:
        assert takes_write_lock(statement)

    @pytest.mark.parametrize(
        "statement",
        [
            "SELECT * FROM jobs",
            "BEGIN",
            "begin deferred",
            "WITH recent AS (SELECT id FROM jobs) SELECT * FROM recent",
            "PRAGMA busy_timeout=5000",
            "SAVEPOINT sa_savepoint_1",
            "SELECT updated_at FROM jobs",
        ],
    )
    def test_leaves_a_statement_that_only_reads(self, statement) -> None:
        assert not takes_write_lock(statement)
