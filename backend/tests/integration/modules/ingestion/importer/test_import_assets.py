"""Archive imports persist unit progress before consuming the next staged file."""

from pathlib import Path

from sqlmodel import select

from app.db.models import File, IngestionEntryState, JobKind, Metadata
from app.db.session import get_session_factory
from app.modules.ingestion import batch_store, importer, ingestion
from app.modules.ingestion.batch_contracts import JobBatch
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.work.jobs import jobs
from tests.factories.content import gcode
from tests.factories.ops import build_job_context


class _ProgressProbe:
    """Observe committed Job status immediately before each real file commit."""

    def __init__(self, job_id):
        self.job_id = job_id
        self.seen = []
        self.ingest = importer._ingest_one_file

    def __call__(self, staged: Path, original_filename: str, **kwargs):
        self.seen.append(jobs.get(self.job_id))
        return self.ingest(staged, original_filename, **kwargs)


class _FailFirstCommit:
    """Raise at the canonical pre-publication boundary once, then allow commit."""

    def __init__(self):
        self.calls = 0

    def __call__(self, stage, _key):
        if stage == "before_commit":
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("import_failed")


def _assert_stored_artifact(record, body):
    assert record.published
    with get_session_factory().scoped_session() as session:
        stored = session.exec(
            select(File, Metadata)
            .join(Metadata, Metadata.file_id == File.id)
            .where(File.id == record.file_id)
        ).one()
        file, metadata = stored
        assert file.model_id == record.model_id
        assert metadata.file_id == file.id
        assert get_backend().read_bytes(file.path) == body


def _import(job, owner, staged_files):
    importer.import_assets(
        job_context=build_job_context(job.id),
        staged_files=staged_files,
        collection="Archive",
        tags=None,
        source_url=None,
        actor_user_id=owner.id,
        session_factory=get_session_factory(),
    )


class TestImportAssets:
    def test_reports_imported_models_before_the_next_file(
        self, make_job, make_user, monkeypatch, tmp_path, local_storage
    ) -> None:
        owner = make_user("archive-progress-owner")
        job = make_job(kind=JobKind.INGESTION_ARCHIVE_SELECTION, owner=owner)
        first, second = tmp_path / "one.gcode", tmp_path / "two.gcode"
        first_body, second_body = gcode(marker="one"), gcode(marker="two")
        first.write_bytes(first_body)
        second.write_bytes(second_body)
        probe = _ProgressProbe(job.id)
        monkeypatch.setattr(importer, "_ingest_one_file", probe)

        _import(job, owner, [(first, first.name), (second, second.name)])

        assert probe.seen[1] is not None
        assert (
            probe.seen[1].processed,
            probe.seen[1].succeeded,
            probe.seen[1].total,
        ) == (1, 1, 2)
        records = batch_store.results(JobBatch(job.id), limit=2)
        assert len(records) == 2
        _assert_stored_artifact(records[0], first_body)
        _assert_stored_artifact(records[1], second_body)
        assert not first.exists() and not second.exists()

    def test_advances_progress_after_a_failed_file(
        self, make_job, make_user, monkeypatch, tmp_path, local_storage
    ) -> None:
        owner = make_user("archive-failed-progress-owner")
        job = make_job(kind=JobKind.INGESTION_ARCHIVE_SELECTION, owner=owner)
        first, second = tmp_path / "one.gcode", tmp_path / "two.gcode"
        first.write_bytes(gcode(marker="failed"))
        second_body = gcode(marker="successful")
        second.write_bytes(second_body)
        probe = _ProgressProbe(job.id)
        fault = _FailFirstCommit()
        monkeypatch.setattr(importer, "_ingest_one_file", probe)
        monkeypatch.setattr(ingestion, "_fault_injection_checkpoint", fault)

        _import(job, owner, [(first, first.name), (second, second.name)])

        assert probe.seen[1] is not None
        assert (
            probe.seen[1].processed,
            probe.seen[1].failed,
            probe.seen[1].progress,
        ) == (1, 1, 50.0)
        records = batch_store.results(JobBatch(job.id), limit=2)
        assert len(records) == 2 and fault.calls == 2
        assert records[0].state is IngestionEntryState.FAILED
        assert records[0].error_code == "import_failed"
        assert records[0].file_id is None and records[0].model_id is None
        _assert_stored_artifact(records[1], second_body)
        with get_session_factory().scoped_session() as session:
            assert (
                session.exec(
                    select(File).where(File.original_filename == first.name)
                ).all()
                == []
            )
        assert not first.exists() and not second.exists()

    def test_advances_progress_after_a_skipped_file(
        self, make_job, make_user, monkeypatch, tmp_path, local_storage
    ) -> None:
        owner = make_user("archive-skipped-progress-owner")
        job = make_job(kind=JobKind.INGESTION_ARCHIVE_SELECTION, owner=owner)
        first, second = tmp_path / "one.txt", tmp_path / "two.gcode"
        first.write_bytes(b"unsupported staged notes")
        second_body = gcode(marker="after-skipped")
        second.write_bytes(second_body)
        probe = _ProgressProbe(job.id)
        monkeypatch.setattr(importer, "_ingest_one_file", probe)

        _import(job, owner, [(first, first.name), (second, second.name)])

        assert probe.seen[1] is not None
        assert (
            probe.seen[1].processed,
            probe.seen[1].skipped,
            probe.seen[1].progress,
        ) == (1, 1, 50.0)
        records = batch_store.results(JobBatch(job.id), limit=2)
        assert len(records) == 2
        assert records[0].state is IngestionEntryState.SKIPPED
        assert records[0].error_code == "unsupported_file_type"
        assert records[0].file_id is None
        _assert_stored_artifact(records[1], second_body)
        assert not first.exists() and not second.exists()
