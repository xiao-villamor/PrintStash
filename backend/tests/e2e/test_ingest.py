"""E2E: G-code ingestion, end to end through the real pipeline.

Uploads a real OrcaSlicer fixture through the public ingest endpoint, waits for
the background job to finish, and asserts the model was persisted with parsed
slicer metadata. Re-uploading the same bytes must dedup by content hash rather
than create a second model.
"""

from __future__ import annotations

import io
import json
import math
import struct
import zipfile

import numpy as np
import pytest
import trimesh
from PIL import Image
from sqlmodel import select

from app.core.config import _overlay, settings
from app.db.models import ArtifactDerivative
from tests.e2e._jobs import settle
from tests.fixtures.three_mf_projects import build_3d_builder_component_project
from tests.paths import FIXTURES_DIR

FIXTURE = FIXTURES_DIR / "real_orca_ender3_benchy.gcode"


async def _setup_and_login(api, tmp_path) -> dict[str, str]:
    r = await api.post(
        "/api/v1/setup",
        json={
            "username": "owner",
            "password": "Password123",
            "storage_backend": "local",
            "data_dir": str(tmp_path / "files"),
            "thumb_dir": str(tmp_path / "thumbs"),
        },
    )
    assert r.status_code == 201, r.text
    # Storage backend is normally initialised in the app lifespan (not run here).
    from app.modules.storage.storage_backend.runtime import init_backend

    init_backend()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _upload(api, headers, *, model_name: str) -> dict:
    r = await api.post(
        "/api/v1/ingest/orca",
        files={"file": (FIXTURE.name, FIXTURE.read_bytes(), "text/plain")},
        data={"model_name": model_name},
        headers=headers,
    )
    assert r.status_code == 202, r.text
    return r.json()


