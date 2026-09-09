"""Defend the authenticated, provider-neutral resumable upload HTTP protocol.

A failure here means clients may lose resumability, isolation, or ingestion handoff.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from starlette.requests import Request

from app.api.v1 import artifact_uploads as upload_api
from app.db.models import (
    ArtifactUploadPart,
    ArtifactUploadSession,
    ArtifactUploadState,
    AuditLog,
    CollectionPermission,
    CollectionRole,
    File,
    FileRevisionStatus,
    FileType,
    Model,
)
from app.modules.ingestion.artifact_uploads import (
    ArtifactUploadError,
    SqlArtifactUploadManager,
)
from app.modules.ingestion.artifact_uploads.api_chunks import CHUNK_SIZE, ApiChunkError
from app.modules.storage.storage_backend.contracts import (
    CreationReceipt,
    NativeMultipartCapability,
    NativeMultipartHandle,
    NativeMultipartPart,
)
from app.schemas.artifact_uploads import ArtifactUploadCreate
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


class _NativeUploadBackend:
    storage_target = None

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.native_multipart_capability = NativeMultipartCapability(
            part_size=max(1, len(payload) // 2), max_parts=10
        )
        self.remote_parts: list[NativeMultipartPart] = []
        self.signed: list[int] = []
        self.cleaned = False

    def begin_native_multipart(self, *, session_id: str, **_kwargs):
        return NativeMultipartHandle(
            key=f"private/{session_id}",
            upload_id="opaque-native-id",
            ownership_token="owned-token",
        )

    def sign_native_multipart_part(
        self, _handle, *, part_number: int, **_kwargs
    ) -> str:
        self.signed.append(part_number)
        return f"https://upload.example.test/part/{part_number}?signature=redacted"

    def list_native_multipart_parts(self, _handle):
        return self.remote_parts

    def complete_native_multipart(self, handle, _parts):
        return CreationReceipt(
            key=handle.key,
            size=len(self.payload),
            token=handle.ownership_token,
            backend="fake",
            namespace="private",
            etag='"complete"',
            version_id="v1",
        )

    def download_to_path(self, _key: str, destination: Path) -> Path:
        destination.write_bytes(self.payload)
        return destination

    def rollback_create(self, _receipt: CreationReceipt) -> bool:
        self.cleaned = True
        return True

    def abort_native_multipart(self, _handle) -> None:
        self.cleaned = True


class TestArtifactUploads:
    def test_validates_file_types_for_each_upload_purpose(self) -> None:
        upload_api._validate_purpose_file(
            ArtifactUploadCreate(
                purpose="archive",
                target_role="new_model",
                filename="bundle.zip",
                size_bytes=1,
            )
        )
        upload_api._validate_purpose_file(
            ArtifactUploadCreate(
                purpose="browser_capture",
                target_role="new_model",
                filename="capture.bin",
                size_bytes=1,
            )
        )

        with pytest.raises(HTTPException) as raised:
            upload_api._validate_purpose_file(
                ArtifactUploadCreate(
                    purpose="archive",
                    target_role="new_model",
                    filename="bundle.stl",
                    size_bytes=1,
                )
            )

        assert raised.value.status_code == 400
        assert raised.value.detail == "unsupported_file_type"

    @pytest.mark.parametrize(
        ("error", "status_code"),
        [
            (ArtifactUploadError("artifact_upload_expired"), 410),
            (ArtifactUploadError("staging_capacity_exceeded"), 507),
            (ApiChunkError("artifact_upload_chunk_length_invalid"), 422),
        ],
    )
    def test_maps_protocol_errors_to_stable_http_statuses(
        self, error: Exception, status_code: int
    ) -> None:
        assert upload_api._translate_error(error).status_code == status_code

    def test_rejects_missing_plus_malformed_revision_targets(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        payload = content.gcode()
        base = _request(payload) | {
            "purpose": "revision",
            "filename": "revision.gcode",
        }

        missing = client.post(
            "/api/v1/artifact-uploads",
            json=base,
            headers=auth_headers,
        )
        malformed = client.post(
            "/api/v1/artifact-uploads",
            json=base | {"target_role": "model_revision", "target_id": "not-an-id"},
            headers=auth_headers,
        )

        assert missing.status_code == 422
        assert missing.json()["detail"] == "revision_target_required"
        assert malformed.status_code == 422
        assert malformed.json()["detail"] == "revision_target_invalid"

    def test_missing_plan_plus_native_operations_return_protocol_errors(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        assert (
            client.get(
                "/api/v1/artifact-uploads/missing/plan", headers=auth_headers
            ).status_code
            == 404
        )
        payload = b"proxy-only"
        upload_id = client.post(
            "/api/v1/artifact-uploads", json=_request(payload), headers=auth_headers
        ).json()["id"]
        signed = client.post(
            f"/api/v1/artifact-uploads/{upload_id}/parts/1/sign",
            json={"checksum_sha256": "a" * 64},
            headers=auth_headers,
        )
        recorded = client.post(
            f"/api/v1/artifact-uploads/{upload_id}/parts/1",
            json={
                "size_bytes": len(payload),
                "checksum_sha256": "a" * 64,
                "etag": '"part-1"',
            },
            headers=auth_headers,
        )

        assert signed.status_code == 422
        assert recorded.status_code == 422

    def test_rejects_a_non_numeric_content_length(
        self,
    ) -> None:
        request = Request(
            {"type": "http", "headers": [(b"content-length", b"invalid")]}
        )
        with pytest.raises(HTTPException) as raised:
            asyncio.run(
                upload_api.put_artifact_upload_chunk(
                    "upload-1",
                    0,
                    request,
                    offset=0,
                    length=1,
                    sha256="a" * 64,
                    current_user=None,  # type: ignore[arg-type]
                    session=None,  # type: ignore[arg-type]
                )
            )

        assert raised.value.status_code == 400
        assert raised.value.detail == "content_length_invalid"

    def test_rejects_an_oversized_declared_content_length(self) -> None:
        request = Request(
            {
                "type": "http",
                "headers": [(b"content-length", str(CHUNK_SIZE + 1).encode())],
            }
        )

        with pytest.raises(HTTPException) as raised:
            asyncio.run(
                upload_api.put_artifact_upload_chunk(
                    "upload-1",
                    0,
                    request,
                    offset=0,
                    length=1,
                    sha256="a" * 64,
                    current_user=None,  # type: ignore[arg-type]
                    session=None,  # type: ignore[arg-type]
                )
            )

        assert raised.value.status_code == 413
        assert raised.value.detail == "upload_chunk_too_large"

    def test_rejects_a_stream_that_exceeds_the_chunk_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def receive() -> dict[str, object]:
            return {"type": "http.request", "body": b"too-large", "more_body": False}

        monkeypatch.setattr(upload_api, "CHUNK_SIZE", 1)
        request = Request({"type": "http", "headers": []}, receive)

        with pytest.raises(HTTPException) as raised:
            asyncio.run(
                upload_api.put_artifact_upload_chunk(
                    "upload-1",
                    0,
                    request,
                    offset=0,
                    length=1,
                    sha256="a" * 64,
                    current_user=None,  # type: ignore[arg-type]
                    session=None,  # type: ignore[arg-type]
                )
            )

        assert raised.value.status_code == 413
        assert raised.value.detail == "upload_chunk_too_large"

    def test_create_is_idempotent_for_the_same_client_key(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        headers = auth_headers | {"Idempotency-Key": "artifact-upload:test-key"}
        first = client.post(
            "/api/v1/artifact-uploads", json=_request(b"x"), headers=headers
        )
        second = client.post(
            "/api/v1/artifact-uploads", json=_request(b"x"), headers=headers
        )

        assert first.status_code == second.status_code == 201
        assert first.json()["id"] == second.json()["id"]
        assert len(db_session.exec(select(ArtifactUploadSession)).all()) == 1

    def test_rejects_an_oversized_session_before_accepting_bytes(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        request = _request(b"x") | {"size_bytes": 512 * 1024 * 1024 + 1}

        response = client.post(
            "/api/v1/artifact-uploads", json=request, headers=auth_headers
        )

        assert response.status_code == 413
        assert response.json()["detail"] == "upload_too_large"

    def test_rejects_a_chunk_body_larger_than_the_protocol_bound(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        payload = b"x"
        upload_id = client.post(
            "/api/v1/artifact-uploads", json=_request(payload), headers=auth_headers
        ).json()["id"]

        response = client.put(
            f"/api/v1/artifact-uploads/{upload_id}/chunks/0",
            params={
                "offset": 0,
                "length": 1,
                "sha256": hashlib.sha256(payload).hexdigest(),
            },
            content=payload,
            headers=auth_headers | {"content-length": str(CHUNK_SIZE + 1)},
        )

        assert response.status_code == 413
        assert response.json()["detail"] == "upload_chunk_too_large"

    def test_rate_limits_repeated_session_creation(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        responses = [
            client.post(
                "/api/v1/artifact-uploads", json=_request(b"x"), headers=auth_headers
            )
            for _ in range(31)
        ]

        assert responses[-1].status_code == 429
        assert responses[-1].json()["detail"] == "rate_limited"

    def test_native_parts_bypass_the_chunk_request_body(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        tmp_path,
        monkeypatch,
    ) -> None:
        use_local_storage(tmp_path)
        payload = content.binary_stl() + b"padding"
        backend = _NativeUploadBackend(payload)
        monkeypatch.setattr(
            "app.modules.ingestion.artifact_uploads.manager.get_backend",
            lambda: backend,
        )
        request = _request(payload)
        created = client.post(
            "/api/v1/artifact-uploads", json=request, headers=auth_headers
        )
        upload_id = created.json()["id"]
        assert created.json()["mode"] == "native_parts"

        part_size = backend.native_multipart_capability.part_size
        chunks = [
            payload[offset : offset + part_size]
            for offset in range(0, len(payload), part_size)
        ]
        for part_number, chunk in enumerate(chunks, start=1):
            checksum = hashlib.sha256(chunk).hexdigest()
            signed = client.post(
                f"/api/v1/artifact-uploads/{upload_id}/parts/{part_number}/sign",
                json={"checksum_sha256": checksum},
                headers=auth_headers,
            )
            assert signed.status_code == 200
            assert signed.headers["cache-control"] == "no-store"
            assert "signature" not in str(created.json()).lower()
            etag = f'"part-{part_number}"'
            backend.remote_parts.append(
                NativeMultipartPart(part_number, len(chunk), checksum, etag)
            )
            recorded = client.post(
                f"/api/v1/artifact-uploads/{upload_id}/parts/{part_number}",
                json={
                    "size_bytes": len(chunk),
                    "checksum_sha256": checksum,
                    "etag": etag,
                },
                headers=auth_headers,
            )
            assert recorded.status_code == 200

        finalized = client.post(
            f"/api/v1/artifact-uploads/{upload_id}/finalize", headers=auth_headers
        )

        assert finalized.status_code == 200
        completed = client.get(
            f"/api/v1/artifact-uploads/{upload_id}", headers=auth_headers
        ).json()
        assert completed["state"] == "completed"
        assert backend.cleaned is True
        artifact = db_session.exec(
            select(File).where(File.sha256 == hashlib.sha256(payload).hexdigest())
        ).one()
        assert artifact.size_bytes == len(payload)

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
        assert created.json()["mode"] == "simple"

        plan = client.get(
            f"/api/v1/artifact-uploads/{upload_id}/plan", headers=auth_headers
        )
        assert plan.status_code == 200
        assert plan.headers["cache-control"] == "no-store"
        assert plan.json()["mode"] == "simple"
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
        repeated = client.post(
            f"/api/v1/artifact-uploads/{upload_id}/finalize", headers=auth_headers
        )
        assert repeated.status_code == 200
        assert repeated.json()["state"] == "completed"
        assert repeated.json()["job_id"] == finalized.json()["job_id"]
        artifact = db_session.exec(
            select(File).where(File.sha256 == hashlib.sha256(payload).hexdigest())
        ).one()
        assert db_session.get(Model, artifact.model_id) is not None
        actions = {
            row.action
            for row in db_session.exec(
                select(AuditLog).where(AuditLog.resource_type == "artifact_upload")
            ).all()
        }
        assert {"artifact_upload.create", "artifact_upload.finalize"} <= actions

    def test_concurrent_duplicate_finalize_returns_the_winners_state(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        tmp_path,
        monkeypatch,
    ) -> None:
        use_local_storage(tmp_path)
        payload = content.binary_stl()
        upload_id = client.post(
            "/api/v1/artifact-uploads", json=_request(payload), headers=auth_headers
        ).json()["id"]
        assert _put_chunk(client, auth_headers, upload_id, payload).status_code == 200
        transition = SqlArtifactUploadManager.transition
        raced = False

        def concurrent_claim(self, upload, target, **changes):
            nonlocal raced
            if target == ArtifactUploadState.INGESTING and not changes and not raced:
                raced = True
                upload.state = ArtifactUploadState.INGESTING
                upload.version += 1
                self.session.add(upload)
                self.session.commit()
                raise ArtifactUploadError("artifact_upload_state_conflict")
            return transition(self, upload, target, **changes)

        monkeypatch.setattr(SqlArtifactUploadManager, "transition", concurrent_claim)

        response = client.post(
            f"/api/v1/artifact-uploads/{upload_id}/finalize", headers=auth_headers
        )

        assert response.status_code == 200
        assert response.json()["state"] == "ingesting"
        assert response.json()["job_id"] is None

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

    def test_simple_slicer_client_needs_only_canonical_http_state(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        tmp_path,
    ) -> None:
        use_local_storage(tmp_path)
        payload = content.gcode(marker="simple slicer client")
        request = _request(payload) | {
            "purpose": "slicer",
            "filename": "slicer-output.gcode",
        }
        created = client.post(
            "/api/v1/artifact-uploads", json=request, headers=auth_headers
        )
        upload_id = created.json()["id"]

        assert created.status_code == 201
        assert _put_chunk(client, auth_headers, upload_id, payload).status_code == 200
        finalized = client.post(
            f"/api/v1/artifact-uploads/{upload_id}/finalize", headers=auth_headers
        )

        assert finalized.status_code == 200
        assert finalized.json()["job_id"]
        assert (
            client.get(
                f"/api/v1/artifact-uploads/{upload_id}", headers=auth_headers
            ).json()["state"]
            == "completed"
        )
        assert db_session.exec(
            select(File).where(File.sha256 == hashlib.sha256(payload).hexdigest())
        ).one()

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

    def test_hash_mismatch_creates_no_ingestion_job(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        tmp_path,
    ) -> None:
        use_local_storage(tmp_path)
        payload = content.binary_stl()
        request = _request(payload) | {"sha256": "0" * 64}
        upload_id = client.post(
            "/api/v1/artifact-uploads", json=request, headers=auth_headers
        ).json()["id"]
        assert _put_chunk(client, auth_headers, upload_id, payload).status_code == 200

        response = client.post(
            f"/api/v1/artifact-uploads/{upload_id}/finalize", headers=auth_headers
        )

        assert response.status_code == 422
        db_session.expire_all()
        upload = db_session.get(ArtifactUploadSession, upload_id)
        assert upload is not None
        assert str(upload.state) == "failed"
        assert upload.background_job_id is None
        assert not db_session.exec(
            select(File).where(File.sha256 == hashlib.sha256(payload).hexdigest())
        ).all()

    def test_finalize_rechecks_revision_authorization(
        self,
        client: TestClient,
        db_session: Session,
        make_user,
        make_collection,
        make_model,
        headers_for,
        grant_role,
        tmp_path,
    ) -> None:
        use_local_storage(tmp_path)
        editor = make_user("revoked-upload-editor")
        collection = make_collection("Restricted")
        grant_role(editor, collection, CollectionRole.EDIT)
        model = make_model("Restricted model", collection=collection)
        headers = headers_for(editor)
        payload = content.gcode()
        request = _request(payload) | {
            "purpose": "revision",
            "target_role": "model_revision",
            "target_id": str(model.id),
            "filename": "revision.gcode",
        }
        upload_id = client.post(
            "/api/v1/artifact-uploads", json=request, headers=headers
        ).json()["id"]
        assert _put_chunk(client, headers, upload_id, payload).status_code == 200
        permission = db_session.exec(
            select(CollectionPermission).where(
                CollectionPermission.user_id == editor.id,
                CollectionPermission.collection_id == collection.id,
            )
        ).one()
        db_session.delete(permission)
        db_session.commit()

        response = client.post(
            f"/api/v1/artifact-uploads/{upload_id}/finalize", headers=headers
        )

        assert response.status_code == 403
        assert not db_session.exec(
            select(File).where(File.sha256 == hashlib.sha256(payload).hexdigest())
        ).all()

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
        assert db_session.exec(
            select(AuditLog).where(AuditLog.action == "artifact_upload.abort")
        ).first()
        assert not db_session.exec(
            select(ArtifactUploadPart).where(ArtifactUploadPart.session_id == upload_id)
        ).all()

    def test_concurrent_duplicate_abort_returns_the_winners_state(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        tmp_path,
        monkeypatch,
    ) -> None:
        use_local_storage(tmp_path)
        payload = b"cancel-race"
        upload_id = client.post(
            "/api/v1/artifact-uploads", json=_request(payload), headers=auth_headers
        ).json()["id"]
        assert _put_chunk(client, auth_headers, upload_id, payload).status_code == 200
        transition = SqlArtifactUploadManager.transition
        raced = False

        def concurrent_abort(self, upload, target, **changes):
            nonlocal raced
            if target == ArtifactUploadState.ABORTED and not raced:
                raced = True
                upload.state = ArtifactUploadState.ABORTED
                upload.version += 1
                self.session.add(upload)
                self.session.commit()
                raise ArtifactUploadError("artifact_upload_state_conflict")
            return transition(self, upload, target, **changes)

        monkeypatch.setattr(SqlArtifactUploadManager, "transition", concurrent_abort)

        response = client.delete(
            f"/api/v1/artifact-uploads/{upload_id}", headers=auth_headers
        )

        assert response.status_code == 200
        assert response.json()["state"] == "aborted"
