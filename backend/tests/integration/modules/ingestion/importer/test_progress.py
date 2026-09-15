"""Archive progress stays useful while files run, fail, or are skipped.

These drive the real ingestion pipeline and observe persisted parent statuses;
counting only at completion left the task list displaying zero for whole imports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from app.db.session import get_session_factory
from app.modules.ingestion import importer
from app.runtime.jobs import registry
from app.schemas.ingest import IngestJobStatus

RunImport = Callable[[list[tuple[str, str | None]]], list[IngestJobStatus]]


@pytest.fixture
def run_import(local_storage: Path, monkeypatch: pytest.MonkeyPatch) -> RunImport:
    def run(entries: list[tuple[str, str | None]]) -> list[IngestJobStatus]:
        staged = []
        for index, (name, content) in enumerate(entries):
            path = local_storage / f"{index}-{Path(name).name}"
            if content is not None:
                path.write_text(content)
            staged.append((path, name))
        job_id = registry.create(kind="archive")
        snapshots = []
        update = registry.update

        def observe(updated_id, **fields):
            update(updated_id, **fields)
            if updated_id == job_id:
                status = registry.get(job_id)
                assert status is not None
                snapshots.append(status.model_copy(deep=True))

        monkeypatch.setattr(registry, "update", observe)
        importer.import_assets(
            job_id=job_id,
            staged_files=staged,
            collection=None,
            tags=None,
            source_url=None,
            actor_user_id=None,
            session_factory=get_session_factory(),
        )
        return snapshots

    return run


class TestImportAssets:
    def test_reports_completed_files_before_the_next_file_finishes(
        self, run_import: RunImport
    ) -> None:
        snapshots = run_import(
            [("first.gcode", "; first\nG28\n"), ("second.gcode", "; second\nG28\n")]
        )

        running = next(s for s in snapshots if s.current_item == "second.gcode")
        assert running.state == "running"
        assert running.processed == 1
        assert running.total == 2
        assert running.succeeded == 1

    def test_advances_progress_within_the_first_file(
        self, run_import: RunImport
    ) -> None:
        snapshots = run_import([("first.gcode", "G28\n"), ("second.gcode", "G29\n")])

        assert any(s.processed == 0 and 0 < (s.progress or 0) < 50 for s in snapshots)

    def test_progress_never_moves_backwards(self, run_import: RunImport) -> None:
        snapshots = run_import([("first.gcode", "G28\n"), ("second.gcode", "G29\n")])

        progress = [s.progress or 0 for s in snapshots]
        assert progress == sorted(progress)
        assert progress[-1] == 100

    def test_shows_only_the_current_filename(self, run_import: RunImport) -> None:
        snapshots = run_import([("folder/first.gcode", "G28\n")])

        assert {s.current_item for s in snapshots if s.current_item} == {"first.gcode"}

    def test_counts_a_skipped_file_before_the_next_file(
        self, run_import: RunImport
    ) -> None:
        snapshots = run_import([("notes.txt", "notes"), ("second.gcode", "G28\n")])

        running = next(s for s in snapshots if s.current_item == "second.gcode")
        assert running.processed == 1
        assert running.skipped == 1
        assert snapshots[-1].processed == snapshots[-1].total == 2
        assert snapshots[-1].completion == "partial"

    def test_counts_a_duplicate_before_the_next_file(
        self, run_import: RunImport
    ) -> None:
        snapshots = run_import(
            [
                ("first.gcode", "G28\n"),
                ("duplicate.gcode", "G28\n"),
                ("third.gcode", "G29\n"),
            ]
        )

        running = next(s for s in snapshots if s.current_item == "third.gcode")
        assert running.processed == 2
        assert running.deduplicated == 1

    def test_reports_a_failed_file_before_continuing(
        self, run_import: RunImport
    ) -> None:
        snapshots = run_import([("missing.gcode", None), ("second.gcode", "G28\n")])

        running = next(s for s in snapshots if s.current_item == "second.gcode")
        assert running.processed == 1
        assert running.failed == 1
        assert snapshots[-1].completion == "partial"
        assert snapshots[-1].succeeded == 1
        assert snapshots[-1].failed == 1

    def test_finishes_an_entirely_failed_import(self, run_import: RunImport) -> None:
        snapshots = run_import([("missing.gcode", None)])

        assert snapshots[-1].state == "failed"
        assert snapshots[-1].processed == 1
        assert snapshots[-1].failed == 1

    def test_rejects_an_empty_import(self, run_import: RunImport) -> None:
        snapshots = run_import([])

        assert snapshots[-1].state == "failed"
        assert snapshots[-1].error == "no_importable_files"
