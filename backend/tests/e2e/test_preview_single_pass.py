"""An upload with multi-angle previews enabled gets its views from one mesh load."""

import pytest
from sqlmodel import Session, col, select

from app.db.models import ArtifactDerivative, DerivativeKind, Job, JobKind
from tests.e2e._jobs import settle
from tests.factories import content


class TestPreviewSinglePass:
    @pytest.mark.asyncio
    async def test_upload_publishes_views_from_the_cover_pass(
        self, api, superuser_headers, e2e_db
    ):
        headers = superuser_headers
        enabled = await api.put(
            "/api/v1/config",
            headers=headers,
            json={"derivatives_previews_enabled": True},
        )
        assert enabled.status_code == 200, enabled.text
        upload = await api.post(
            "/api/v1/ingest/model",
            headers=headers,
            files={"file": ("views.stl", content.binary_stl(), "application/sla")},
        )
        assert upload.status_code == 202, upload.text
        settle()
        job = (
            await api.get(f"/api/v1/jobs/{upload.json()['job_id']}", headers=headers)
        ).json()
        file_id = job["file_id"]
        derivatives = await api.get(
            f"/api/v1/files/{file_id}/derivatives", headers=headers
        )
        assert {row["kind"]: row["state"] for row in derivatives.json()} == {
            "metadata": "ready",
            "previews": "ready",
            "thumbnail": "ready",
        }
        with Session(e2e_db.get_bind()) as fresh:
            views = fresh.exec(
                select(ArtifactDerivative).where(
                    ArtifactDerivative.file_id == file_id,
                    ArtifactDerivative.kind == DerivativeKind.PREVIEWS,
                )
            ).one()
            batches = fresh.exec(
                select(Job.id).where(col(Job.kind) == JobKind.DERIVATIVES_PREVIEWS)
            ).all()
        # The cover Job owned the only attempt; no second load was queued.
        assert views.attempts == 1
        assert batches == []
        model = await api.get(f"/api/v1/models/{job['model_id']}", headers=headers)
        assert model.status_code == 200, model.text
        previews = model.json()["previews"]
        assert previews["state"] == "ready"
        assert [view["id"] for view in previews["published"]["views"]] == [
            "orbit-0",
            "orbit-1",
            "orbit-2",
            "orbit-3",
        ]
        image = await api.get(previews["published"]["views"][0]["url"], headers=headers)
        assert image.status_code == 200
        assert image.headers["content-type"] == "image/webp"
