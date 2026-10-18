"""Real Jobs publish independently after sharing one controlled render fence."""

from __future__ import annotations

import asyncio
import contextvars
import io
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import trimesh
from PIL import Image
from sqlmodel import select

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
from app.runtime.compute.contracts import Reason
from app.runtime.compute.protocol import Priority
from tests.fixtures.compute_renderer import qualified_renderer as qualified_renderer

# These cases coordinate overlapping real native workers through a broker fence.
# Independent xdist vaults own independent admission pools; use the mandatory
# serial native-host phase for the complete publication/cancellation contract.
pytestmark = pytest.mark.native_host


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


class TestRenderBatchPublication:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "subdivisions", [0, 6], ids=["small-visual", "large-visual"]
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
                assert [mode for _key, _priority, mode in batch] == [
                    "gpu-visual-v2"
                ] * 2
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


class TestThumbnailFallbackPublication:
    @pytest.mark.parametrize(
        "handoff_enabled", [False, True], ids=["combined", "handoff"]
    )
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "failure,color",
        [
            pytest.param(Reason.DEVICE_FAILED, (180, 60, 30, 255), id="device-failure"),
            pytest.param(None, (180, 60, 30, 0), id="empty-frame"),
            pytest.param(None, (180, 60, 30, 255), id="gpu-success"),
        ],
    )
    async def test_preserves_publication_across_gpu_outcomes(
        self,
        api,
        superuser_headers,
        e2e_db,
        work_engine,
        qualified_renderer,
        monkeypatch,
        failure,
        color,
        handoff_enabled,
    ):
        from app.modules.media import mesh_isolation, thumbnail_handoff
        from app.modules.media.native_process import native_capacity
        from app.modules.media.source_preparation import capacity as prepared_capacity
        from app.runtime import native_runtime, preparation_runtime

        if not handoff_enabled:
            monkeypatch.setattr(thumbnail_handoff, "plan", lambda *args: None)
        controls = qualified_renderer
        controls.failure = failure
        controls.colors = [color]
        controls.release_batch.set()
        headers = superuser_headers
        payload = trimesh.creation.box(extents=(10, 20, 30)).export(file_type="3mf")
        work_engine.launch(listen_lanes=[LaneName.INGEST, LaneName.RECONCILE])
        uploaded = await api.post(
            "/api/v1/ingest/model",
            headers=headers,
            files={"file": ("owned.3mf", payload, "model/3mf")},
        )
        assert uploaded.status_code == 202, uploaded.text
        ingest_id = uploaded.json()["job_id"]
        for _ in range(40):
            if jobs.get(ingest_id).state.value == "completed":
                break
            assert work_engine.run_one() is not None
        else:
            raise AssertionError("ingest did not settle")
        file_id = jobs.get(ingest_id).file_id
        run_pass(JobKind.DERIVATIVES_MESH)
        derivative = _derivative_job(file_id)
        work_engine.launch(listen_lanes=[LaneName.DERIVE_NATIVE])
        executions = []
        supervise = mesh_isolation.supervise_result

        def observe(command, **kwargs):
            executions.append(command)
            return supervise(command, **kwargs)

        monkeypatch.setattr(mesh_isolation, "supervise_result", observe)
        for _ in range(20):
            if jobs.get(derivative.id).state.value == "completed":
                break
            outcome = work_engine.run_one()
            assert outcome is not None
            assert outcome.error is None
        else:
            raise AssertionError("derivative did not settle")
        assert len(controls.batches) == 1
        needs_fallback = failure is not None or color[3] == 0
        assert len(executions) == (2 if handoff_enabled and needs_fallback else 1)
        if handoff_enabled and needs_fallback:
            assert "app.modules.media.thumbnail_cpu_worker" in executions[1]
        assert "app.modules.media.mesh_worker" in executions[0]
        rows = (
            await api.get(f"/api/v1/files/{file_id}/derivatives", headers=headers)
        ).json()
        assert (
            next(row for row in rows if row["kind"] == "thumbnail")["state"] == "ready"
        )
        assert (
            next(row for row in rows if row["kind"] == "metadata")["state"] == "ready"
        )
        preview = await api.get(f"/api/v1/files/{file_id}/thumbnail", headers=headers)
        assert preview.status_code == 200
        with Image.open(io.BytesIO(preview.content)) as image:
            assert image.size == (640, 480)
            assert image.getbbox() is not None
        original = await api.get(f"/api/v1/files/{file_id}/download", headers=headers)
        assert original.content == payload
        with get_session_factory().scoped_session() as session:
            meta = session.exec(
                select(Metadata).where(Metadata.file_id == file_id)
            ).one()
            assert (meta.bbox_x_mm, meta.bbox_y_mm, meta.bbox_z_mm) == (10, 20, 30)
            assert meta.volume_mm3 == pytest.approx(6000)
            assert meta.triangle_count == 12
        deadline = time.monotonic() + 5

        def check():
            assert time.monotonic() < deadline, (
                "completed Job retained resource credits"
            )

        native = native_capacity()
        with native_runtime.admit(native, native, checkpoint=check):
            pass
        prepared = prepared_capacity()
        with preparation_runtime.reserve(prepared, prepared, checkpoint=check):
            pass
