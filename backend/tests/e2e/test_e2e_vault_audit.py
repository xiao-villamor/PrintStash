"""E2E: a missing derived thumbnail is found and safely regenerated.

This is the headline maintenance journey through the real ingest and audit
routers: upload a mesh, remove only its derived thumbnail, audit, repair, then
audit again.  It deliberately never mutates the primary Artifact bytes.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlmodel import select

from app.db.models import Model
from app.services.setup_token import current_setup_token
from app.services.storage_backend import get_backend, init_backend

pytestmark = pytest.mark.e2e

_STL = b"""solid audit_fixture
facet normal 0 0 1
 outer loop
  vertex 0 0 0
  vertex 1 0 0
  vertex 0 1 0
 endloop
endfacet
endsolid audit_fixture
"""


async def _setup_and_login(api, tmp_path) -> dict[str, str]:
    response = await api.post(
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
    assert response.status_code == 201, response.text
    init_backend()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _await_job(api, headers: dict[str, str], job_id: str) -> dict:
    for _ in range(100):
        response = await api.get(f"/api/v1/ingest/jobs/{job_id}", headers=headers)
        assert response.status_code == 200, response.text
        job = response.json()
        if job["state"] in ("completed", "failed"):
            return job
        await asyncio.sleep(0.05)
    raise AssertionError(f"ingest job did not finish: {job}")


@pytest.mark.asyncio
async def test_quick_audit_finds_and_repairs_missing_thumbnail(api, tmp_path, e2e_db):
    headers = await _setup_and_login(api, tmp_path)
    upload = await api.post(
        "/api/v1/ingest/model",
        files={"file": ("audit-fixture.stl", _STL, "model/stl")},
        data={"model_name": "Audit fixture"},
        headers=headers,
    )
    assert upload.status_code == 202, upload.text
    assert (await _await_job(api, headers, upload.json()["job_id"]))["state"] == "completed"

    model = e2e_db.exec(select(Model).where(Model.name == "Audit fixture")).one()
    assert model.thumbnail_file_id is not None
    assert model.thumbnail_path is not None
    backend = get_backend()
    assert backend.exists(model.thumbnail_path)
    backend.delete(model.thumbnail_path)

    started = await api.post(
        "/api/v1/maintenance/audits", json={"mode": "quick"}, headers=headers
    )
    assert started.status_code == 202, started.text
    audited = await api.get(
        f"/api/v1/maintenance/audits/{started.json()['id']}", headers=headers
    )
    assert audited.status_code == 200, audited.text
    finding = next(item for item in audited.json()["findings"] if item["code"] == "thumbnail_missing")
    assert finding["repair_action"] == "regenerate_thumbnail"

    repaired = await api.post(
        f"/api/v1/maintenance/findings/{finding['id']}/repair", headers=headers
    )
    assert repaired.status_code == 200, repaired.text
    assert repaired.json()["state"] == "resolved"
    assert backend.exists(model.thumbnail_path)

    rerun = await api.post(
        "/api/v1/maintenance/audits", json={"mode": "quick"}, headers=headers
    )
    assert rerun.status_code == 202, rerun.text
    healthy = await api.get(
        f"/api/v1/maintenance/audits/{rerun.json()['id']}", headers=headers
    )
    assert healthy.status_code == 200, healthy.text
    assert "thumbnail_missing" not in {item["code"] for item in healthy.json()["findings"]}
