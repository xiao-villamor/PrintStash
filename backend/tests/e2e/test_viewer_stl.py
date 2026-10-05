"""Concurrent viewer misses share durable conversion without blocking originals.

The public demand route persists one logical Job; its worker publication is
shared by every waiter. Original Artifact delivery remains independent.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
from contextlib import contextmanager
from threading import Event

import httpx
import numpy as np
import pytest
import trimesh
from sqlalchemy import text
from sqlmodel import select

from app.db.models import (
    ArtifactDerivative,
    DerivativeKind,
    DerivativeState,
    File,
    Job,
    JobKind,
    OwnedStorageObject,
    StorageObjectState,
)
from app.modules.media import mesh_isolation
from app.modules.storage.storage_backend.runtime import get_backend
from tests.e2e._jobs import settle
from tests.factories import build_derivative
from tests.factories.geometry import tetrahedron, three_mf


class TestViewerStlPreparation:
    @pytest.mark.asyncio
    async def test_shares_twenty_viewer_misses(self, api, superuser_headers, e2e_db):
        headers = superuser_headers
        with e2e_db.get_bind().connect() as connection:
            connection.execute(text("PRAGMA journal_mode=WAL"))
        original = three_mf()
        upload = await api.post(
            "/api/v1/ingest/model",
            headers=headers,
            files={"file": ("shared.3mf", original, "model/3mf")},
        )
        assert upload.status_code == 202, upload.text
        settle()
        ingestion = await api.get(
            f"/api/v1/jobs/{upload.json()['job_id']}", headers=headers
        )
        assert ingestion.json()["state"] == "completed", ingestion.text
        file_id = ingestion.json()["file_id"]

        requests = asyncio.gather(
            *(
                api.get(f"/api/v1/files/{file_id}/stl", headers=headers)
                for _ in range(20)
            ),
            return_exceptions=True,
        )
        heartbeat = await asyncio.wait_for(api.get("/api/v1/health"), timeout=2)
        misses = await requests
        settle()
        preview = await api.get(f"/api/v1/files/{file_id}/stl", headers=headers)
        download = await api.get(f"/api/v1/files/{file_id}/download", headers=headers)

        assert heartbeat.status_code == 200, heartbeat.text
        assert all(isinstance(response, httpx.Response) for response in misses), misses
        assert [response.status_code for response in misses] == [202] * 20
        assert [response.headers["cache-control"] for response in misses] == [
            "private, no-store"
        ] * 20
        assert preview.status_code == 200, preview.text
        assert download.status_code == 200, download.text
        assert download.content == original
        e2e_db.expire_all()
        conversions = e2e_db.exec(
            select(Job).where(Job.kind == JobKind.DERIVATIVES_VIEWER_STL)
        ).all()
        assert len(conversions) == 1
        assert conversions[0].attempts == 1
        derivative = e2e_db.exec(
            select(ArtifactDerivative).where(
                ArtifactDerivative.file_id == file_id,
                ArtifactDerivative.kind == DerivativeKind.VIEWER_STL,
            )
        ).one()
        proof = e2e_db.exec(
            select(OwnedStorageObject).where(
                OwnedStorageObject.key == derivative.storage_key
            )
        ).one()
        assert proof.state is StorageObjectState.COMMITTED
        assert proof.size_bytes == len(preview.content)
        assert proof.sha256 == hashlib.sha256(preview.content).hexdigest()

    @pytest.mark.asyncio
    async def test_preserves_original_download_during_native_conversion(
        self, api, superuser_headers, e2e_db, monkeypatch
    ):
        original = three_mf()
        upload = await api.post(
            "/api/v1/ingest/model",
            headers=superuser_headers,
            files={"file": ("admitted.3mf", original, "model/3mf")},
        )
        assert upload.status_code == 202, upload.text
        settle()
        ingestion = await api.get(
            f"/api/v1/jobs/{upload.json()['job_id']}", headers=superuser_headers
        )
        assert ingestion.json()["state"] == "completed", ingestion.text
        file_id = ingestion.json()["file_id"]
        admission = mesh_isolation.admission
        admitted, release = Event(), Event()

        @contextmanager
        def hold_admitted_conversion(*args, **kwargs):
            with admission(*args, **kwargs) as permit:
                admitted.set()
                assert release.wait(timeout=20), "conversion gate was not released"
                yield permit

        monkeypatch.setattr(mesh_isolation, "admission", hold_admitted_conversion)
        miss = await api.get(f"/api/v1/files/{file_id}/stl", headers=superuser_headers)
        worker = asyncio.create_task(asyncio.to_thread(settle))
        try:
            assert await asyncio.to_thread(admitted.wait, 10), (
                "native admission missing"
            )
            download = await api.get(
                f"/api/v1/files/{file_id}/download", headers=superuser_headers
            )
            pending = await api.get(
                f"/api/v1/files/{file_id}/stl", headers=superuser_headers
            )
            assert miss.status_code == 202, miss.text
            assert pending.status_code == 202, pending.text
            assert pending.headers["cache-control"] == "private, no-store"
            assert download.status_code == 200, download.text
            assert download.content == original
            assert not worker.done()
        finally:
            release.set()
            await asyncio.wait_for(worker, timeout=30)
        preview = await api.get(
            f"/api/v1/files/{file_id}/stl", headers=superuser_headers
        )
        e2e_db.expire_all()
        jobs = e2e_db.exec(
            select(Job).where(Job.kind == JobKind.DERIVATIVES_VIEWER_STL)
        ).all()
        assert preview.status_code == 200, preview.text
        mesh = trimesh.load(io.BytesIO(preview.content), file_type="stl", force="mesh")
        assert len(mesh.faces) == 4
        assert mesh.is_watertight
        assert len(jobs) == 1
        assert jobs[0].attempts == 1

    @pytest.mark.asyncio
    async def test_rebuilds_the_previous_viewer_recipe_on_demand(
        self, api, superuser_headers, e2e_db
    ):
        original = three_mf()
        upload = await api.post(
            "/api/v1/ingest/model",
            headers=superuser_headers,
            files={"file": ("old-recipe.3mf", original, "model/3mf")},
        )
        assert upload.status_code == 202, upload.text
        settle()
        ingestion = await api.get(
            f"/api/v1/jobs/{upload.json()['job_id']}", headers=superuser_headers
        )
        assert ingestion.json()["state"] == "completed", ingestion.text
        file_id = ingestion.json()["file_id"]
        source = e2e_db.get(File, file_id)
        stale_mesh = tetrahedron()
        stale_mesh.apply_translation([100, 0, 0])
        stale_bytes = stale_mesh.export(file_type="stl")
        stale_key = "old-viewer-recipe-2.stl"
        get_backend().write_bytes(stale_bytes, stale_key)
        previous = build_derivative(
            e2e_db,
            source,
            DerivativeKind.VIEWER_STL,
            recipe_version=2,
            state=DerivativeState.READY,
            storage_key=stale_key,
        )

        pending = await api.get(
            f"/api/v1/files/{file_id}/stl", headers=superuser_headers
        )

        assert pending.status_code == 202, pending.text
        assert pending.headers["cache-control"] == "private, no-store"
        e2e_db.refresh(source)
        assert source.viewer_requested_at is not None
        assert source.sha256 == hashlib.sha256(original).hexdigest()
        settle()
        preview = await api.get(
            f"/api/v1/files/{file_id}/stl", headers=superuser_headers
        )
        download = await api.get(
            f"/api/v1/files/{file_id}/download", headers=superuser_headers
        )
        e2e_db.expire_all()
        current = e2e_db.exec(
            select(ArtifactDerivative).where(
                ArtifactDerivative.file_id == file_id,
                ArtifactDerivative.kind == DerivativeKind.VIEWER_STL,
                ArtifactDerivative.recipe_version == 3,
            )
        ).one()
        jobs = e2e_db.exec(
            select(Job).where(Job.kind == JobKind.DERIVATIVES_VIEWER_STL)
        ).all()
        assert preview.status_code == 200, preview.text
        assert preview.content != stale_bytes
        mesh = trimesh.load(io.BytesIO(preview.content), file_type="stl", force="mesh")
        np.testing.assert_array_equal(mesh.bounds, tetrahedron().bounds)
        assert mesh.is_watertight
        assert current.state is DerivativeState.READY
        assert current.storage_key != stale_key
        assert current.attempts == 1
        assert len(jobs) == 1
        assert jobs[0].attempts == 1
        assert previous.recipe_version == 2
        assert previous.state is DerivativeState.READY
        assert get_backend().read_bytes(stale_key) == stale_bytes
        assert download.status_code == 200, download.text
        assert download.content == original
