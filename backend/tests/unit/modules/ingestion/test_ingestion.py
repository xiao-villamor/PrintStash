"""Stage reports of one staged file's commit.

Each report is a write transaction on the Job. A file that is hashed and
committed in well under a second gains nothing from "hashing" and "persisting"
rows nobody can read in time; a file that takes long enough to watch still
reports every stage it enters.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.ingestion import ingestion


class _Job:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    def update(self, **fields: Any) -> None:
        self.updates.append(fields)


@pytest.fixture
def clock(monkeypatch) -> list[float]:
    now = [100.0]
    monkeypatch.setattr(ingestion.time, "monotonic", lambda: now[0])
    return now


class TestPacedReport:
    def test_a_commit_finished_at_once_reports_no_stage(self, clock) -> None:
        job = _Job()
        report = ingestion._paced_report(job)

        report(stage="hashing")
        clock[0] += 0.4
        report(stage="ingesting")

        assert job.updates == []

    def test_a_stage_entered_after_the_threshold_is_reported(self, clock) -> None:
        job = _Job()
        report = ingestion._paced_report(job)

        clock[0] += ingestion._STAGE_REPORT_SECONDS
        report(stage="ingesting", progress=50)

        assert job.updates == [{"stage": "ingesting", "progress": 50}]

    def test_every_later_stage_of_a_long_commit_is_reported(self, clock) -> None:
        job = _Job()
        report = ingestion._paced_report(job)

        report(stage="hashing")
        clock[0] += 5
        report(stage="ingesting")
        report(stage="verifying")

        assert [update["stage"] for update in job.updates] == ["ingesting", "verifying"]

    def test_each_commit_measures_from_its_own_start(self, clock) -> None:
        first = _Job()
        ingestion._paced_report(first)
        clock[0] += 30
        second = _Job()

        ingestion._paced_report(second)(stage="hashing")

        assert second.updates == []
