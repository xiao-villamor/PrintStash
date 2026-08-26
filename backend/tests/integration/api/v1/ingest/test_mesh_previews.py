"""Mesh uploads keep useful previews across the public ingest boundary.

Dense STL fallback rendering and embedded 3MF previews must survive persistence and
remain retrievable as authenticated WebP responses.
"""

from __future__ import annotations

import io
import math
import struct
import zipfile
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session, select

from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import BackgroundJob, InboxItem, InboxItemState, User
from app.services.jobs import registry

from ._ingest_api_shared import _completed_job, _configure_storage


@pytest.fixture(autouse=True)
def _use_file_backed_db(file_backed_integration_db: None) -> None:
    """Let request, worker, and assertion sessions use separate connections."""


def _microfaceted_stl(columns: int = 420, rows: int = 420) -> bytes:
    record = struct.Struct("<12fH")
    output = io.BytesIO()
    output.write(b"integration-microfaceted-regression".ljust(80, b"\x00"))
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
    color = (220, 40, 120)
    preview = io.BytesIO()
    Image.new("RGB", (32, 24), color).save(preview, format="PNG")
    model = b"""<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
  <resources><object id="1" type="model"><mesh><vertices>
    <vertex x="0" y="0" z="0"/><vertex x="10" y="0" z="0"/>
    <vertex x="10" y="10" z="0"/><vertex x="0" y="10" z="0"/>
    <vertex x="0" y="0" z="10"/><vertex x="10" y="0" z="10"/>
    <vertex x="10" y="10" z="10"/><vertex x="0" y="10" z="10"/>
  </vertices><triangles>
    <triangle v1="0" v2="1" v3="2"/><triangle v1="0" v2="2" v3="3"/>
    <triangle v1="4" v2="6" v3="5"/><triangle v1="4" v2="7" v3="6"/>
    <triangle v1="0" v2="4" v3="5"/><triangle v1="0" v2="5" v3="1"/>
    <triangle v1="1" v2="5" v3="6"/><triangle v1="1" v2="6" v3="2"/>
    <triangle v1="2" v2="6" v3="7"/><triangle v1="2" v2="7" v3="3"/>
    <triangle v1="4" v2="0" v3="3"/><triangle v1="4" v2="3" v3="7"/>
  </triangles></mesh></object></resources><build><item objectid="1"/></build>
</model>"""
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("3D/3dmodel.model", model)
        zf.writestr("Metadata/thumbnail.png", preview.getvalue())
    return archive.getvalue(), color


def test_over_cap_mesh_upload_has_a_visible_thumbnail(
    tmp_path: Path,
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    _configure_storage(tmp_path)
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1_000)
    owner = db_session.exec(select(User).where(User.username == "test-writer")).one()
    expired_job = BackgroundJob(
        id="issue-67-expired-inbox-job",
        owner_user_id=owner.id,
        state="completed",
        status_json='{"state":"completed"}',
        finished_at=utcnow() - timedelta(hours=2),
    )
    db_session.add(expired_job)
    db_session.flush()
    db_session.add(
        InboxItem(
            owner_user_id=owner.id,
            state=InboxItemState.COMPLETED,
            background_job_id=expired_job.id,
        )
    )
    db_session.commit()
    monkeypatch.setattr(registry, "_last_persisted_prune_at", float("-inf"))

    payload = _completed_job(
        client,
        client.post(
            "/api/v1/ingest/model",
            files={
                "file": (
                    "issue-67-dense.stl",
                    _microfaceted_stl(),
                    "application/sla",
                )
            },
            data={"model_name": "Issue 67 Dense"},
            headers=auth_headers,
        ),
    )

    assert payload["thumbnail_status"] == "fallback_generated", payload
    thumbnail = client.get(
        f"/api/v1/files/{payload['file_id']}/thumbnail", headers=auth_headers
    )
    assert thumbnail.status_code == 200, thumbnail.text
    assert thumbnail.headers["content-type"] == "image/webp"
    with Image.open(io.BytesIO(thumbnail.content)) as image:
        pixels = np.asarray(image.convert("RGBA"))
    visible = pixels[:, :, 3] > 20
    assert visible.mean() > 0.08
    assert _largest_component_fraction(visible) > 0.75
    assert float(pixels[:, :, :3][visible].std()) > 8.0


def test_3mf_upload_persists_embedded_preview(
    tmp_path: Path,
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    _configure_storage(tmp_path)
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1)
    archive, color = _embedded_3mf()

    payload = _completed_job(
        client,
        client.post(
            "/api/v1/ingest/model",
            files={"file": ("embedded-preview.3mf", archive, "model/3mf")},
            data={"model_name": "Embedded Preview"},
            headers=auth_headers,
        ),
    )

    assert payload["thumbnail_status"] == "generated", payload
    thumbnail = client.get(
        f"/api/v1/files/{payload['file_id']}/thumbnail", headers=auth_headers
    )
    assert thumbnail.status_code == 200, thumbnail.text
    assert thumbnail.headers["content-type"] == "image/webp"
    with Image.open(io.BytesIO(thumbnail.content)) as image:
        pixels = np.asarray(image.convert("RGB"))
    assert np.all(pixels == color)
