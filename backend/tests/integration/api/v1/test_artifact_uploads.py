"""Defend the authenticated, provider-neutral resumable upload HTTP protocol.

A failure here means clients may lose resumability, isolation, or ingestion handoff.
"""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import (
    ArtifactUploadPart,
    ArtifactUploadSession,
    File,
    FileRevisionStatus,
    FileType,
    Model,
)
from tests._env import use_local_storage
from tests.factories import content


def _request(payload: bytes) -> dict[str, object]:
    return {
        "purpose": "model",
        "target_role": "new_model",
        "filename": "cube.stl",
        "media_type": "model/stl",
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _put_chunk(
    client: TestClient,
    headers: dict[str, str],
    upload_id: str,
    payload: bytes,
    *,
    sha256: str | None = None,
):
    return client.put(
        f"/api/v1/artifact-uploads/{upload_id}/chunks/0",
        params={
            "offset": 0,
            "length": len(payload),
            "sha256": sha256 or hashlib.sha256(payload).hexdigest(),
        },
        content=payload,
        headers=headers,
    )


class TestArtifactUploads:
    def test_provider_neutral_protocol_reaches_ingestion(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        tmp_path,
    ) -> None:
        use_local_storage(tmp_path)
        payload = content.binary_stl()
        created = client.post(
            "/api/v1/artifact-uploads", json=_request(payload), headers=auth_headers
        )
        assert created.status_code == 201
        upload_id = created.json()["id"]
        assert created.json()["mode"] == "api_chunks"

        plan = client.get(
            f"/api/v1/artifact-uploads/{upload_id}/plan", headers=auth_headers
        )
        assert plan.status_code == 200
        assert plan.headers["cache-control"] == "no-store"
        assert plan.json()["mode"] == "api_chunks"
        assert "provider" not in str(plan.json()).lower()

        first = _put_chunk(client, auth_headers, upload_id, payload)
        duplicate = _put_chunk(client, auth_headers, upload_id, payload)
        assert first.status_code == duplicate.status_code == 200
        assert duplicate.json()["session"]["received_bytes"] == len(payload)
        assert len(duplicate.json()["session"]["parts"]) == 1

        status = client.get(
            f"/api/v1/artifact-uploads/{upload_id}", headers=auth_headers
        )
        assert status.headers["cache-control"] == "no-store"
        assert status.json()["received_bytes"] == len(payload)
        assert not {
            "protected_native_id",
            "staging_identity_json",
            "destination_ref",
            "request_json",
        }.intersection(status.json())

        finalized = client.post(
            f"/api/v1/artifact-uploads/{upload_id}/finalize", headers=auth_headers
        )
        assert finalized.status_code == 200
        assert finalized.json()["state"] == "ingesting"
        assert finalized.json()["verified_size"] == len(payload)
        assert (
            finalized.json()["verified_sha256"] == hashlib.sha256(payload).hexdigest()
        )

        rows = db_session.exec(
            select(ArtifactUploadPart).where(ArtifactUploadPart.session_id == upload_id)
        ).all()
        assert len(rows) == 1
        completed = client.get(
            f"/api/v1/artifact-uploads/{upload_id}", headers=auth_headers
        )
        assert completed.json()["state"] == "completed"
        artifact = db_session.exec(
            select(File).where(File.sha256 == hashlib.sha256(payload).hexdigest())
        ).one()
        assert db_session.get(Model, artifact.model_id) is not None

    def test_gcode_uses_the_normal_ingestion_pipeline(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        tmp_path,
    ) -> None:
        use_local_storage(tmp_path)
        payload = content.gcode()
        request = _request(payload) | {
            "purpose": "gcode",
            "filename": "calibration.gcode",
        }
        upload_id = client.post(
            "/api/v1/artifact-uploads", json=request, headers=auth_headers
        ).json()["id"]
        assert _put_chunk(client, auth_headers, upload_id, payload).status_code == 200

        response = client.post(
            f"/api/v1/artifact-uploads/{upload_id}/finalize", headers=auth_headers
        )

        assert response.status_code == 200
        completed = client.get(
            f"/api/v1/artifact-uploads/{upload_id}", headers=auth_headers
        ).json()
        assert completed["state"] == "completed"
        artifact = db_session.exec(
            select(File).where(File.sha256 == hashlib.sha256(payload).hexdigest())
        ).one()
        assert artifact.file_type == FileType.GCODE

    def test_revision_attaches_to_the_authorized_model(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        make_model,
        tmp_path,
    ) -> None:
        use_local_storage(tmp_path)
        model = make_model(name="Revision target")
        payload = content.gcode(marker="resumable revision")
        request = _request(payload) | {
            "purpose": "revision",
            "target_role": "model_revision",
            "target_id": str(model.id),
            "filename": "revision.gcode",
            "revision_label": "Resumable",
            "revision_status": "needs_test",
        }
        upload_id = client.post(
            "/api/v1/artifact-uploads", json=request, headers=auth_headers
        ).json()["id"]
        assert _put_chunk(client, auth_headers, upload_id, payload).status_code == 200

        response = client.post(
            f"/api/v1/artifact-uploads/{upload_id}/finalize", headers=auth_headers
        )

        assert response.status_code == 200
        completed = client.get(
            f"/api/v1/artifact-uploads/{upload_id}", headers=auth_headers
        ).json()
        assert completed["state"] == "completed"
        artifact = db_session.exec(
            select(File).where(File.sha256 == hashlib.sha256(payload).hexdigest())
        ).one()
        assert artifact.model_id == model.id
        assert artifact.revision_label == "Resumable"
        assert artifact.revision_status == FileRevisionStatus.NEEDS_TEST

    def test_revision_requires_an_existing_target(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
    ) -> None:
        payload = content.gcode()
        request = _request(payload) | {
            "purpose": "revision",
            "target_role": "model_revision",
            "target_id": "999999",
            "filename": "revision.gcode",
        }

        response = client.post(
            "/api/v1/artifact-uploads", json=request, headers=auth_headers
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "model_not_found"

    def test_unrelated_user_cannot_access_an_upload(
        self,
        client: TestClient,
        user_headers,
    ) -> None:
        owner_headers = user_headers("upload-owner", is_superuser=True)
        other_headers = user_headers("upload-stranger")
        payload = b"private"
        created = client.post(
            "/api/v1/artifact-uploads", json=_request(payload), headers=owner_headers
        )
        upload_id = created.json()["id"]

        assert (
            client.get(
                f"/api/v1/artifact-uploads/{upload_id}", headers=other_headers
            ).status_code
            == 404
        )
        assert _put_chunk(client, other_headers, upload_id, payload).status_code == 404
        assert (
            client.delete(
                f"/api/v1/artifact-uploads/{upload_id}", headers=other_headers
            ).status_code
            == 404
        )

    def test_conflicting_duplicate_preserves_receipt(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
    ) -> None:
        original = b"first"
        replacement = b"other"
        created = client.post(
            "/api/v1/artifact-uploads", json=_request(original), headers=auth_headers
        )
        upload_id = created.json()["id"]
        assert _put_chunk(client, auth_headers, upload_id, original).status_code == 200

        conflict = _put_chunk(client, auth_headers, upload_id, replacement)

        assert conflict.status_code == 409
        status = client.get(
            f"/api/v1/artifact-uploads/{upload_id}", headers=auth_headers
        ).json()
        assert status["received_bytes"] == len(original)
        assert status["parts"][0]["sha256"] == hashlib.sha256(original).hexdigest()

    def test_abort_is_idempotent(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        payload = b"cancel"
        upload_id = client.post(
            "/api/v1/artifact-uploads", json=_request(payload), headers=auth_headers
        ).json()["id"]
        assert _put_chunk(client, auth_headers, upload_id, payload).status_code == 200

        first = client.delete(
            f"/api/v1/artifact-uploads/{upload_id}", headers=auth_headers
        )
        second = client.delete(
            f"/api/v1/artifact-uploads/{upload_id}", headers=auth_headers
        )

        assert first.status_code == second.status_code == 200
        assert second.json()["state"] == "aborted"
        upload = db_session.get(ArtifactUploadSession, upload_id)
        assert upload is not None
        assert not db_session.exec(
            select(ArtifactUploadPart).where(ArtifactUploadPart.session_id == upload_id)
        ).all()
