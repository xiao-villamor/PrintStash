"""Prometheus metrics registry and instruments.

A single process-local ``CollectorRegistry`` holds every PrintStash metric so
the ``/metrics`` endpoint can render them in one pass. The app runs
API process per vault, so the default per-process registry semantics are
correct for request and printer metrics. A split deployment's standalone workers have no HTTP scrape endpoint. Their
process-local counters are not visible at the API scrape; mesh costs also leave
those workers through structured stdout records. Job counts are read from the
database and are therefore the same from every process.

Instruments:
- ``http_request_duration`` — request latency histogram, labelled by method,
  matched route template, and status. The route *template* (not the raw path)
  keeps label cardinality bounded.
- ``jobs`` — terminal background Job counter, labelled by definition/result.
- ``lane_depth`` / ``step_duration`` / ``reconcile_*`` — engine lanes, step
  latency and reconciler passes.
- ``printer_status`` — gauge of live printers by provider/status, set at scrape
  time so it always reflects the current fleet.
- ``app_info`` — static version info.
- ``fleet_jobs`` — current fleet jobs by normalized state.
- ``fleet_scheduler`` — scheduler liveness/tick timestamp and dispatch outcomes.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, Info

# Process-local registry: everything we expose is registered here.
registry = CollectorRegistry()

http_request_duration = Histogram(
    "printstash_http_request_duration_seconds",
    "HTTP request latency in seconds.",
    labelnames=("method", "path", "status"),
    registry=registry,
)

jobs_terminal = Counter(
    "printstash_jobs_total",
    "Background Jobs that reached a terminal state, by definition and outcome.",
    labelnames=("kind", "result"),
    registry=registry,
)

job_duration = Histogram(
    "printstash_job_duration_seconds",
    "Wall-clock duration of terminal background Jobs.",
    labelnames=("kind", "result"),
    registry=registry,
)

stuck_jobs = Gauge(
    "printstash_stuck_jobs",
    "Active Jobs whose row has not changed for three reconcile intervals.",
    registry=registry,
)

lane_depth = Gauge(
    "printstash_lane_depth",
    "Executions per lane, by engine state (queued or running).",
    labelnames=("lane", "state"),
    registry=registry,
)

step_duration = Histogram(
    "printstash_job_step_duration_seconds",
    "Duration of one job step attempt, by definition, step and outcome.",
    labelnames=("kind", "step", "result"),
    registry=registry,
)

job_resubmits = Counter(
    "printstash_job_resubmits_total",
    "Interrupted executions the reconciler resubmitted, by definition.",
    labelnames=("kind",),
    registry=registry,
)

reconcile_pass_duration = Histogram(
    "printstash_reconcile_pass_duration_seconds",
    "Duration of one reconciler pass over one source.",
    labelnames=("source",),
    registry=registry,
)

reconcile_outcomes = Counter(
    "printstash_reconcile_outcomes_total",
    "Reconciler decisions, by source and outcome (submitted, deferred, ...).",
    labelnames=("source", "outcome"),
    registry=registry,
)

job_depth = Gauge(
    "printstash_job_depth",
    "Persisted Jobs by state.",
    labelnames=("state",),
    registry=registry,
)

staging_bytes = Gauge(
    "printstash_staging_bytes",
    "Bytes protected by active durable staging leases.",
    registry=registry,
)

storage_delete_intents = Gauge(
    "printstash_storage_delete_intents",
    "Durable storage delete intents by state.",
    labelnames=("state",),
    registry=registry,
)

printer_status = Gauge(
    "printstash_printer_status",
    "Number of configured printers by provider and coarse status.",
    labelnames=("provider", "status"),
    registry=registry,
)

app_info = Info(
    "printstash_app",
    "Static PrintStash build information.",
    registry=registry,
)

fleet_jobs = Gauge(
    "printstash_fleet_jobs",
    "Current normalized fleet jobs by state.",
    labelnames=("state",),
    registry=registry,
)

fleet_blocked_jobs = Gauge(
    "printstash_fleet_blocked_jobs",
    "Queued fleet jobs currently blocked from dispatch.",
    registry=registry,
)

fleet_scheduler_running = Gauge(
    "printstash_fleet_scheduler_running",
    "Whether the local fleet scheduler loop is running.",
    registry=registry,
)

fleet_scheduler_last_tick = Gauge(
    "printstash_fleet_scheduler_last_tick_timestamp_seconds",
    "Unix timestamp of the latest fleet scheduler tick.",
    registry=registry,
)

fleet_dispatches = Counter(
    "printstash_fleet_dispatches_total",
    "Fleet dispatch attempts by terminal dispatcher outcome.",
    labelnames=("outcome",),
    registry=registry,
)

capture_operations = Counter(
    "printstash_capture_operations_total",
    "Capture operations by bounded provider/transport/outcome.",
    labelnames=("provider", "transport", "outcome"),
    registry=registry,
)
capture_operation_duration = Histogram(
    "printstash_capture_operation_duration_seconds",
    "Capture operation duration.",
    labelnames=("provider", "transport", "outcome"),
    registry=registry,
)
capture_uploaded_bytes = Counter(
    "printstash_capture_uploaded_bytes_total",
    "Validated capture upload bytes.",
    labelnames=("provider",),
    registry=registry,
)
capture_contract_errors = Counter(
    "printstash_capture_contract_errors_total",
    "Capture contract failures.",
    labelnames=("provider", "category"),
    registry=registry,
)

artifact_upload_sessions = Counter(
    "printstash_artifact_upload_sessions_total",
    "Artifact upload session events by bounded mode and outcome.",
    labelnames=("event", "mode"),
    registry=registry,
)
artifact_upload_bytes = Counter(
    "printstash_artifact_upload_bytes_total",
    "Artifact upload bytes accepted by transfer path.",
    labelnames=("mode", "path"),
    registry=registry,
)

_ARTIFACT_UPLOAD_EVENTS = frozenset(
    {
        "created",
        "resumed",
        "completed",
        "failed",
        "expired",
        "aborted",
        "verification_failed",
    }
)
_ARTIFACT_UPLOAD_MODES = frozenset({"api_chunks", "native_parts", "simple"})


def record_artifact_upload_event(event: str, mode: str) -> None:
    """Record only bounded upload labels; metrics never alter the operation."""

    try:
        normalized_event = event if event in _ARTIFACT_UPLOAD_EVENTS else "failed"
        normalized_mode = mode if mode in _ARTIFACT_UPLOAD_MODES else "simple"
        artifact_upload_sessions.labels(normalized_event, normalized_mode).inc()
    except Exception:
        pass


def record_artifact_upload_bytes(mode: str, count: int, *, bypassed_api: bool) -> None:
    try:
        normalized_mode = mode if mode in _ARTIFACT_UPLOAD_MODES else "simple"
        path = "direct" if bypassed_api else "api"
        artifact_upload_bytes.labels(normalized_mode, path).inc(max(0, count))
    except Exception:
        pass


_CAPTURE_PROVIDERS = frozenset(
    {"myminifactory", "cults", "makerworld", "printables", "thingiverse", "unknown"}
)
_CAPTURE_TRANSPORTS = frozenset(
    {"provider_api", "browser_upload", "upload_slots", "unknown"}
)
_CAPTURE_OUTCOMES = frozenset(
    {"success", "error", "rate_limited", "contract_changed", "required"}
)
_CAPTURE_CATEGORIES = frozenset(
    {
        "provider_connection_required",
        "provider_rate_limited",
        "provider_contract_changed",
        "user_file_required",
        "extension_capture_required",
        "unknown",
    }
)


def record_capture_operation(
    provider: str,
    transport: str,
    outcome: str,
    duration_seconds: float,
    *,
    uploaded_bytes: int = 0,
    error_category: str | None = None,
) -> None:
    """Best-effort capture telemetry with fixed label vocabularies only."""
    try:
        provider = provider if provider in _CAPTURE_PROVIDERS else "unknown"
        transport = transport if transport in _CAPTURE_TRANSPORTS else "unknown"
        outcome = outcome if outcome in _CAPTURE_OUTCOMES else "error"
        capture_operations.labels(provider, transport, outcome).inc()
        capture_operation_duration.labels(provider, transport, outcome).observe(
            max(0.0, duration_seconds)
        )
        if uploaded_bytes > 0:
            capture_uploaded_bytes.labels(provider).inc(uploaded_bytes)
        if error_category is not None:
            capture_contract_errors.labels(
                provider,
                error_category if error_category in _CAPTURE_CATEGORIES else "unknown",
            ).inc()
    except Exception:
        pass


def observe_request(
    method: str, path: str, status: int, duration_seconds: float
) -> None:
    """Record one completed HTTP request. Best-effort: never raises to callers."""
    try:
        http_request_duration.labels(
            method=method, path=path, status=str(status)
        ).observe(duration_seconds)
    except Exception:  # noqa: BLE001 — metrics must never break a request
        pass


def record_job_terminal(kind: str, result: str, duration_seconds: float) -> None:
    """Record one terminal Job using only bounded, non-sensitive labels."""
    try:
        jobs_terminal.labels(kind=kind, result=result).inc()
        job_duration.labels(kind=kind, result=result).observe(
            max(0.0, duration_seconds)
        )
    except Exception:  # noqa: BLE001 — metrics must never break a job
        pass


def set_stuck_jobs(count: int) -> None:
    try:
        stuck_jobs.set(max(0, count))
    except Exception:  # noqa: BLE001 — metrics must never break a request
        pass


def set_lane_depth(lane: str, *, queued: int, running: int) -> None:
    try:
        lane_depth.labels(lane=lane, state="queued").set(max(0, queued))
        lane_depth.labels(lane=lane, state="running").set(max(0, running))
    except Exception:  # noqa: BLE001 — metrics must never break a request
        pass


def record_step(kind: str, step: str, result: str, duration_seconds: float) -> None:
    try:
        step_duration.labels(kind=kind, step=step, result=result).observe(
            max(0.0, duration_seconds)
        )
    except Exception:  # noqa: BLE001 — metrics must never break a job
        pass


def record_resubmit(kind: str) -> None:
    try:
        job_resubmits.labels(kind=kind).inc()
    except Exception:  # noqa: BLE001 — metrics must never break a job
        pass


def record_reconcile_pass(
    source: str, duration_seconds: float, outcomes: dict[str, int]
) -> None:
    try:
        reconcile_pass_duration.labels(source=source).observe(
            max(0.0, duration_seconds)
        )
        for outcome, count in outcomes.items():
            if count:
                reconcile_outcomes.labels(source=source, outcome=outcome).inc(count)
    except Exception:  # noqa: BLE001 — metrics must never break a pass
        pass


def record_fleet_dispatch(outcome: str) -> None:
    try:
        fleet_dispatches.labels(outcome=outcome).inc()
    except Exception:  # noqa: BLE001 — metrics must never break dispatch
        pass
