"""Assertions shared by the ingest tests and the download tests that follow them.

These are *assertions*, not builders, which is why they are not in
`tests/factories/`: each one checks a whole cluster of postconditions that only
mean anything together, and repeating that cluster inline is how two files came to
disagree about what "ingested successfully" means.

They live in a module rather than a conftest because `api/v1/ingest/` and
`api/v1/files/` both need them, and a fixture in one directory's conftest is
invisible to its sibling. Previously `files/test_slicer_download.py` reached
*into* `ingest/test_ingest_api.py` and imported its private helpers — a coupling
that broke the moment either file was touched.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import File, FileType, Metadata, Model
from app.services.storage_backend import get_backend


def completed_job(client: TestClient, response) -> dict:
    """Drive an accepted ingest job to its terminal state and return it.

    Reuses the submitting request's Authorization header rather than taking one:
    a job is owner-scoped, so polling it as a different identity is a 404 that
    reads like a job failure.
    """
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    headers = {}
    authorization = response.request.headers.get("authorization")
    if authorization:
        headers["Authorization"] = authorization

    job = client.get(f"/api/v1/ingest/jobs/{job_id}", headers=headers)

    assert job.status_code == 200, job.text
    payload = job.json()
    assert payload["state"] == "completed", payload
    assert payload["model_id"] is not None
    assert payload["file_id"] is not None
    return payload


def assert_file_created(session: Session, file_id: int, file_type: FileType) -> File:
    """Assert the full postcondition of one successful ingest.

    Ingestion is a multi-table write plus two blob writes, and a partial result is
    the dangerous outcome: a `File` row whose bytes are missing, or a model whose
    thumbnail points at a key that was never written. Checking the row alone would
    pass for both. So this asserts the row, its bytes on the backend, its
    metadata, and the owning model's thumbnail — row *and* blob — together.
    """
    file_row = session.get(File, file_id)
    assert file_row is not None
    assert file_row.file_type == file_type
    assert file_row.size_bytes > 0
    assert get_backend().exists(file_row.path)

    metadata = session.exec(select(Metadata).where(Metadata.file_id == file_id)).one()
    assert metadata.file_id == file_id

    model = session.get(Model, file_row.model_id)
    assert model is not None
    assert model.thumbnail_file_id == file_id
    assert model.thumbnail_path
    assert get_backend().exists(model.thumbnail_path)
    return file_row
