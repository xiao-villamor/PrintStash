"""An administrator disables processing, uploads, and resumes the missing preview."""

import io

import pytest
from PIL import Image
from sqlmodel import Session

from app.db.models import SystemConfig
from tests.e2e._jobs import settle
from tests.factories import content


class TestDerivativeControls:
    @pytest.mark.asyncio
    async def test_disable_upload_enable_produces_a_preview(
        self, api, superuser_headers, e2e_db
    ):
        headers = superuser_headers
        disabled = await api.put(
            "/api/v1/config", headers=headers, json={"derivatives_mesh_enabled": False}
        )
        assert disabled.status_code == 200, disabled.text
        upload = await api.post(
            "/api/v1/ingest/model",
            headers=headers,
            files={"file": ("disabled.stl", content.binary_stl(), "application/sla")},
        )
        assert upload.status_code == 202, upload.text
        settle()
        job = (
            await api.get(f"/api/v1/jobs/{upload.json()['job_id']}", headers=headers)
        ).json()
        assert job["state"] == "completed", job
        file_id = job["file_id"]
        before = await api.get(f"/api/v1/files/{file_id}/derivatives", headers=headers)
        assert {row["state"] for row in before.json()} == {"disabled"}
        # A fresh session is how another process reads the persisted setting.
        with Session(e2e_db.get_bind()) as fresh:
            assert fresh.get(SystemConfig, 1).derivatives_mesh_enabled is False
        enabled = await api.put(
            "/api/v1/config", headers=headers, json={"derivatives_mesh_enabled": True}
        )
        assert enabled.status_code == 200, enabled.text
        settle()
        after = await api.get(f"/api/v1/files/{file_id}/derivatives", headers=headers)
        assert {row["state"] for row in after.json()} == {"ready"}
        thumbnail = await api.get(f"/api/v1/files/{file_id}/thumbnail", headers=headers)
        assert thumbnail.status_code == 200
        assert thumbnail.headers["content-type"].startswith("image/")
        decoded = Image.open(io.BytesIO(thumbnail.content))
        assert decoded.format == "WEBP"
        assert decoded.size == (640, 480)
        lossless = io.BytesIO()
        decoded.save(lossless, format="WEBP", lossless=True, exact=True, method=6)
        assert len(thumbnail.content) < len(lossless.getvalue()) * 0.6
