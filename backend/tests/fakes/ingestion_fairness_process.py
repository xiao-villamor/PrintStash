"""Real DBOS/native work under a bounded stream of public mesh uploads."""

from __future__ import annotations

import faulthandler
import fcntl
import hashlib
import json
import os
import time
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import select

from app.core.config import ensure_dirs, settings
from app.core.time import utcnow
from app.db.models import (
    ArtifactDerivative,
    DerivativeKind,
    DerivativeState,
    FileType,
    Job,
    JobKind,
    JobState,
    WorkPriority,
)
from app.db.session import get_session_factory
from app.modules.media.native_process import native_capacity
from app.modules.work import service
from app.runtime import native_runtime
from tests.factories import build_file, build_model, content
from tests.factories.geometry import three_mf
from tests.fakes.job_engine_process import _diagnostics, _emit, _set_up

_DEADLINE_S = 110
_MAX_UPLOADS = 80
_WINDOW = 6


def _native_snapshot(directory: Path) -> list[dict]:
    """Read one coherent physical budget snapshot, excluding closed receipts."""
    with (directory / "coordinator").open("r+b", buffering=0) as coordinator:
        fcntl.flock(coordinator.fileno(), fcntl.LOCK_EX)
        rows = []
        for path in directory.glob("*.ticket"):
            try:
                descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
            except FileNotFoundError:
                continue
            try:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    rows.append(json.loads(os.pread(descriptor, 4096, 0)))
            finally:
                os.close(descriptor)
        return rows


