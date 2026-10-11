"""Real Jobs publish independently after sharing one controlled render fence."""

from __future__ import annotations

import asyncio
import contextvars
import io
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import trimesh
from PIL import Image
from sqlmodel import select

from app.core.config import _overlay
from app.db.models import (
    ArtifactDerivative,
    DerivativeKind,
    Job,
    JobKind,
    LaneName,
    Metadata,
)
from app.db.session import get_session_factory
from app.modules.derivatives.source import subject_key
from app.modules.work.jobs import jobs
from app.modules.work.reconciler import run_pass
from app.runtime.compute import client
from app.runtime.compute.broker import serve
from app.runtime.compute.protocol import Priority
from tests.fakes.qualified_batch_renderer import QualifiedBatchRenderer


async def _wait_until(predicate, message):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.025)
    raise AssertionError(message)


def _derivative_job(file_id):
    with get_session_factory().scoped_session() as session:
        return session.exec(
            select(Job).where(
                Job.kind == JobKind.DERIVATIVES_MESH,
                Job.subject_key == subject_key(file_id),
            )
        ).one()


@pytest.fixture
def qualified_renderer(tmp_path, monkeypatch):
    # Fake the external optional runtime in parent and fresh parser children.
    # Discovery may find it; importing a real GPU is forbidden by this fixture.
    package = tmp_path / "external-runtime"
    package.mkdir()
    (package / "wgpu.py").write_text(
        'raise AssertionError("the test executor owns GPU execution")\n'
    )
    monkeypatch.syspath_prepend(str(package))
    monkeypatch.setenv(
        "PYTHONPATH", os.pathsep.join((str(package), os.environ.get("PYTHONPATH", "")))
    )
    monkeypatch.setitem(_overlay, "compute_mode", "auto")
    monkeypatch.setitem(_overlay, "compute_render_policy", "qualified")
    monkeypatch.setitem(_overlay, "max_render_jobs", 2)
    client.bind(tmp_path)
    root = client.directory()
    assert root is not None
    controls = QualifiedBatchRenderer()
    context = contextvars.copy_context()
    owner = threading.Thread(
        target=context.run,
        args=(serve, root),
        kwargs={"dispatcher_factory": controls.dispatcher},
    )
    owner.start()
    try:
        deadline = time.monotonic() + 5
        while not (root / "broker.sock").exists():
            assert time.monotonic() < deadline, "test broker did not start"
            time.sleep(0.01)
        yield controls
    finally:
        controls.release_owner.set()
        controls.release_batch.set()
        controls.stop.set()
        owner.join(timeout=5)
        assert not owner.is_alive(), "test broker did not stop"


