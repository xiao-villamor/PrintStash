"""An accepted download survives process-local coordinator state."""

import hashlib

import pytest
from sqlmodel import select

from app.db.models import BackgroundJob, StagingLease
from app.db.session import get_session_factory
from app.modules.ingestion import acquisition
from app.modules.ingestion.acquisition import AcquisitionJournal
from app.modules.ingestion.commands import claim_next, enqueue, execution_scope, release
from app.runtime.jobs import registry


@pytest.fixture
def acquisition_job(db_session):
    job_id = registry.create(session=db_session)
    enqueue(
        db_session,
        job_id,
        "url",
        {"request": {"url": "https://example.com/model.stl"}, "actor_user_id": 1},
    )
    db_session.commit()
    return job_id


class TestAcquisitionContract:
    @pytest.mark.asyncio
    async def test_records_the_streaming_download_receipt_without_rereading(
        self,
        db_session,
        acquisition_job,
        tmp_path,
        monkeypatch,
    ):
        path = tmp_path / "received.stl"
        path.write_bytes(b"received model")
        digest = hashlib.sha256(b"received model").hexdigest()

        async def native_download(_url: str):
            return path, "source.stl", digest

        async def inline_to_thread(function, *args, **kwargs):
            return function(*args, **kwargs)

        monkeypatch.setattr(
            "app.modules.ingestion.importer.download_to_staging_with_receipt",
            native_download,
        )
        monkeypatch.setattr(acquisition.asyncio, "to_thread", inline_to_thread)
        claim = claim_next(db_session)
        with execution_scope(claim):
            result = await AcquisitionJournal(
                acquisition_job, get_session_factory()
            ).download("download", "https://example.com/model.stl")

        assert result == (path, "source.stl")
        assert AcquisitionJournal(
            acquisition_job, get_session_factory()
        )._restore_download("download") == (path, "source.stl")

    def test_restores_download_receipts_from_a_fresh_coordinator(
        self, db_session, acquisition_job, tmp_path
    ):
        path = tmp_path / "received.stl"
        path.write_bytes(b"received model")
        journal = AcquisitionJournal(acquisition_job, get_session_factory())
        claim = claim_next(db_session)
        with execution_scope(claim):
            journal.save(
                "download",
                {"path": str(path), "filename": "source.stl"},
                receipt=(path, hashlib.sha256(path.read_bytes()).hexdigest()),
            )
        restored = AcquisitionJournal(
            acquisition_job, get_session_factory()
        )._restore_download("download")
        assert restored == (path, "source.stl")
        assert db_session.exec(
            select(StagingLease).where(
                StagingLease.background_job_id == acquisition_job
            )
        ).one().size_bytes == len(b"received model")

    def test_rejects_a_replaced_staging_object(
        self, db_session, acquisition_job, tmp_path
    ):
        path = tmp_path / "received.stl"
        path.write_bytes(b"received model")
        journal = AcquisitionJournal(acquisition_job, get_session_factory())
        journal.save(
            "download",
            {"path": str(path), "filename": "source.stl"},
            receipt=(path, hashlib.sha256(path.read_bytes()).hexdigest()),
        )
        path.unlink()
        path.write_bytes(b"foreign model")
        assert journal._restore_download("download") is None
        assert path.read_bytes() == b"foreign model"

    def test_a_stale_worker_cannot_replace_an_acquisition_checkpoint(
        self, db_session, acquisition_job
    ):
        journal = AcquisitionJournal(acquisition_job, get_session_factory())
        previous = claim_next(db_session)
        release(db_session, previous)
        current = claim_next(db_session)
        with execution_scope(current):
            journal.save("resolved", ["current"])
        with (
            execution_scope(previous),
            pytest.raises(RuntimeError, match="ingestion_claim_lost"),
        ):
            journal.save("resolved", ["stale"])
        assert journal.read("resolved") == ["current"]
        assert db_session.get(BackgroundJob, acquisition_job).replay_safe is True
