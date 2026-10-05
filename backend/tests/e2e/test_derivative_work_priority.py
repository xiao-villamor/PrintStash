"""A new upload queues beside backfill, then publishes through real native work."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest
from sqlmodel import select

from app.bootstrap.native_resources import configure
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
from app.db.session import get_session_factory, override_session_factory
from app.modules.derivatives.source import subject_key
from app.modules.media.native_process import native_capacity
from app.modules.work.jobs import jobs
from app.modules.work.reconciler import run_pass
from app.runtime import native_runtime
from tests.factories import build_file, build_job, build_model, content


def _wait_for_native_queue(directory: Path) -> None:
    deadline = time.monotonic() + 10
    pause = threading.Event()
    while time.monotonic() < deadline:
        for path in directory.glob("*.ticket"):
            try:
                receipt = json.loads(path.read_bytes())
            except FileNotFoundError, json.JSONDecodeError:
                # The allocator can replace/remove a receipt between reads or
                # be partway through its bounded write. Retry the observation.
                continue
            if receipt["state"] == "queued" and receipt["priority"] == "interactive":
                return
        pause.wait(0.025)
    raise AssertionError("real mesh work never queued for native capacity")


def _drive_until(engine, predicate):
    for _ in range(40):
        if predicate():
            return
        assert engine.run_one() is not None, "work did not reach the expected outcome"
    raise AssertionError("bounded work dispatch did not reach the expected outcome")


def _completed(job_id):
    row = jobs.get(job_id)
    assert row is not None
    return row.state is JobState.COMPLETED


def _mesh_job(file_id):
    with get_session_factory().scoped_session() as session:
        return session.exec(
            select(Job).where(
                Job.kind == JobKind.DERIVATIVES_MESH,
                Job.subject_key == subject_key(file_id),
            )
        ).one()


def _outputs(file_id):
    with get_session_factory().scoped_session() as session:
        return session.exec(
            select(ArtifactDerivative).where(
                ArtifactDerivative.file_id == file_id,
                ArtifactDerivative.kind.in_(
                    (DerivativeKind.METADATA, DerivativeKind.THUMBNAIL)
                ),
            )
        ).all()


def _ready(file_id):
    outputs = _outputs(file_id)
    return len(outputs) == 2 and all(
        row.state is DerivativeState.READY for row in outputs
    )


async def _upload_and_ingest(api, headers, engine):
    upload = await api.post(
        "/api/v1/ingest/model",
        headers=headers,
        files={
            "file": (
                "interactive.stl",
                content.binary_stl(triangles=2),
                "application/sla",
            )
        },
    )
    assert upload.status_code == 202, upload.text
    job_id = upload.json()["job_id"]
    _drive_until(engine, lambda: _completed(job_id))
    status = await api.get(f"/api/v1/jobs/{job_id}", headers=headers)
    assert status.status_code == 200, status.text
    assert status.json()["state"] == "completed"
    return status.json()["file_id"]


@pytest.fixture
def old_backlog(e2e_db, tmp_path):
    artifacts = []
    for index in range(3):
        payload = content.binary_stl(triangles=3 + index)
        path = tmp_path / f"old-{index}.stl"
        path.write_bytes(payload)
        artifacts.append(
            build_file(
                e2e_db,
                build_model(e2e_db, name=f"Old model {index}"),
                file_type=FileType.STL,
                external=True,
                path=str(path),
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
                uploaded_at=utcnow() - timedelta(days=2),
            )
        )
    job = build_job(
        e2e_db,
        kind=JobKind.DERIVATIVES_MESH,
        subject=subject_key(artifacts[0].id),
        priority=WorkPriority.BACKFILL,
    )
    return artifacts, job.id


class TestDerivativeWorkPriority:
    @pytest.mark.asyncio
    async def test_upload_enters_the_queue_before_backfill_drains(
        self,
        api,
        superuser_headers,
        e2e_db,
        old_backlog,
        work_engine,
        tmp_path,
    ):
        artifacts, backfill_id = old_backlog
        configure(tmp_path)
        file_id = await _upload_and_ingest(api, superuser_headers, work_engine)
        run_pass(JobKind.DERIVATIVES_MESH)
        interactive = _mesh_job(file_id)
        backfill = jobs.get(backfill_id)
        assert interactive.state is JobState.QUEUED
        assert interactive.priority is WorkPriority.INTERACTIVE
        assert backfill is not None and backfill.state is JobState.QUEUED
        with get_session_factory().scoped_session() as session:
            queued = session.exec(
                select(Job).where(
                    Job.kind == JobKind.DERIVATIVES_MESH, Job.state == JobState.QUEUED
                )
            ).all()
            assert sum(row.priority is WorkPriority.BACKFILL for row in queued) == 1
        _drive_until(work_engine, lambda: _ready(file_id))
        assert not _outputs(artifacts[1].id)
        assert not _outputs(artifacts[2].id)
        thumbnail = await api.get(
            f"/api/v1/files/{file_id}/thumbnail", headers=superuser_headers
        )
        assert thumbnail.status_code == 200, thumbnail.text
        assert thumbnail.headers["content-type"].startswith("image/")
        assert thumbnail.content

    @pytest.mark.asyncio
    async def test_capacity_wait_preserves_the_attempt(
        self,
        api,
        superuser_headers,
        e2e_db,
        work_engine,
        tmp_path,
    ):
        configure(tmp_path)
        file_id = await _upload_and_ingest(api, superuser_headers, work_engine)
        run_pass(JobKind.DERIVATIVES_MESH)
        factory = get_session_factory()

        def execute():
            override_session_factory(factory)
            _drive_until(work_engine, lambda: _ready(file_id))

        capacity = native_capacity()
        with ThreadPoolExecutor(1) as executor:
            with native_runtime.admit(capacity, capacity, checkpoint=lambda: None):
                future = executor.submit(execute)
                _wait_for_native_queue(tmp_path / "runtime" / "native")
                waiting = _mesh_job(file_id)
                outputs = _outputs(file_id)
                assert waiting.state is JobState.RUNNING
                assert waiting.attempts == 1
                assert json.loads(waiting.status_json).get("error") is None
                assert len(outputs) == 2
                assert {row.state for row in outputs} == {DerivativeState.RUNNING}
                assert {row.attempts for row in outputs} == {1}
                assert all(row.failure_reason is None for row in outputs)
                assert not future.done()
            future.result(timeout=30)
        completed = _mesh_job(file_id)
        assert completed.state is JobState.COMPLETED
        assert completed.attempts == 1
        assert {row.attempts for row in _outputs(file_id)} == {1}
        assert _ready(file_id)
        thumbnail = await api.get(
            f"/api/v1/files/{file_id}/thumbnail", headers=superuser_headers
        )
        assert thumbnail.status_code == 200, thumbnail.text
        assert thumbnail.content
