"""Ingest real meshes, find geometric evidence and confirm without a Family."""

import hashlib

import pytest
from sqlmodel import select

from app.db.models import File, SimilarityReviewDecision
from app.modules.storage.storage_backend.runtime import get_backend
from app.runtime.similarity import process_one
from tests.factories.geometry import tetrahedron


class TestSimilarity:
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
            assert response.status_code == 202, response.text
            job = await api.get(
                f"/api/v1/ingest/jobs/{response.json()['job_id']}", headers=headers
            )
            assert job.json()["state"] == "completed", job.text
            assert job.json()["fingerprint_status"] == "ready", job.text
            uploaded.append(
                (job.json()["file_id"], hashlib.sha256(content).hexdigest())
            )
        for _ in range(50):
            if not process_one():
                break
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
