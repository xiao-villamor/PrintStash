"""Upload measurements expose their volume policy independently of fingerprints."""

import io

import pytest
import trimesh
from PIL import Image
from sqlmodel import select

from app.core.config import _overlay
from app.db.models import GeometryFingerprint, Metadata
from tests.e2e._jobs import finished_job
from tests.factories import content
from tests.factories.geometry import three_mf


@pytest.fixture(
    params=[
        (
            0.001,
            (),
            {
                "state": "measured",
                "unit": "mm3",
                "method": "mesh_surface_integral",
                "value_mm3": pytest.approx(1e-9, rel=1e-12, abs=0),
                "cause": None,
            },
        ),
        (
            10.0,
            (0,),
            {
                "state": "unavailable",
                "unit": "mm3",
                "method": "mesh_surface_integral",
                "value_mm3": None,
                "cause": "inconsistent_winding",
            },
        ),
    ],
    ids=["small-measured-volume", "inconsistent-winding"],
)
def source_measurements(request):
    edge, reversed_faces, expected = request.param
    mesh = trimesh.creation.box(extents=[edge, edge, edge])
    for index in reversed_faces:
        mesh.faces[index] = mesh.faces[index][::-1]
    raw = three_mf(meshes={1: mesh}, extras={"Metadata/thumbnail.png": content.png()})
    return raw, edge, expected


class TestMeshMeasurementEvidence:
    @pytest.mark.asyncio
    async def test_ingestion_publishes_volume_evidence_without_fingerprints(
        self, api, superuser_headers, e2e_db, source_measurements
    ):
        raw, edge, expected = source_measurements
        headers = superuser_headers
        disabled = await api.patch(
            "/api/v1/similarity/settings",
            headers=headers,
            json={"enabled": False, "fingerprint_on_ingest": False},
        )
        assert disabled.status_code == 200, disabled.text
        upload = await api.post(
            "/api/v1/ingest/model",
            headers=headers,
            files={"file": ("measurement.3mf", raw, "model/3mf")},
        )
        job = await finished_job(api, upload, headers)
        assert job["state"] == "completed", job
        response = await api.get(f"/api/v1/models/{job['model_id']}", headers=headers)
        assert response.status_code == 200, response.text
        artifact = next(
            row for row in response.json()["files"] if row["id"] == job["file_id"]
        )
        metadata = artifact["metadata"]
        assert metadata["volume_measurement"] == expected
        assert metadata["volume_mm3"] == expected["value_mm3"]
        assert tuple(
            metadata[field] for field in ("bbox_x_mm", "bbox_y_mm", "bbox_z_mm")
        ) == pytest.approx((edge, edge, edge), rel=1e-12, abs=0)
        assert metadata["triangle_count"] == 12
        e2e_db.expire_all()
        row = e2e_db.exec(
            select(Metadata).where(Metadata.file_id == job["file_id"])
        ).one()
        assert row.volume_mm3 == expected["value_mm3"]
        assert row.volume_state.value == expected["state"]
        assert (
            e2e_db.exec(
                select(GeometryFingerprint).where(
                    GeometryFingerprint.file_id == job["file_id"]
                )
            ).all()
            == []
        )
        listing = await api.get(
            f"/api/v1/files/{job['file_id']}/derivatives", headers=headers
        )
        assert listing.status_code == 200, listing.text
        derived = next(
            output for output in listing.json() if output["kind"] == "metadata"
        )
        from app.modules.derivatives.kinds import MESH_GEOMETRY_RECIPE

        assert (derived["state"], derived["recipe_version"]) == (
            "ready",
            MESH_GEOMETRY_RECIPE,
        )


class TestRetainedSceneMeasurementEvidence:
    @pytest.mark.asyncio
    async def test_ingestion_measures_instances_beyond_materialization_budget(
        self, api, superuser_headers, e2e_db, monkeypatch
    ):
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 100)
        raw = three_mf(
            build=((1, None),) * 64, extras={"Metadata/thumbnail.png": content.png()}
        )
        disabled = await api.patch(
            "/api/v1/similarity/settings",
            headers=superuser_headers,
            json={"enabled": False, "fingerprint_on_ingest": False},
        )
        assert disabled.status_code == 200, disabled.text
        upload = await api.post(
            "/api/v1/ingest/model",
            headers=superuser_headers,
            files={"file": ("retained-instances.3mf", raw, "model/3mf")},
        )
        job = await finished_job(api, upload, superuser_headers)
        assert job["state"] == "completed", job
        response = await api.get(
            f"/api/v1/models/{job['model_id']}", headers=superuser_headers
        )
        assert response.status_code == 200, response.text
        artifact = next(
            row for row in response.json()["files"] if row["id"] == job["file_id"]
        )
        metadata = artifact["metadata"]
        assert metadata["triangle_count"] == 256
        assert tuple(metadata[f"bbox_{axis}_mm"] for axis in "xyz") == (10, 20, 30)
        assert metadata["volume_measurement"] == {
            "state": "measured",
            "unit": "mm3",
            "method": "mesh_surface_integral",
            "value_mm3": 64000.0,
            "cause": None,
        }
        preview = await api.get(
            f"/api/v1/files/{job['file_id']}/thumbnail", headers=superuser_headers
        )
        assert preview.status_code == 200, preview.text
        with Image.open(io.BytesIO(preview.content)) as image:
            assert image.convert("RGB").getextrema() != ((255, 255),) * 3
        e2e_db.expire_all()
        assert (
            e2e_db.exec(
                select(GeometryFingerprint).where(
                    GeometryFingerprint.file_id == job["file_id"]
                )
            ).all()
            == []
        )