class TestRenderBatchPublication:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "subdivisions", [0, 6], ids=["canonical", "deferred-normals"]
    )
    async def test_publishes_the_healthy_member_when_a_batched_job_is_cancelled(
        self,
        api,
        superuser_headers,
        e2e_db,
        work_engine,
        qualified_renderer,
        subdivisions,
    ):
        headers, controls = superuser_headers, qualified_renderer
        configured = await api.put(
            "/api/v1/admin/work/lanes/derive.native",
            headers=headers,
            json={"concurrency": 2},
        )
        assert configured.status_code == 200, configured.text
        uploads = []
        extents = ((10, 20, 30), (20, 30, 40))
        payloads = []
        for size in extents:
            mesh = trimesh.creation.box(extents=size)
            for _ in range(subdivisions):
                mesh = mesh.subdivide()
            payloads.append(mesh.export(file_type="stl"))
        expected_faces = 12 * 4**subdivisions
        # Run the shipped ingest/reconciliation definitions, leaving native Jobs
        # queued. Compute urgency is set by the real fresh mesh worker.
        work_engine.launch(listen_lanes=[LaneName.INGEST, LaneName.RECONCILE])
        for index, payload in enumerate(payloads):
            response = await api.post(
                "/api/v1/ingest/model",
                headers=headers,
                files={"file": (f"batch-{index}.stl", payload, "application/sla")},
            )
            assert response.status_code == 202, response.text
            uploads.append(response.json()["job_id"])
        for _ in range(40):
            if all(jobs.get(job_id).state.value == "completed" for job_id in uploads):
                break
            assert work_engine.run_one() is not None
        else:
            raise AssertionError("ingest jobs did not complete")
        files = [jobs.get(job_id).file_id for job_id in uploads]
        dimensions = dict(zip(files, extents, strict=True))
        run_pass(JobKind.DERIVATIVES_MESH)
        derivative_jobs = [_derivative_job(file_id) for file_id in files]
        sources = {
            job.id: (file_id, payload)
            for job, file_id, payload in zip(
                derivative_jobs, files, payloads, strict=True
            )
        }
        work_engine.launch(listen_lanes=[LaneName.DERIVE_NATIVE])

        with ThreadPoolExecutor(max_workers=3) as pool:
            blocker = pool.submit(contextvars.copy_context().run, controls.occupy)
            try:
                await _wait_until(
                    controls.owner_busy.is_set, "hardware did not become busy"
                )
                first = pool.submit(contextvars.copy_context().run, work_engine.run_one)
                await _wait_until(
                    lambda: client.status().queue_depth == 1,
                    "first real worker did not reach the broker",
                )
                cancelled_job = next(
                    job
                    for job in derivative_jobs
                    if jobs.get(job.id).state.value == "running"
                )
                healthy_job = next(
                    job for job in derivative_jobs if job.id != cancelled_job.id
                )
                files, payloads = zip(
                    sources[cancelled_job.id], sources[healthy_job.id], strict=True
                )
                second = pool.submit(
                    contextvars.copy_context().run, work_engine.run_one
                )
                await _wait_until(
                    lambda: client.status().queue_depth == 2,
                    "second real worker did not reach the broker",
                )
                controls.release_owner.set()
                await _wait_until(
                    controls.batch_started.is_set, "ready work did not execute"
                )
                assert len(controls.batches) == 1
                batch = controls.batches[0]
                assert len(batch) == 2
                assert len({key for key, _priority, _mode in batch}) == 2
                assert [priority for _key, priority, _mode in batch] == [
                    Priority.BACKGROUND
                ] * 2
                expected_mode = "gpu-angle-csr-v1" if subdivisions else "canonical"
                assert [mode for _key, _priority, mode in batch] == [expected_mode] * 2
                cancelled = await api.post(
                    f"/api/v1/jobs/{cancelled_job.id}/cancel", headers=headers
                )
                assert cancelled.status_code == 200, cancelled.text
                assert cancelled.json()["state"] == "cancelled"
                controls.release_batch.set()
                await _wait_until(
                    lambda: first.done() and second.done(),
                    "derivative Jobs did not settle",
                )
                assert first.result().error is None
                assert second.result().error is None
                assert blocker.result(timeout=5) == b"released"
            finally:
                controls.release_owner.set()
                controls.release_batch.set()

        final = [
            (await api.get(f"/api/v1/jobs/{job.id}", headers=headers)).json()
            for job in (cancelled_job, healthy_job)
        ]
        assert [job["state"] for job in final] == ["cancelled", "completed"]
        outputs = [
            (
                await api.get(f"/api/v1/files/{file_id}/derivatives", headers=headers)
            ).json()
            for file_id in files
        ]
        thumbnails = [
            next(row for row in rows if row["kind"] == "thumbnail") for rows in outputs
        ]
        assert [row["state"] for row in thumbnails] == ["cancelled", "ready"]
        assert (
            next(row for row in outputs[1] if row["kind"] == "metadata")["state"]
            == "ready"
        )
        published = await api.get(
            f"/api/v1/files/{files[1]}/thumbnail", headers=headers
        )
        assert published.status_code == 200, published.text
        with Image.open(io.BytesIO(published.content)) as image:
            assert image.size == (640, 480)
            actual = image.convert("RGBA").getpixel((320, 240))
            assert all(
                abs(a - b) <= 2 for a, b in zip(actual, controls.colors[1], strict=True)
            )
        for file_id, payload in zip(files, payloads, strict=True):
            original = await api.get(
                f"/api/v1/files/{file_id}/download", headers=headers
            )
            assert original.status_code == 200
            assert original.content == payload
        # Fresh sessions prove late shared completion cannot publish a cancelled
        # thumbnail, while the healthy member's storage pointer is committed.
        with get_session_factory().scoped_session() as session:
            metadata = session.exec(
                select(Metadata).where(Metadata.file_id == files[1])
            ).one()
            assert (
                metadata.bbox_x_mm,
                metadata.bbox_y_mm,
                metadata.bbox_z_mm,
            ) == dimensions[files[1]]
            assert metadata.triangle_count == expected_faces
            x, y, z = dimensions[files[1]]
            assert metadata.volume_mm3 == pytest.approx(x * y * z)
            persisted = {
                row.file_id: row
                for row in session.exec(
                    select(ArtifactDerivative).where(
                        ArtifactDerivative.file_id.in_(files),
                        ArtifactDerivative.kind == DerivativeKind.THUMBNAIL,
                    )
                ).all()
            }
            assert persisted[files[0]].storage_key is None
            assert persisted[files[1]].storage_key is not None
