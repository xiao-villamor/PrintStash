"""Timing report statistics have explicit sample and percentile semantics."""

import math

import pytest

from scripts.render_statistics import summarize


class TestSummarize:
    def test_reports_nearest_rank_p95(self):
        result = summarize(list(range(1, 101)))
        assert result["count"] == 100
        assert result["median"] == 50.5
        assert result["p95"] == 95
        assert result["population_stddev"] == pytest.approx(math.sqrt(833.25))

    def test_reproduces_confidence_interval(self):
        assert summarize([1.0, 2.0, 3.0]) == summarize([1.0, 2.0, 3.0])
        assert summarize([5.0] * 30)["median_95_percent_bootstrap_ci"] == [5.0, 5.0]

    def test_retains_absence_of_successful_observations(self):
        result = summarize([])
        assert result["count"] == 0
        assert result["median"] is None
        assert result["p95"] is None

    @pytest.mark.parametrize("value", [-1, math.nan, math.inf])
    def test_refuses_invalid_observations(self, value):
        with pytest.raises(ValueError, match="invalid_timing_sample"):
            summarize([value])
