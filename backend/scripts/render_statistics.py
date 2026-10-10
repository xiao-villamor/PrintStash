"""Reproducible descriptive statistics; failed observations stay in raw reports."""

from __future__ import annotations

import math
import random
from statistics import median, pstdev


def summarize(samples: list[float]) -> dict[str, object]:
    if any(not math.isfinite(x) or x < 0 for x in samples):
        raise ValueError("invalid_timing_sample")
    if not samples:
        return {
            "count": 0,
            "median": None,
            "p95": None,
            "population_stddev": None,
            "median_95_percent_bootstrap_ci": None,
        }
    ordered = sorted(samples)
    generator = random.Random(16)
    bootstraps = sorted(
        median(generator.choices(samples, k=len(samples))) for _ in range(2000)
    )
    return {
        "count": len(samples),
        "median": median(samples),
        "p95": ordered[math.ceil(len(samples) * 0.95) - 1],
        "population_stddev": pstdev(samples),
        "median_95_percent_bootstrap_ci": [bootstraps[49], bootstraps[1949]],
        "method": "nearest-rank p95; percentile bootstrap median CI, 2000 resamples, seed 16",
    }
