"""SQL cursor timing counts completed and failed statements in the active request only."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from app.core.request_timing import capture_sql_timing


class TestCaptureSqlTiming:
    def test_counts_each_statement(self, db_session: Session) -> None:
        with capture_sql_timing() as stats:
            db_session.execute(text("SELECT 1"))
            db_session.execute(text("SELECT 2"))

        assert stats.statement_count == 2
        assert stats.duration_ms >= 0

    def test_counts_a_failed_statement(self, db_session: Session) -> None:
        with capture_sql_timing() as stats:
            with pytest.raises(OperationalError):
                db_session.execute(text("SELECT * FROM missing_request_timing_table"))

        assert stats.statement_count == 1
        assert stats.duration_ms >= 0

    def test_resets_the_request_scope(self, db_session: Session) -> None:
        with capture_sql_timing() as first:
            db_session.execute(text("SELECT 1"))
        with capture_sql_timing() as second:
            pass

        assert first.statement_count == 1
        assert second.statement_count == 0
