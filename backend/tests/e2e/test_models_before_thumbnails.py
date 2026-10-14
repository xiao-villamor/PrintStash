"""An import shows every Model first; thumbnails start once it has finished.

Two uploads go through the public API, the second arriving after the first
Model is committed. While either is still being imported no thumbnail Job
exists, so the Models appear without derived work competing with the import;
the last upload to settle starts the thumbnails, and both arrive.
"""

from __future__ import annotations

import pytest
from sqlmodel import select

from app.db.models import Job, JobKind, JobState
from app.db.session import get_session_factory
from tests.e2e._jobs import settle
from tests.factories.geometry import tetrahedron


def _jobs(kind: JobKind) -> list[Job]:
    with get_session_factory().scoped_session() as session:
        return list(session.exec(select(Job).where(Job.kind == kind)).all())


async def _setup_and_login(api, tmp_path) -> dict[str, str]:
    created = await api.post(
        "/api/v1/setup",
        json={
            "username": "owner",
            "password": "Password123",
            "storage_backend": "local",
            "data_dir": str(tmp_path / "files"),
            "thumb_dir": str(tmp_path / "thumbs"),
        },
    )
    assert created.status_code == 201, created.text
    from app.modules.storage.storage_backend.runtime import init_backend

    init_backend()
    return {"Authorization": f"Bearer {created.json()['access_token']}"}


async def _upload(api, headers, name: str, scale: float) -> str:
    mesh = tetrahedron()
    mesh.apply_scale(scale)
    accepted = await api.post(
        "/api/v1/ingest/model",
        files={"file": (f"{name}.stl", mesh.export(file_type="stl"), "model/stl")},
        data={"model_name": name},
        headers=headers,
    )
    assert accepted.status_code == 202, accepted.text
    return accepted.json()["job_id"]


@pytest.mark.asyncio
async def test_thumbnails_start_after_the_last_upload(api, tmp_path) -> None:
    from app.modules.work.catalog import get_engine

    headers = await _setup_and_login(api, tmp_path)
    engine = get_engine()
    first = await _upload(api, headers, "first", 1.0)
    # The first Model's commit queues a thumbnail pass; the second upload
    # arrives while that pass is still waiting to run.
    assert engine.run_one() is not None  # type: ignore[attr-defined]
    second = await _upload(api, headers, "second", 2.0)
    thumbnails_while_importing: list[int] = []
    while any(
        job.state not in (JobState.COMPLETED, JobState.FAILED)
        for job in _jobs(JobKind.INGESTION_UPLOAD)
    ):
        thumbnails_while_importing.append(len(_jobs(JobKind.DERIVATIVES_MESH)))
        assert engine.run_one() is not None, "the uploads stopped short"  # type: ignore[attr-defined]

    settle()

    uploads = {job.id: job.state for job in _jobs(JobKind.INGESTION_UPLOAD)}
    thumbnails = [job.state for job in _jobs(JobKind.DERIVATIVES_MESH)]
    assert uploads == {first: JobState.COMPLETED, second: JobState.COMPLETED}
    assert set(thumbnails_while_importing) == {0}
    assert thumbnails == [JobState.COMPLETED, JobState.COMPLETED]
