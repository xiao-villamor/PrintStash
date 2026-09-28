"""Archive import jobs expose live model counts and file progress.

The task center reads persisted Job status while the import is still running.
Keeping counts only in the terminal result leaves its counter at zero.
"""

from pathlib import Path

from app.db.models import JobKind
from app.modules.ingestion import importer
from app.modules.work.jobs import jobs


class TestImportAssets:
    def test_reports_imported_models_before_the_next_file(
        self, make_job, make_user, monkeypatch, tmp_path
    ) -> None:
        owner = make_user("archive-progress-owner")
        job = make_job(kind=JobKind.INGESTION_ARCHIVE_SELECTION, owner=owner)
        seen = []

        def ingest(staged: Path, original_filename: str, **_kwargs):
            seen.append(jobs.get(job.id))
            return {
                "name": original_filename,
                "model_id": len(seen),
                "file_id": len(seen),
            }

        monkeypatch.setattr(importer, "_ingest_one_file", ingest)

        importer.import_assets(
            job_id=job.id,
            staged_files=[
                (tmp_path / "one.stl", "one.stl"),
                (tmp_path / "two.stl", "two.stl"),
            ],
            collection="Archive",
            tags=None,
            source_url=None,
            actor_user_id=owner.id,
            session_factory=lambda: None,
        )

        assert seen[1] is not None
        assert (seen[1].processed, seen[1].succeeded, seen[1].total) == (1, 1, 2)

    def test_advances_progress_after_a_failed_file(
        self, make_job, make_user, monkeypatch, tmp_path
    ) -> None:
        owner = make_user("archive-failed-progress-owner")
        job = make_job(kind=JobKind.INGESTION_ARCHIVE_SELECTION, owner=owner)
        seen = []

        def ingest(staged: Path, original_filename: str, **_kwargs):
            seen.append(jobs.get(job.id))
            if original_filename == "one.stl":
                return {"name": original_filename, "error": "import_failed"}
            return {"name": original_filename, "model_id": 1, "file_id": 1}

        monkeypatch.setattr(importer, "_ingest_one_file", ingest)

        importer.import_assets(
            job_id=job.id,
            staged_files=[
                (tmp_path / "one.stl", "one.stl"),
                (tmp_path / "two.stl", "two.stl"),
            ],
            collection="Archive",
            tags=None,
            source_url=None,
            actor_user_id=owner.id,
            session_factory=lambda: None,
        )

        assert seen[1] is not None
        assert (seen[1].processed, seen[1].failed, seen[1].progress) == (1, 1, 50.0)

    def test_advances_progress_after_a_skipped_file(
        self, make_job, make_user, monkeypatch, tmp_path
    ) -> None:
        owner = make_user("archive-skipped-progress-owner")
        job = make_job(kind=JobKind.INGESTION_ARCHIVE_SELECTION, owner=owner)
        seen = []

        def ingest(staged: Path, original_filename: str, **_kwargs):
            seen.append(jobs.get(job.id))
            if original_filename == "one.txt":
                return None
            return {"name": original_filename, "model_id": 1, "file_id": 1}

        monkeypatch.setattr(importer, "_ingest_one_file", ingest)

        importer.import_assets(
            job_id=job.id,
            staged_files=[
                (tmp_path / "one.txt", "one.txt"),
                (tmp_path / "two.stl", "two.stl"),
            ],
            collection="Archive",
            tags=None,
            source_url=None,
            actor_user_id=owner.id,
            session_factory=lambda: None,
        )

        assert seen[1] is not None
        assert (seen[1].processed, seen[1].skipped, seen[1].progress) == (1, 1, 50.0)
