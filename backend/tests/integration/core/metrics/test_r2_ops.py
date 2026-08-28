"""R2 operations hardening: richer health, scan restart cleanup, metrics."""

from __future__ import annotations

import json
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import (
    ExternalLibrary,
    ExternalLibraryScanStatus,
    FileType,
)
from app.services import external_library
from app.services.jobs import JobRegistry
from tests.factories import build_file, build_model, build_print_job

# --- Item 1: richer /health output --------------------------------------------


# The two health-endpoint tests that used to live here (component presence, and the
# external-library status counts) moved to tests/integration/api/v1/test_health.py, the
# mirror of the module they defend.


# --- Item 2: background-scan restart cleanup ----------------------------------


# --- Item 3: Prometheus metrics -----------------------------------------------


class TestMetricsEndpoint:
    def test_metrics_endpoint_exposes_prometheus_text(self, client: TestClient) -> None:
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]
        assert "printstash_app_info" in resp.text
        assert "printstash_http_request_duration_seconds" in resp.text

    def test_metrics_counts_terminal_ingestion_jobs(self, client: TestClient) -> None:
        reg = JobRegistry()
        job_id = reg.create()
        reg.update(job_id, state="completed")

        body = client.get("/metrics").text
        assert (
            'printstash_ingestion_jobs_total{kind="ingest",result="complete"}' in body
        )
        assert "printstash_ingestion_job_duration_seconds" in body
        assert "printstash_ingestion_stuck_jobs" in body

    def test_metrics_exposes_the_fleet_scheduler_state(
        self,
        client: TestClient,
        db_session: Session,
    ) -> None:
        model = build_model(db_session, name="Blocked", slug="blocked", hash="c" * 64)
        artifact = build_file(
            db_session,
            model,
            path="metrics/blocked.gcode",
            filename="blocked.gcode",
            file_type=FileType.GCODE,
            version=1,
            size_bytes=1,
            sha256="d" * 64,
        )
        build_print_job(
            db_session,
            artifact,
            remote_filename="blocked.gcode",
            blocked_reason="no_eligible_printer",
        )

        body = client.get("/metrics").text

        assert 'printstash_fleet_jobs{state="queued"} 1.0' in body
        assert "printstash_fleet_scheduler_last_tick_timestamp_seconds" in body

    def test_metrics_token_enforced_when_set(self, client: TestClient) -> None:
        _overlay["metrics_token"] = "s3cr3t"
        try:
            assert client.get("/metrics").status_code == 401
            assert (
                client.get(
                    "/metrics", headers={"Authorization": "Bearer wrong"}
                ).status_code
                == 401
            )
            assert (
                client.get(
                    "/metrics", headers={"Authorization": "Bearer s3cr3t"}
                ).status_code
                == 200
            )
        finally:
            _overlay.pop("metrics_token", None)


class TestResetOrphanedScans:
    def test_reset_orphaned_scans_recovers_stranded_library(
        self, db_session: Session
    ) -> None:
        lib = ExternalLibrary(
            name="nas",
            root_path="/mnt/nas",
            enabled=True,
            scan_schedule="* * * * *",
            last_scanned_at=None,
            last_scan_status=ExternalLibraryScanStatus.RUNNING,
        )
        db_session.add(lib)
        db_session.commit()
        db_session.refresh(lib)

        # While stranded RUNNING, the scheduler skips it.
        assert lib.id not in external_library.libraries_due_for_scan(db_session)

        reset = external_library.reset_orphaned_scans(db_session)
        assert reset == 1

        db_session.refresh(lib)
        assert lib.last_scan_status == ExternalLibraryScanStatus.ERROR
        assert json.loads(lib.last_scan_summary)["error"] == "interrupted by restart"

        # Loop-breaker (issue #24): the reset stamps last_scanned_at, so a scan that
        # crashed the process is NOT immediately due again on the next tick — it
        # waits for the schedule instead of crash-looping the container.
        assert lib.last_scanned_at is not None
        assert lib.id not in external_library.libraries_due_for_scan(db_session)

        # Once the schedule has elapsed, it becomes eligible again as normal.
        lib.last_scanned_at = utcnow() - timedelta(minutes=2)
        db_session.add(lib)
        db_session.commit()
        assert lib.id in external_library.libraries_due_for_scan(db_session)

    def test_reset_orphaned_scans_noop_without_running(
        self, db_session: Session
    ) -> None:
        db_session.add(
            ExternalLibrary(
                name="nas",
                root_path="/mnt/nas",
                last_scan_status=ExternalLibraryScanStatus.OK,
            )
        )
        db_session.commit()
        assert external_library.reset_orphaned_scans(db_session) == 0
