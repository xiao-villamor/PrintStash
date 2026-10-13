"""An accepted archive selection can be withdrawn before model ingestion."""

import pytest
from sqlmodel import select

from app.db.models import File, IngestRequest, Job, JobState
from tests.e2e._jobs import settle
from tests.factories.content import gcode, zip_bytes


class TestBatchImportCancellation:
    @pytest.mark.asyncio
    async def test_cancels_selected_archive_before_execution(
        self, api, superuser_headers, e2e_db
    ) -> None:
        archive = zip_bytes(
            {
                "first.gcode": gcode(marker="first-canceled"),
                "second.gcode": gcode(marker="second-canceled"),
            }
        )
        inspected = await api.post(
            "/api/v1/ingest/archive/inspect",
            headers=superuser_headers,
            files={"file": ("batch.zip", archive, "application/zip")},
        )
        assert inspected.status_code == 202, inspected.text
        inspection_id = inspected.json()["job_id"]
        settle()
        inspected_job = await api.get(
            f"/api/v1/jobs/{inspection_id}",
            headers=superuser_headers,
        )
        assert inspected_job.status_code == 200, inspected_job.text
        assert inspected_job.json()["state"] == "completed"
        selected = await api.post(
            f"/api/v1/ingest/archive/{inspection_id}/select",
            headers=superuser_headers,
            json={"names": ["first.gcode", "second.gcode"]},
        )
        assert selected.status_code == 202, selected.text
        job_id = selected.json()["job_id"]
        request = e2e_db.get(IngestRequest, job_id)
        assert request is not None
        job = e2e_db.get(Job, job_id)
        assert job is not None and job.state is JobState.QUEUED
        canceled = await api.post(
            f"/api/v1/jobs/{job_id}/cancel", headers=superuser_headers
        )
        assert canceled.status_code == 200, canceled.text
        settle()
        final = await api.get(f"/api/v1/jobs/{job_id}", headers=superuser_headers)
        assert final.status_code == 200, final.text
        assert final.json()["state"] == "cancelled"
        e2e_db.expire_all()
        assert e2e_db.exec(select(File)).all() == []
