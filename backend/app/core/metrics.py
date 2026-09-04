"""Prometheus metrics registry and instruments.

A single process-local ``CollectorRegistry`` holds every PrintStash metric so
the ``/metrics`` endpoint can render them in one pass. The app runs
single-process (in-process job registry + ``app.state`` printer hub), so the
default per-process registry semantics are correct as deployed. Running multiple
uvicorn workers would require prometheus multiprocess mode, which is out of
scope here.

Instruments:
- ``http_request_duration`` — request latency histogram, labelled by method,
  matched route template, and status. The route *template* (not the raw path)
  keeps label cardinality bounded.
- ``ingestion_jobs`` — terminal ingestion job counter, labelled by bounded kind/result.
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

ingestion_jobs = Counter(
    "printstash_ingestion_jobs_total",
    "Ingestion jobs that reached a terminal state, by outcome.",
    labelnames=("kind", "result"),
    registry=registry,
)

ingestion_job_duration = Histogram(
    "printstash_ingestion_job_duration_seconds",
    "Wall-clock duration of terminal ingestion jobs.",
    labelnames=("kind", "result"),
    registry=registry,
)

ingestion_stuck_jobs = Gauge(
    "printstash_ingestion_stuck_jobs",
    "Persisted pending/running ingestion jobs whose heartbeat is stale.",
    registry=registry,
)

background_job_depth = Gauge(
    "printstash_background_job_depth",
    "Persisted background jobs by state.",
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


def record_ingestion_terminal(kind: str, result: str, duration_seconds: float) -> None:
    """Record one terminal job using only bounded, non-sensitive labels."""
    try:
        ingestion_jobs.labels(kind=kind, result=result).inc()
        ingestion_job_duration.labels(kind=kind, result=result).observe(
            max(0.0, duration_seconds)
        )
    except Exception:  # noqa: BLE001 — metrics must never break a job
        pass


def set_ingestion_stuck_jobs(count: int) -> None:
    try:
        ingestion_stuck_jobs.set(max(0, count))
    except Exception:  # noqa: BLE001 — metrics must never break a request
        pass


def record_fleet_dispatch(outcome: str) -> None:
    try:
        fleet_dispatches.labels(outcome=outcome).inc()
    except Exception:  # noqa: BLE001 — metrics must never break dispatch
        pass
