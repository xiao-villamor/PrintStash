"""E2E: G-code ingestion, end to end through the real pipeline.

Uploads a real OrcaSlicer fixture through the public ingest endpoint, waits for
the background job to finish, and asserts the model was persisted with parsed
slicer metadata. Re-uploading the same bytes must dedup by content hash rather
than create a second model.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "real_orca_ender3_benchy.gcode"


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


@pytest.mark.asyncio
async def test_gcode_upload_parses_metadata_and_dedups(api, tmp_path, e2e_db):
    headers = await _setup_and_login(api, tmp_path)

    job = await _await_job(api, headers, (await _upload(api, headers, model_name="Benchy"))["job_id"])
    assert job["state"] == "completed", job

    # The model now exists and is listable.
    listing = await api.get("/api/v1/models", headers=headers)
    assert listing.status_code == 200, listing.text
    models = listing.json()
    assert any(m["name"] == "Benchy" for m in models), models

    # Parsed slicer metadata is attached to the persisted file.
    from app.db.models import Metadata
    from sqlmodel import select

    meta = e2e_db.exec(select(Metadata)).first()
    assert meta is not None, "expected extracted metadata row"
    # The OrcaSlicer benchy fixture carries a real layer height + slicer name.
    assert (meta.slicer_name or "").lower().startswith("orca") or meta.layer_height_mm

    # Re-uploading identical bytes dedups by content hash (no second model).
    dup = await _await_job(api, headers, (await _upload(api, headers, model_name="Benchy Copy"))["job_id"])
    assert dup["state"] in ("duplicate", "completed"), dup
    listing2 = (await api.get("/api/v1/models", headers=headers)).json()
    benchies = [m for m in listing2 if m["name"] in ("Benchy", "Benchy Copy")]
    assert len(benchies) == 1, f"dedup failed, got {benchies}"
