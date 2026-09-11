"""Render permits survive SQLite contention without admitting extra native work."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlmodel import select

from app.core.config import _overlay
from app.db.models import ThumbnailRenderSlot
from app.db.session import get_session_factory
from app.modules.media import compute_slots


@pytest.fixture
def locked_permit(threaded_hub_db, monkeypatch):
    monkeypatch.setitem(_overlay, "max_render_jobs", 1)
    factory = get_session_factory()
    with factory.scoped_session() as session:
        permit = compute_slots.acquire(session, "initial")
        assert permit is not None
        compute_slots.release(session, permit.id, "initial")
        session.commit()
    with factory.scoped_session() as blocker:
        blocker.execute(text("UPDATE thumbnail_render_slots SET lease_token = 'held'"))
        yield blocker
        blocker.rollback()


class TestAcquire:
    def test_retries_transient_sqlite_lock(self, locked_permit, monkeypatch):
        monkeypatch.setattr(
            compute_slots.time, "sleep", lambda _: locked_permit.rollback()
        )

        with get_session_factory().scoped_session() as session:
            permit = compute_slots.acquire(session, "recovered")

            assert permit is not None
            assert permit.lease_token == "recovered"
            assert len(session.exec(select(ThumbnailRenderSlot)).all()) == 1

    def test_bounds_persistent_sqlite_lock(self, locked_permit, monkeypatch):
        monkeypatch.setattr(compute_slots.time, "sleep", lambda _: None)

        with get_session_factory().scoped_session() as session:
            with pytest.raises(OperationalError, match="locked"):
                compute_slots.acquire(session, "denied")

        locked_permit.rollback()
        with get_session_factory().scoped_session() as session:
            assert session.exec(select(ThumbnailRenderSlot)).one().lease_token is None

    @pytest.mark.parametrize(
        "token,seconds",
        [
            pytest.param("", 900, id="empty-token"),
            pytest.param("valid", 0, id="zero-lease"),
            pytest.param("valid", 901, id="over-lease-cap"),
        ],
    )
    def test_rejects_invalid_lease(self, db_session, token, seconds):
        with pytest.raises(ValueError, match="invalid_compute_lease"):
            compute_slots.acquire(db_session, token, lease_seconds=seconds)

        assert db_session.exec(select(ThumbnailRenderSlot)).all() == []


class TestRetry:
    def test_propagates_non_lock_database_failure(self, db_session):
        with pytest.raises(OperationalError, match="no such table"):
            compute_slots._retry(
                db_session,
                lambda: db_session.execute(
                    text("SELECT * FROM nonexistent_permit_table")
                ),
            )
