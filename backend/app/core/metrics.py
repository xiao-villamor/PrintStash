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
- ``ingestion_jobs`` — terminal ingestion job counter, labelled by outcome.
- ``printer_status`` — gauge of live printers by provider/status, set at scrape
  time so it always reflects the current fleet.
- ``app_info`` — static version info.
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


def observe_request(method: str, path: str, status: int, duration_seconds: float) -> None:
    """Record one completed HTTP request. Best-effort: never raises to callers."""
    try:
        http_request_duration.labels(
            method=method, path=path, status=str(status)
        ).observe(duration_seconds)
    except Exception:  # noqa: BLE001 — metrics must never break a request
        pass


def record_ingestion_terminal(state: str) -> None:
    """Increment the terminal ingestion-job counter for ``completed``/``failed``."""
    try:
        ingestion_jobs.labels(state=state).inc()
    except Exception:  # noqa: BLE001 — metrics must never break a job
        pass
