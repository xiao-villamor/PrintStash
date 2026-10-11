"""Export bounded mesh costs from the supervising process, never worker globals."""

from __future__ import annotations

import json
import os
from dataclasses import asdict

from prometheus_client import Counter, Histogram

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import registry
from app.modules.media.mesh_telemetry import (
    AdmissionStats,
    PhaseStats,
    SupervisionStats,
)

logger = get_logger(__name__)
_worker_exits = Counter(
    "printstash_mesh_worker_exits_total",
    "Supervised process exit causes.",
    ("cause",),
    registry=registry,
)
_worker_duration = Histogram(
    "printstash_mesh_worker_duration_seconds",
    "Process supervision including startup and cleanup.",
    ("cause",),
    registry=registry,
)
_worker_rss = Histogram(
    "printstash_mesh_worker_peak_tree_rss_bytes",
    "Largest sampled resident size of the supervised process tree; unobserved values omitted.",
    ("cause",),
    buckets=(2**20, 2**24, 2**26, 2**28, 2**30, 2**32),
    registry=registry,
)
_worker_reply = Counter(
    "printstash_mesh_worker_reply_bytes_total",
    "Reply bytes read, including incomplete replies.",
    ("cause",),
    registry=registry,
)
_phase_duration = Histogram(
    "printstash_mesh_phase_duration_seconds",
    "Sequential stage duration, inclusive of nested native work; excludes uninstrumented gaps.",
    ("phase", "outcome"),
    registry=registry,
)
_phase_bytes = Counter(
    "printstash_mesh_phase_bytes_total",
    "Known stage input/output sizes (not measured IO traffic).",
    ("phase", "direction"),
    registry=registry,
)
_phase_triangles = Histogram(
    "printstash_mesh_phase_triangles",
    "Known triangle counts at the stage boundary.",
    ("phase",),
    buckets=(12, 1000, 10000, 100000, 1000000, 2000000),
    registry=registry,
)


_admission_duration = Histogram(
    "printstash_mesh_admission_duration_seconds",
    "Native resource queue time, excluding process execution.",
    ("outcome",),
    registry=registry,
)
_admission_memory = Histogram(
    "printstash_mesh_admission_requested_bytes",
    "Memory requested at native resource admission.",
    ("outcome",),
    buckets=(2**24, 2**26, 2**28, 2**29, 2**30, 2**32),
    registry=registry,
)


def record_admission(stats: AdmissionStats) -> None:
    try:
        logger.info(
            "mesh_admission %s",
            json.dumps(
                {
                    "version": 1,
                    "process_id": os.getpid(),
                    "process_role": settings.process_role.value,
                    **asdict(stats),
                },
                allow_nan=False,
                separators=(",", ":"),
            ),
        )
        outcome = stats.outcome.value
        _admission_duration.labels(outcome).observe(stats.elapsed_ns / 1_000_000_000)
        _admission_memory.labels(outcome).observe(stats.requested_bytes)
    except Exception:
        logger.exception("failed to publish native admission metrics")


def record_supervision(stats: SupervisionStats) -> None:
    try:
        # Put bounded JSON in the message itself: the production text formatter
        # does not serialize `extra`, and split workers expose stdout, not HTTP.
        logger.info(
            "mesh_supervision %s",
            json.dumps(
                {
                    "version": 1,
                    "process_id": os.getpid(),
                    "process_role": settings.process_role.value,
                    **asdict(stats),
                },
                allow_nan=False,
                separators=(",", ":"),
            ),
        )
        cause = stats.exit_cause.value
        _worker_exits.labels(cause).inc()
        _worker_duration.labels(cause).observe(stats.elapsed_ns / 1_000_000_000)
        _worker_reply.labels(cause).inc(stats.reply_bytes)
        if stats.peak_tree_rss_bytes is not None:
            _worker_rss.labels(cause).observe(stats.peak_tree_rss_bytes)
    except Exception:
        # Telemetry is best-effort and must never alter cancellation or cleanup.
        logger.exception("failed to publish mesh supervision metrics")


def record_phases(stats: tuple[PhaseStats, ...], supervision: SupervisionStats) -> None:
    try:
        logger.info(
            "mesh_phases %s",
            json.dumps(
                {
                    "version": 1,
                    "execution_id": supervision.execution_id,
                    "process_id": os.getpid(),
                    "process_role": settings.process_role.value,
                    "stages": [asdict(stage) for stage in stats],
                },
                allow_nan=False,
                separators=(",", ":"),
            ),
        )
        for stage in stats:
            phase = stage.phase.value
            _phase_duration.labels(phase, stage.outcome.value).observe(
                stage.elapsed_ns / 1_000_000_000
            )
            if stage.input_bytes is not None:
                _phase_bytes.labels(phase, "input").inc(stage.input_bytes)
            if stage.output_bytes is not None:
                _phase_bytes.labels(phase, "output").inc(stage.output_bytes)
            if stage.triangle_count is not None:
                _phase_triangles.labels(phase).observe(stage.triangle_count)
    except Exception:
        logger.exception("failed to publish mesh phase metrics")
