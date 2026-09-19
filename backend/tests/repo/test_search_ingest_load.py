"""Load acceptance requires actual backfill progress, not a stale status label."""

import pytest

from tests.fakes.search_ingest_load import backfill_overlapped


@pytest.fixture
def observations():
    def state(indexed):
        return {"id": 1, "state": "building", "phase": "backfill", "indexed": indexed}

    return [
        {"generation_before": state(8), "generation_after": state(16)},
        {"generation_before": state(16), "generation_after": state(24)},
    ]


class TestBackfillOverlap:
    def test_accepts_progress_during_the_loaded_sample(self, observations):
        assert backfill_overlapped(observations)

    @pytest.mark.parametrize(
        "failure",
        ["empty", "stalled", "switched", "regressed", "finished", "cancelled"],
    )
    def test_rejects_invalid_load_evidence(self, observations, failure):
        final = observations[-1]["generation_after"]
        if failure == "empty":
            observations = []
        elif failure == "stalled":
            for row in observations:
                for state in row.values():
                    state["indexed"] = 8
        elif failure == "switched":
            final["id"] = 2
        elif failure == "regressed":
            observations[-1]["generation_before"]["indexed"] = 7
        elif failure == "finished":
            final["phase"] = "ready"
        else:
            final["state"] = "cancelled"
        assert not backfill_overlapped(observations)
