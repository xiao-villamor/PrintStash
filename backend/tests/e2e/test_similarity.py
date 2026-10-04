"""Ingest real meshes, find geometric evidence and confirm it."""

import hashlib

import pytest
from sqlmodel import select

from app.db.models import (
    File,
    GeometryFingerprint,
    Metadata,
    SimilarityReviewDecision,
)
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.work.reconciler import tick
from tests.e2e._jobs import completed_job, settle
from tests.factories.geometry import tetrahedron
from tests.paths import TESTDATA_DIR


def _fingerprint_state(session, file_id: int) -> str:
    session.expire_all()
    return session.exec(
        select(GeometryFingerprint.state).where(
            GeometryFingerprint.file_id == file_id,
            GeometryFingerprint.component_index == 0,
        )
    ).one()


class TestSimilarity:
    @pytest.mark.asyncio
    async def test_ingests_complete_repository_benchy(
        self, api, superuser_headers, e2e_db
    ):
        source = (TESTDATA_DIR / "benchy" / "3dbenchy.stl").read_bytes()
        configured = await api.patch(
            "/api/v1/similarity/settings",
            headers=superuser_headers,
            json={"enabled": True},
        )
        assert configured.status_code == 200, configured.text

        response = await api.post(
            "/api/v1/ingest/model",
            headers=superuser_headers,
            files={"file": ("3dbenchy.stl", source, "application/octet-stream")},
            data={"model_name": "Complete repository Benchy"},
        )

        job = await completed_job(api, response, superuser_headers)
        # Geometry, thumbnail and fingerprint all come from the one mesh load of
        # the derivative that the commit nudged.
        assert _fingerprint_state(e2e_db, job["file_id"]) == "ready"
        file = e2e_db.get(File, job["file_id"])
        metadata = e2e_db.exec(
            select(Metadata).where(Metadata.file_id == file.id)
        ).one()
        assert metadata.triangle_count == 225706
        assert get_backend().read_bytes(file.path) == source
        assert file.thumbnail_path is not None
        assert get_backend().read_bytes(file.thumbnail_path)

    @pytest.mark.asyncio
    async def test_confirms_evidence_after_local_scan(
        self, api, superuser_headers, e2e_db
    ):
        headers = superuser_headers
        configured = await api.patch(
            "/api/v1/similarity/settings",
            headers=headers,
            json={"enabled": True, "sample_points": 256},
        )
        assert configured.status_code == 200, configured.text
        uploaded = []
        model_ids = []
        for index in range(2):
            mesh = tetrahedron()
            mesh.apply_translation([index * 30, 0, 0])
            content = mesh.export(file_type="stl")
            response = await api.post(
                "/api/v1/ingest/model",
                headers=headers,
                files={
                    "file": (f"part-{index}.stl", content, "application/octet-stream")
                },
                data={"model_name": f"Part {index}"},
            )
            # Settling completes the queued upload and mesh work.
            job = await completed_job(api, response, headers)
            assert _fingerprint_state(e2e_db, job["file_id"]) == "ready"
            uploaded.append((job["file_id"], hashlib.sha256(content).hexdigest()))
            model_ids.append(job["model_id"])

        listed = await api.get("/api/v1/similarity/runs", headers=headers)
        assert listed.status_code == 200, listed.text
        pending_runs = listed.json()["items"]
        assert len(pending_runs) == 2, pending_runs
        assert all(
            run["trigger"] == "ingest"
            and run["scope"] == "models"
            and run["state"] == "queued"
            for run in pending_runs
        ), pending_runs
        assert {tuple(run["scope_ids"]) for run in pending_runs} == {
            (model_id,) for model_id in model_ids
        }

        # The inline fixture has no periodic scheduler. Exercise the real tick
        # to discover the committed analysis intent, then follow its public state.
        tick()
        settle()
        for run in pending_runs:
            finished = await api.get(
                f"/api/v1/similarity/runs/{run['id']}", headers=headers
            )
            assert finished.status_code == 200, finished.text
            assert finished.json()["state"] == "completed", finished.json()
            assert finished.json()["scope_ids"] == run["scope_ids"]

        response = await api.get("/api/v1/similarity/candidates", headers=headers)
        assert response.status_code == 200, response.text
        rows = response.json()["items"]
        assert len(rows) == 1
        candidate = rows[0]
        assert candidate["evidence_class"] == "identical_geometry"
        assert candidate["confidence"] == 1.0
        request = {
            "action": "confirm_evidence",
            "version": candidate["version"],
            "request_id": "confirm-first",
        }
        confirmed = await api.post(
            f"/api/v1/similarity/candidates/{candidate['id']}/decision",
            headers=headers,
            json=request,
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["resolution_kind"] == "evidence_only"
        replayed = await api.post(
            f"/api/v1/similarity/candidates/{candidate['id']}/decision",
            headers=headers,
            json=request,
        )
        assert replayed.json()["decision_id"] == confirmed.json()["decision_id"]
        assert len(e2e_db.exec(select(SimilarityReviewDecision)).all()) == 1
        for file_id, expected in uploaded:
            file = e2e_db.get(File, file_id)
            assert (
                hashlib.sha256(get_backend().read_bytes(file.path)).hexdigest()
                == expected
            )
