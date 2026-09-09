"""Defend persistence and receipt ownership for durable artifact uploads.

A failure here means resumable state may be lost or orphaned across requests.
"""

from sqlmodel import Session, select

from app.db.models import ArtifactUploadPart, ArtifactUploadSession, ArtifactUploadState


class TestArtifactUploadSession:
    def test_persists_resumable_envelope(
        self,
        db_session: Session,
        make_user,
        make_artifact_upload,
        make_artifact_upload_part,
    ) -> None:
        owner = make_user("upload-owner")
        upload = make_artifact_upload(
            owner,
            state=ArtifactUploadState.UPLOADING,
            purpose="revision",
            target_role="model_revision",
            target_id="42",
            filename="calibration.gcode",
            media_type="text/x-gcode",
            declared_size=128,
            client_sha256="a" * 64,
            destination_ref="target-v1:credential-free",
            protected_native_id="encrypted-payload",
            received_bytes=64,
            retryable=True,
        )
        part = make_artifact_upload_part(
            upload,
            byte_offset=0,
            size_bytes=64,
            sha256="b" * 64,
            provider_receipt_json='{"etag":"opaque"}',
        )

        db_session.expire_all()
        loaded = db_session.get(ArtifactUploadSession, upload.id)
        loaded_part = db_session.exec(
            select(ArtifactUploadPart).where(ArtifactUploadPart.session_id == upload.id)
        ).one()

        assert loaded is not None
        assert (
            loaded.owner_user_id,
            loaded.purpose,
            loaded.target_role,
            loaded.target_id,
            loaded.filename,
            loaded.media_type,
            loaded.declared_size,
            loaded.client_sha256,
            loaded.state,
            loaded.adapter_id,
            loaded.destination_ref,
            loaded.protected_native_id,
            loaded.received_bytes,
            loaded.retryable,
        ) == (
            owner.id,
            "revision",
            "model_revision",
            "42",
            "calibration.gcode",
            "text/x-gcode",
            128,
            "a" * 64,
            ArtifactUploadState.UPLOADING,
            "api_chunks",
            "target-v1:credential-free",
            "encrypted-payload",
            64,
            True,
        )
        assert loaded_part.id == part.id
        assert (
            loaded_part.part_number,
            loaded_part.byte_offset,
            loaded_part.size_bytes,
            loaded_part.sha256,
            loaded_part.provider_receipt_json,
        ) == (1, 0, 64, "b" * 64, '{"etag":"opaque"}')

    def test_deleting_upload_cascades_its_receipts(
        self,
        db_session: Session,
        make_user,
        make_artifact_upload,
        make_artifact_upload_part,
    ) -> None:
        upload = make_artifact_upload(make_user("cascade-owner"))
        part = make_artifact_upload_part(upload)
        part_id = part.id

        db_session.delete(upload)
        db_session.commit()

        assert db_session.get(ArtifactUploadPart, part_id) is None
