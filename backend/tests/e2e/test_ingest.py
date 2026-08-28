"""E2E: G-code ingestion, end to end through the real pipeline.

Uploads a real OrcaSlicer fixture through the public ingest endpoint, waits for
the background job to finish, and asserts the model was persisted with parsed
slicer metadata. Re-uploading the same bytes must dedup by content hash rather
than create a second model.
"""

from __future__ import annotations

import asyncio
import io
import math
import struct
import zipfile
from datetime import timedelta

import numpy as np
import pytest
import trimesh
from PIL import Image
from sqlmodel import select

from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import BackgroundJob, InboxItem, InboxItemState, User
from app.services.jobs import registry
from app.services.setup_token import current_setup_token
from tests.fixtures.three_mf_projects import build_3d_builder_component_project
from tests.paths import FIXTURES_DIR

FIXTURE = FIXTURES_DIR / "real_orca_ender3_benchy.gcode"


async def _setup_and_login(api, tmp_path) -> dict[str, str]:
    r = await api.post(
        "/api/v1/setup",
        json={
            "setup_token": current_setup_token(),
            "username": "owner",
            "password": "Password123",
            "storage_backend": "local",
            "data_dir": str(tmp_path / "files"),
            "thumb_dir": str(tmp_path / "thumbs"),
        },
    )
    assert r.status_code == 201, r.text
    # Storage backend is normally initialised in the app lifespan (not run here).
    from app.services.storage_backend import init_backend

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
    for _ in range(50):
        r = await api.get(f"/api/v1/ingest/jobs/{job_id}", headers=headers)
        assert r.status_code == 200, r.text
        job = r.json()
        if job["state"] in ("completed", "failed", "duplicate"):
            return job
        await asyncio.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish: {job}")


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
        owner = e2e_db.exec(select(User).where(User.username == "owner")).one()
        expired_job = BackgroundJob(
            id="issue-67-expired-inbox-job",
            owner_user_id=owner.id,
            state="completed",
            status_json='{"state":"completed"}',
            finished_at=utcnow() - timedelta(hours=2),
        )
        e2e_db.add(expired_job)
        e2e_db.flush()
        e2e_db.add(
            InboxItem(
                owner_user_id=owner.id,
                state=InboxItemState.COMPLETED,
                background_job_id=expired_job.id,
            )
        )
        e2e_db.commit()
        monkeypatch.setattr(registry, "_last_persisted_prune_at", float("-inf"))

        uploaded = await api.post(
            "/api/v1/ingest/model",
            files={"file": ("issue-67-dense.stl", stl, "application/sla")},
            data={"model_name": "Issue 67 Dense"},
            headers=headers,
        )
        assert uploaded.status_code == 202, uploaded.text
        job = await _await_job(api, headers, uploaded.json()["job_id"])

        assert job["state"] == "completed", job
        assert job["thumbnail_status"] == "fallback_generated", job
        file_id = job["file_id"]
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
        """The public ingest flow serves a valid embedded 3MF preview unchanged."""
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
        assert job["thumbnail_status"] == "generated", job
        thumbnail = await api.get(
            f"/api/v1/files/{job['file_id']}/thumbnail", headers=headers
        )
        assert thumbnail.status_code == 200, thumbnail.text
        assert thumbnail.headers["content-type"] == "image/webp"

        with Image.open(io.BytesIO(thumbnail.content)) as image:
            pixels = np.asarray(image.convert("RGB"))
        assert np.all(pixels == color)

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

        response = await api.get(
            f"/api/v1/files/{job['file_id']}/stl", headers=headers
        )

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
