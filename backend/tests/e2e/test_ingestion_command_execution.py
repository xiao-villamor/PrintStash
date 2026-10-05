"""A slow engine hint cannot monopolize the ordinary API worker capacity."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from threading import Event

import pytest
from anyio import to_thread
from sqlmodel import select

from app.core.config import _overlay
from app.db.models import File, IngestRequest, Job, JobState, StagingLease
from tests.e2e._jobs import settle
from tests.factories import content


class TestIngestionCommandExecution:
    @pytest.mark.asyncio
    async def test_slow_nudge_preserves_api_progress_after_durable_acceptance(
        self,
        api,
        e2e_db,
        superuser_headers,
        work_engine,
        monkeypatch,
    ):
        payload = content.ascii_stl()
        entered = Event()
        release = Event()
        submit = work_engine.submit
        ordinary = to_thread.current_default_thread_limiter()
        previous_limit = ordinary.total_tokens
        ordinary.total_tokens = 1
        monkeypatch.setitem(_overlay, "api_command_concurrency", 1)

        def slow_hint(submission):
            entered.set()
            assert release.wait(timeout=10)
            return submit(submission)

        monkeypatch.setattr(work_engine, "submit", slow_hint)
        request = asyncio.create_task(
            api.post(
                "/api/v1/ingest/model",
                headers=superuser_headers,
                files={"file": ("responsive.stl", payload, "model/stl")},
            )
        )
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            response = await asyncio.wait_for(api.get("/api/v1/health"), timeout=2)
            assert response.status_code == 200, response.text
            assert not request.done()
            pending = e2e_db.exec(
                select(IngestRequest).where(
                    IngestRequest.original_filename == "responsive.stl"
                )
            ).one()
            job = e2e_db.get(Job, pending.job_id)
            assert job is not None and job.state is JobState.QUEUED
            assert job.attempts == 0
            lease = e2e_db.exec(
                select(StagingLease).where(StagingLease.job_id == pending.job_id)
            ).one()
            assert Path(lease.path).read_bytes() == payload
        finally:
            release.set()
            ordinary.total_tokens = previous_limit
        accepted = await request
        assert accepted.status_code == 202, accepted.text
        assert accepted.json()["job_id"] == pending.job_id
        monkeypatch.setattr(work_engine, "submit", submit)
        await asyncio.to_thread(settle)
        e2e_db.expire_all()
        artifact = e2e_db.exec(
            select(File).where(File.sha256 == hashlib.sha256(payload).hexdigest())
        ).one()
        downloaded = await api.get(
            f"/api/v1/files/{artifact.id}/download",
            headers=superuser_headers,
        )
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.content == payload
