"""The entry ledger owns one batch and preserves historical published outcomes."""

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.db.models import File, IngestionEntry, IngestionEntryState
from tests.factories import build_ingestion_entry


class TestIngestionEntry:
    @pytest.mark.parametrize(
        "changes",
        [
            {"job_id": None},
            {"entry_key": ""},
            {"ordinal": -1},
            {"size_bytes": -1},
            {"error_code": "not_failed"},
            {"retryable": True},
        ],
    )
    def test_rejects_invalid_entry_states(self, db_session, make_job, changes):
        job = make_job()
        with pytest.raises(IntegrityError, match="CHECK constraint"):
            build_ingestion_entry(db_session, job, **changes)
        db_session.rollback()
        assert db_session.exec(select(IngestionEntry)).all() == []

    def test_preserves_published_history_when_the_file_is_purged(
        self, db_session, make_job, make_model, make_file
    ):
        file = make_file(make_model())
        entry = build_ingestion_entry(
            db_session,
            make_job(),
            state=IngestionEntryState.IMPORTED,
            model_id=file.model_id,
            file_id=file.id,
        )
        db_session.exec(delete(File).where(File.id == file.id))
        db_session.commit()
        db_session.expire_all()
        historical = db_session.get(IngestionEntry, entry.id)
        assert historical is not None
        assert historical.state is IngestionEntryState.IMPORTED
        assert historical.file_id is None
        assert historical.model_id == file.model_id

    def test_refuses_duplicate_identity_for_the_same_owner(self, db_session, make_job):
        job = make_job()
        build_ingestion_entry(db_session, job, identity="same")
        with pytest.raises(IntegrityError, match="UNIQUE constraint"):
            build_ingestion_entry(db_session, job, identity="same")
        db_session.rollback()
        assert len(db_session.exec(select(IngestionEntry)).all()) == 1