async def _await_job(api, headers, job_id: str) -> dict:
    settle()
    r = await api.get(f"/api/v1/jobs/{job_id}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _thumbnail_derivative(api, headers, file_id: int) -> dict:
    """The thumbnail's derivative row as the public API reports it."""
    listed = await api.get(f"/api/v1/files/{file_id}/derivatives", headers=headers)
    assert listed.status_code == 200, listed.text
    return next(row for row in listed.json() if row["kind"] == "thumbnail")


def _thumbnail_output(session, file_id: int) -> dict:
    """How the published thumbnail was made: its strategy and completeness."""
    row = session.exec(
        select(ArtifactDerivative).where(
            ArtifactDerivative.file_id == file_id,
            ArtifactDerivative.kind == "thumbnail",
        )
    ).one()
    return json.loads(row.output_json)


def _microfaceted_stl(columns: int = 420, rows: int = 420) -> bytes:
    """Build a connected non-planar surface whose sample is sub-pixel."""
    record = struct.Struct("<12fH")
    output = io.BytesIO()
    output.write(b"e2e-microfaceted-regression".ljust(80, b"\x00"))
    output.write(struct.pack("<I", 2 * columns * rows))

    def surface_z(x: float, y: float) -> float:
        return 4.0 * math.sin(x / 17.0) * math.cos(y / 19.0)

    for row in range(rows):
        y0, y1 = float(row), float(row + 1)
        for column in range(columns):
            x0, x1 = float(column), float(column + 1)
            z00 = surface_z(x0, y0)
            z10 = surface_z(x1, y0)
            z11 = surface_z(x1, y1)
            z01 = surface_z(x0, y1)
            output.write(
                record.pack(
                    0.0,
                    0.0,
                    1.0,
                    x0,
                    y0,
                    z00,
                    x1,
                    y0,
                    z10,
                    x1,
                    y1,
                    z11,
                    0,
                )
            )
            output.write(
                record.pack(
                    0.0,
                    0.0,
                    1.0,
                    x0,
                    y0,
                    z00,
                    x1,
                    y1,
                    z11,
                    x0,
                    y1,
                    z01,
                    0,
                )
            )
    return output.getvalue()


def _largest_component_fraction(mask: np.ndarray) -> float:
    """Return the dominant 8-connected component's share of visible pixels."""
    remaining = int(mask.sum())
    if remaining == 0:
        return 0.0
    visited = np.zeros(mask.shape, dtype=bool)
    largest = 0
    height, width = mask.shape
    for y, x in zip(*np.where(mask), strict=True):
        if visited[y, x]:
            continue
        visited[y, x] = True
        stack = [(int(y), int(x))]
        size = 0
        while stack:
            current_y, current_x = stack.pop()
            size += 1
            for delta_y in (-1, 0, 1):
                for delta_x in (-1, 0, 1):
                    next_y, next_x = current_y + delta_y, current_x + delta_x
                    if (
                        0 <= next_y < height
                        and 0 <= next_x < width
                        and mask[next_y, next_x]
                        and not visited[next_y, next_x]
                    ):
                        visited[next_y, next_x] = True
                        stack.append((next_y, next_x))
        largest = max(largest, size)
    return largest / remaining


def _embedded_3mf() -> tuple[bytes, tuple[int, int, int]]:
    """Build a valid small 3MF whose thumbnail is visually unmistakable."""
    color = (220, 40, 120)
    preview = io.BytesIO()
    Image.new("RGB", (32, 24), color).save(preview, format="PNG")
    model = b"""<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
  <resources>
    <object id="1" type="model">
      <mesh>
        <vertices>
          <vertex x="0" y="0" z="0"/><vertex x="10" y="0" z="0"/>
          <vertex x="10" y="10" z="0"/><vertex x="0" y="10" z="0"/>
          <vertex x="0" y="0" z="10"/><vertex x="10" y="0" z="10"/>
          <vertex x="10" y="10" z="10"/><vertex x="0" y="10" z="10"/>
        </vertices>
        <triangles>
          <triangle v1="0" v2="1" v3="2"/><triangle v1="0" v2="2" v3="3"/>
          <triangle v1="4" v2="6" v3="5"/><triangle v1="4" v2="7" v3="6"/>
          <triangle v1="0" v2="4" v3="5"/><triangle v1="0" v2="5" v3="1"/>
          <triangle v1="1" v2="5" v3="6"/><triangle v1="1" v2="6" v3="2"/>
          <triangle v1="2" v2="6" v3="7"/><triangle v1="2" v2="7" v3="3"/>
          <triangle v1="4" v2="0" v3="3"/><triangle v1="4" v2="3" v3="7"/>
        </triangles>
      </mesh>
    </object>
  </resources>
  <build><item objectid="1"/></build>
</model>"""
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("3D/3dmodel.model", model)
        zf.writestr("Metadata/thumbnail.png", preview.getvalue())
    return archive.getvalue(), color


class TestMetadata:
    @pytest.mark.asyncio
    async def test_dxf_source_keeps_original_bytes_without_a_preview(
        self, api, tmp_path
    ):
        headers = await _setup_and_login(api, tmp_path)
        original = b"0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n"
        uploaded = await api.post(
            "/api/v1/ingest/model",
            files={"file": ("drawing.dxf", original, "image/vnd.dxf")},
            data={"model_name": "Drawing"},
            headers=headers,
        )
        assert uploaded.status_code == 202, uploaded.text
        job = await _await_job(api, headers, uploaded.json()["job_id"])
        assert job["state"] == "completed", job
        file_id = job["file_id"]
        # No preview renderer exists for drawings, so no derivative is owed:
        # nothing shows as pending, and nothing ever fails.
        derivatives = await api.get(
            f"/api/v1/files/{file_id}/derivatives", headers=headers
        )
        assert derivatives.status_code == 200, derivatives.text
        assert derivatives.json() == []
        downloaded = await api.get(f"/api/v1/files/{file_id}/download", headers=headers)
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.content == original

        repeated = await api.post(
            "/api/v1/ingest/model",
            files={"file": ("drawing-copy.dxf", original, "image/vnd.dxf")},
            data={"model_name": "Drawing copy"},
            headers=headers,
        )
        assert repeated.status_code == 202, repeated.text
        duplicate_job = await _await_job(api, headers, repeated.json()["job_id"])
        assert duplicate_job["state"] == "completed", duplicate_job
        models = (await api.get("/api/v1/models", headers=headers)).json()
        assert (
            len([model for model in models if model["name"].startswith("Drawing")]) == 1
        )

    @pytest.mark.critical
    @pytest.mark.asyncio
    async def test_a_repeated_gcode_upload_dedupes_by_content_hash(
        self, api, tmp_path, e2e_db
    ):
        headers = await _setup_and_login(api, tmp_path)

        job = await _await_job(
            api, headers, (await _upload(api, headers, model_name="Benchy"))["job_id"]
        )
        assert job["state"] == "completed", job

        # The model now exists and is listable.
        listing = await api.get("/api/v1/models", headers=headers)
        assert listing.status_code == 200, listing.text
        models = listing.json()
        assert any(m["name"] == "Benchy" for m in models), models

        # Parsed slicer metadata is attached to the persisted file.
        from sqlmodel import select

        from app.db.models import Metadata

        meta = e2e_db.exec(select(Metadata)).first()
        assert meta is not None, "expected extracted metadata row"
        # The OrcaSlicer benchy fixture carries a real layer height + slicer name.
        assert (meta.slicer_name or "").lower().startswith(
            "orca"
        ) or meta.layer_height_mm

        # Re-uploading identical bytes dedups by content hash (no second model).
        dup = await _await_job(
            api,
            headers,
            (await _upload(api, headers, model_name="Benchy Copy"))["job_id"],
        )
        assert dup["state"] in ("duplicate", "completed"), dup
        listing2 = (await api.get("/api/v1/models", headers=headers)).json()
        benchies = [m for m in listing2 if m["name"] in ("Benchy", "Benchy Copy")]
        assert len(benchies) == 1, f"dedup failed, got {benchies}"

    @pytest.mark.asyncio
    async def test_over_cap_mesh_upload_has_a_visible_thumbnail(
        self, api, tmp_path, e2e_db, monkeypatch
    ):
        """The headline #67 flow persists a useful fallback through the real API."""
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1_000)
        stl = _microfaceted_stl()
        headers = await _setup_and_login(api, tmp_path)

        uploaded = await api.post(
            "/api/v1/ingest/model",
            files={"file": ("issue-67-dense.stl", stl, "application/sla")},
            data={"model_name": "Issue 67 Dense"},
            headers=headers,
        )
        assert uploaded.status_code == 202, uploaded.text
        job = await _await_job(api, headers, uploaded.json()["job_id"])

        assert job["state"] == "completed", job
        file_id = job["file_id"]
        assert (await _thumbnail_derivative(api, headers, file_id))["state"] == "ready"
        # Over the render cap, the thumbnail is a bounded fallback, not a full render.
        assert _thumbnail_output(e2e_db, file_id)["strategy"] in {
            "streaming",
            "fallback",
        }
        thumbnail = await api.get(f"/api/v1/files/{file_id}/thumbnail", headers=headers)
        assert thumbnail.status_code == 200, thumbnail.text
        assert thumbnail.headers["content-type"] == "image/webp"

        with Image.open(io.BytesIO(thumbnail.content)) as image:
            pixels = np.asarray(image.convert("RGBA"))
        visible = pixels[:, :, 3] > 20
        assert visible.mean() > 0.08
        assert _largest_component_fraction(visible) > 0.75
        assert float(pixels[:, :, :3][visible].std()) > 8.0

    @pytest.mark.asyncio
    async def test_3mf_upload_persists_embedded_preview(
        self, api, tmp_path, e2e_db, monkeypatch
    ):
        """The public ingest flow normalizes a valid embedded 3MF preview."""
        # Force the safe over-cap branch: geometry is intentionally skipped, while
        # the valid embedded image must still bypass any mesh rasterization.
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1)
        archive, color = _embedded_3mf()
        headers = await _setup_and_login(api, tmp_path)

        uploaded = await api.post(
            "/api/v1/ingest/model",
            files={"file": ("embedded-preview.3mf", archive, "model/3mf")},
            data={"model_name": "Embedded Preview"},
            headers=headers,
        )
        assert uploaded.status_code == 202, uploaded.text
        job = await _await_job(api, headers, uploaded.json()["job_id"])

        assert job["state"] == "completed", job
        derivative = await _thumbnail_derivative(api, headers, job["file_id"])
        assert derivative["state"] == "ready", derivative
        assert _thumbnail_output(e2e_db, job["file_id"])["strategy"] == "embedded"
        thumbnail = await api.get(
            f"/api/v1/files/{job['file_id']}/thumbnail", headers=headers
        )
        assert thumbnail.status_code == 200, thumbnail.text
        assert thumbnail.headers["content-type"] == "image/webp"

        with Image.open(io.BytesIO(thumbnail.content)) as image:
            pixels = np.asarray(image.convert("RGBA"))
        width = int(settings.model_thumbnail_width)
        assert image.size == (width, round(width * 3 / 4))
        assert tuple(pixels[0, 0]) == (0, 0, 0, 0)
        assert tuple(pixels[image.height // 2, image.width // 2, :3]) == color
        assert pixels[:, :, 3].mean() > 100

    @pytest.mark.asyncio
    async def test_a_3mf_whose_parts_are_placed_by_transform_previews_correctly(
        self, api, tmp_path, e2e_db
    ):
        """The whole 3MF path, from upload to STL, through the real app.

        A 3MF written by 3D Builder stores one mesh at the origin and positions it
        through a nested build/component graph. Every layer between the upload and
        the viewer has to carry that placement — ingestion, the conversion, the
        content-addressed cache, the route — and any one of them dropping it
        serves a part at 0,0,0 with no error to explain it. Only the e2e tier
        exercises all four together.
        """
        headers = await _setup_and_login(api, tmp_path)

        uploaded = await api.post(
            "/api/v1/ingest/model",
            files={
                "file": (
                    "3d-builder-component.3mf",
                    build_3d_builder_component_project(),
                    "model/3mf",
                )
            },
            data={"model_name": "3D Builder Component"},
            headers=headers,
        )
        assert uploaded.status_code == 202, uploaded.text
        job = await _await_job(api, headers, uploaded.json()["job_id"])
        assert job["state"] == "completed", job

        response = await api.get(f"/api/v1/files/{job['file_id']}/stl", headers=headers)

        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/sla")
        mesh = trimesh.load_mesh(
            io.BytesIO(response.content), file_type="stl", process=False
        )
        np.testing.assert_allclose(
            mesh.bounds,
            np.asarray([[110.0, 220.0, 330.0], [112.0, 223.0, 334.0]]),
            atol=1e-5,
        )


class TestHardlinklessStaging:
    @pytest.mark.asyncio
    async def test_uploads_with_visible_staging_degradation(
        self, api, tmp_path, monkeypatch
    ):
        import errno
        import os

        from tests.factories import content

        def unsupported(*args, **kwargs):
            raise OSError(errno.EPERM, "no hard links")

        monkeypatch.setattr(os, "link", unsupported)
        headers = await _setup_and_login(api, tmp_path)
        health = await api.get("/api/v1/health/details", headers=headers)
        storage = health.json()["components"]["storage"]
        assert storage["diagnostics"]["staging"]["hardlink"] is False
        assert storage["diagnostics"]["staging"]["exclusive_create"] is True
        assert any(
            "staging" in warning and "copy" in warning
            for warning in storage["warnings"]
        )
        payload = content.ascii_stl()
        uploaded = await api.post(
            "/api/v1/ingest/model",
            headers=headers,
            files={"file": ("unraid.stl", payload, "model/stl")},
        )
        assert uploaded.status_code == 202, uploaded.text
        job = await _await_job(api, headers, uploaded.json()["job_id"])
        assert job["state"] == "completed", job
        downloaded = await api.get(
            f"/api/v1/files/{job['file_id']}/download", headers=headers
        )
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.content == payload