def main() -> None:
    from app.main import app

    ensure_dirs()
    # Queue pressure belongs to this fixture, not to the operator defaults.
    # Every accepted foreground upload fits the declared six-item window.
    assert settings.staging_max_active_per_user == _WINDOW
    assert settings.staging_max_pending == _WINDOW
    capacity = native_capacity()
    assert capacity.slots >= 2 and capacity.bytes >= 1024**3, capacity
    native_directory = settings.data_root / "runtime" / "native"
    source_root = settings.staging_dir / "fairness-sources"
    source_root.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + _DEADLINE_S
    uploads: list[str] = []
    old_ids: list[int] = []
    observations: list[dict] = []
    completed_at: dict[int, float] = {}
    foreground_pending_at_completion: dict[int, int] = {}
    queued_large = False
    large_active = False
    initial_foreground_pending = 0
    began = time.monotonic()

    def checkpoint() -> None:
        assert time.monotonic() < deadline, _diagnostics()

    def upload(client: TestClient) -> None:
        assert len(uploads) < _MAX_UPLOADS, (
            "bounded interactive arrival budget exhausted"
        )
        index = len(uploads)
        payload = content.binary_stl(triangles=12, offset=(float(index + 1), 0, 0))
        response = client.post(
            "/api/v1/ingest/model",
            files={"file": (f"interactive-{index}.stl", payload, "application/sla")},
        )
        assert response.status_code == 202, response.text
        uploads.append(response.json()["job_id"])

    def observe() -> tuple[int, int]:
        nonlocal queued_large, large_active
        tickets = _native_snapshot(native_directory)
        active = [row for row in tickets if row["state"] == "active"]
        slots = sum(row["request"][0] for row in active)
        amount = sum(row["request"][1] for row in active)
        assert slots <= capacity.slots and amount <= capacity.bytes, tickets
        for row in tickets:
            if row["priority"] == "backfill" and row["request"][1] == capacity.bytes:
                queued_large |= row["state"] == "queued"
                large_active |= row["state"] == "active"
        with get_session_factory().scoped_session() as session:
            jobs = session.exec(
                select(Job).where(Job.kind == JobKind.DERIVATIVES_MESH).limit(100)
            ).all()
            artifacts = session.exec(
                select(ArtifactDerivative)
                .where(
                    ArtifactDerivative.kind.in_(
                        (DerivativeKind.METADATA, DerivativeKind.THUMBNAIL)
                    )
                )
                .limit(200)
            ).all()
            ingest_jobs = session.exec(
                select(Job)
                .where(
                    Job.kind.in_(
                        (JobKind.INGESTION_UPLOAD, JobKind.INGESTION_ARTIFACT_UPLOAD)
                    )
                )
                .limit(100)
            ).all()
        counts: dict[int, int] = {}
        for row in artifacts:
            if row.state is DerivativeState.READY:
                counts[row.file_id] = counts.get(row.file_id, 0) + 1
        ready = {identifier for identifier, count in counts.items() if count == 2}
        ready_foreground = len(ready - set(old_ids))
        pending_foreground = len(uploads) - ready_foreground
        active_backfill = sum(
            job.priority is WorkPriority.BACKFILL
            and job.state in (JobState.QUEUED, JobState.RUNNING, JobState.INTERRUPTED)
            for job in jobs
        )
        assert active_backfill <= 1, [(job.id, job.priority, job.state) for job in jobs]
        for job in ingest_jobs:
            assert job.state is not JobState.FAILED, job.status_json
        for row in artifacts:
            assert row.state is not DerivativeState.FAILED, (
                row.file_id,
                row.failure_reason,
            )
        for identifier in old_ids:
            if identifier in ready and identifier not in completed_at:
                completed_at[identifier] = time.monotonic() - began
                foreground_pending_at_completion[identifier] = pending_foreground
        observations.append(
            {
                "elapsed": time.monotonic() - began,
                "foreground_ready": ready_foreground,
                "foreground_pending": pending_foreground,
                "backfill_ready": len(completed_at),
                "active_backfill_jobs": active_backfill,
                "native_slots": slots,
                "native_bytes": amount,
            }
        )
        return pending_foreground, ready_foreground

    with TestClient(app) as client:
        _set_up(client)
        # Saturation is only an initial arrangement. All subsequent service is
        # done by production Jobs and their actual native workers.
        with native_runtime.admit(capacity, capacity, checkpoint=checkpoint):
            with get_session_factory().scoped_session() as session:
                for index in range(2):
                    payload = three_mf()
                    source = source_root / f"old-{index}.3mf"
                    source.write_bytes(payload)
                    file = build_file(
                        session,
                        build_model(session, name=f"Old fairness {index}"),
                        file_type=FileType.THREE_MF,
                        external=True,
                        path=str(source),
                        size_bytes=len(payload),
                        sha256=hashlib.sha256(payload).hexdigest(),
                        uploaded_at=utcnow() - timedelta(days=2),
                    )
                    assert file.id is not None
                    old_ids.append(file.id)
            service.nudge(JobKind.DERIVATIVES_MESH)
            while not queued_large:
                checkpoint()
                observe()
                time.sleep(0.025)
            for _ in range(_WINDOW):
                upload(client)
            while not any(
                row["state"] == "queued" and row["priority"] == "interactive"
                for row in _native_snapshot(native_directory)
            ):
                checkpoint()
                observe()
                time.sleep(0.025)
            initial_foreground_pending, _ready = observe()
            assert initial_foreground_pending == _WINDOW, observations[-1]
        began = time.monotonic()
        while len(completed_at) != len(old_ids):
            checkpoint()
            pending, _ready = observe()
            while pending < _WINDOW:
                upload(client)
                pending += 1
            time.sleep(0.1)
        # No new arrivals after both backfills complete. Finish every accepted
        # upload to prove that fairness did not merely strand foreground intent.
        submitted_during_contention = len(uploads)
        while True:
            checkpoint()
            pending, foreground_ready = observe()
            if pending == 0:
                break
            time.sleep(0.1)
        for file_id in old_ids:
            thumbnail = client.get(f"/api/v1/files/{file_id}/thumbnail")
            assert thumbnail.status_code == 200, thumbnail.text
            assert thumbnail.content.startswith((b"\x89PNG", b"RIFF"))
        contention = [row for row in observations if row["foreground_pending"] > 0]
        _emit(
            arrival_window=_WINDOW,
            initial_foreground_pending=initial_foreground_pending,
            max_foreground_pending=max(
                row["foreground_pending"] for row in observations
            ),
            staging_active_per_user_limit=settings.staging_max_active_per_user,
            staging_pending_limit=settings.staging_max_pending,
            capacity_slots=capacity.slots,
            capacity_bytes=capacity.bytes,
            submitted=submitted_during_contention,
            foreground_ready=foreground_ready,
            backfill_ready=len(completed_at),
            backfill_completion_seconds=list(completed_at.values()),
            foreground_pending_at_backfill_completion=list(
                foreground_pending_at_completion.values()
            ),
            max_active_backfill_jobs=max(
                row["active_backfill_jobs"] for row in observations
            ),
            max_native_slots=max(row["native_slots"] for row in observations),
            max_native_bytes=max(row["native_bytes"] for row in observations),
            queued_full_capacity_backfill=queued_large,
            admitted_full_capacity_backfill=large_active,
            foreground_progress_during_contention=max(
                row["foreground_ready"] for row in contention
            ),
            contention_samples=len(contention),
            elapsed_seconds=time.monotonic() - began,
        )


if __name__ == "__main__":
    faulthandler.dump_traceback_later(125, exit=True)
    main()
