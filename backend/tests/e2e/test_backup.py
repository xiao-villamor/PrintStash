"""E2E: backup -> data loss -> restore, driven through the real HTTP API.

``tests/test_backup_restore.py`` already exercises ``create_backup``/
``restore_backup`` extensively at the service-call level (including S3 and
corrupt-archive handling). This drives the same real functions through the
real ``/api/v1/backups`` endpoints instead, and asserts the *application*
endpoints (models, files) return the same data after restore, not just that
the service call succeeded.
"""

from __future__ import annotations

import asyncio
import shutil

import pytest
from sqlmodel import delete

from app.db.models import File, Metadata, Model
from app.services.setup_token import current_setup_token
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
    from app.services.storage_backend import init_backend

    init_backend()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _upload_and_wait(api, headers, *, model_name: str) -> dict:
    up = await api.post(
        "/api/v1/ingest/orca",
        files={"file": (FIXTURE.name, FIXTURE.read_bytes(), "text/plain")},
        data={"model_name": model_name},
        headers=headers,
    )
    assert up.status_code == 202, up.text
    job_id = up.json()["job_id"]
    for _ in range(50):
        status = (
            await api.get(f"/api/v1/ingest/jobs/{job_id}", headers=headers)
        ).json()
        if status["state"] in ("completed", "failed", "duplicate"):
            break
        await asyncio.sleep(0.05)
    assert status["state"] == "completed", status
    models = (await api.get("/api/v1/models", headers=headers)).json()
    return next(m for m in models if m["name"] == model_name)


class TestBackupRestore:
    @pytest.mark.asyncio
    async def test_backup_wipe_restore_round_trips_through_the_real_api(
        self, api, tmp_path, e2e_db
    ):
        headers = await _setup_and_login(api, tmp_path)
        model = await _upload_and_wait(api, headers, model_name="Backup Benchy")
        model_id = model["id"]
        detail = (await api.get(f"/api/v1/models/{model_id}", headers=headers)).json()
        file_id = detail["files"][0]["id"]

        original_blob = (
            await api.get(f"/api/v1/files/{file_id}/download", headers=headers)
        ).content
        assert original_blob == FIXTURE.read_bytes()

        created = await api.post("/api/v1/backups", headers=headers)
        assert created.status_code == 202, created.text
        backup_id = created.json()["backup_id"]
        assert created.json()["file_count"] >= 1

        listed = await api.get("/api/v1/backups", headers=headers)
        assert any(b["backup_id"] == backup_id for b in listed.json())

        # Simulate real data loss: drop the DB rows and delete the blob from disk,
        # bypassing the app entirely (a disk/DB disaster, not a soft delete).
        e2e_db.exec(delete(Metadata).where(Metadata.file_id == file_id))
        e2e_db.exec(delete(File).where(File.id == file_id))
        e2e_db.exec(delete(Model).where(Model.id == model_id))
        e2e_db.commit()
        shutil.rmtree(tmp_path / "files", ignore_errors=True)
        (tmp_path / "files").mkdir(parents=True, exist_ok=True)

        gone = await api.get(f"/api/v1/models/{model_id}", headers=headers)
        assert gone.status_code == 404

        restored = await api.post(
            f"/api/v1/backups/{backup_id}/restore", headers=headers
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["restored_files"] >= 1

        back = await api.get(f"/api/v1/models/{model_id}", headers=headers)
        assert back.status_code == 200, back.text
        assert back.json()["name"] == "Backup Benchy"

        restored_blob = (
            await api.get(f"/api/v1/files/{file_id}/download", headers=headers)
        ).content
        assert restored_blob == FIXTURE.read_bytes()

    @pytest.mark.asyncio
    async def test_restore_of_unknown_backup_id_is_404(self, api, tmp_path, e2e_db):
        headers = await _setup_and_login(api, tmp_path)
        resp = await api.post(
            "/api/v1/backups/not-a-real-backup-id/restore", headers=headers
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "backup_not_found"


class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_backup_removes_it_from_listing(self, api, tmp_path, e2e_db):
        headers = await _setup_and_login(api, tmp_path)
        await _upload_and_wait(api, headers, model_name="Deletable Backup Benchy")

        created = await api.post("/api/v1/backups", headers=headers)
        backup_id = created.json()["backup_id"]

        deleted = await api.delete(f"/api/v1/backups/{backup_id}", headers=headers)
        assert deleted.status_code == 200, deleted.text
        assert deleted.json() == {"backup_id": backup_id, "deleted": True}

        listed = (await api.get("/api/v1/backups", headers=headers)).json()
        assert all(b["backup_id"] != backup_id for b in listed)

        missing = await api.get(f"/api/v1/backups/{backup_id}", headers=headers)
        assert missing.status_code == 404
