"""Fairness observation keeps only pending uploads in its read window."""

import pytest

from tests.fakes.ingestion_fairness_progress import ForegroundProgress


class TestForegroundProgress:
    def test_completed_uploads_leave_the_observation_window(self):
        progress = ForegroundProgress()
        for index in range(1000):
            job_id = f"job-{index}"
            progress.accept(job_id)
            assert progress.pending == {job_id}
            progress.complete({job_id})
            assert progress.pending == set()
        assert progress.submitted == progress.ready == 1000

    def test_incomplete_uploads_stay_pending(self):
        progress = ForegroundProgress()
        progress.accept("one")
        progress.accept("two")
        progress.complete({"one"})
        assert progress.pending == {"two"}
        assert progress.submitted == 2
        assert progress.ready == 1

    def test_refuses_duplicate_uploads(self):
        progress = ForegroundProgress()
        progress.accept("one")
        progress.complete({"one"})
        with pytest.raises(ValueError, match="fairness_duplicate_upload"):
            progress.accept("one")
        assert progress.submitted == progress.ready == 1

    def test_refuses_unaccepted_completion(self):
        progress = ForegroundProgress()
        progress.accept("one")
        with pytest.raises(ValueError, match="fairness_unaccepted_completion"):
            progress.complete({"one", "unknown"})
        assert progress.pending == {"one"}
        assert progress.ready == 0
