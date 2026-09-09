"""Defend durable upload-manager compare-and-set transitions.

A failure here means concurrent workers could overwrite newer upload state.
"""

import pytest
from sqlalchemy import update
from sqlmodel import Session

from app.db.models import ArtifactUploadSession, ArtifactUploadState
from app.modules.ingestion.artifact_uploads import (
    ArtifactUploadError,
    SqlArtifactUploadManager,
    UploadRequest,
)
from app.modules.storage.storage_backend.contracts import (
    NativeMultipartCapability,
    NativeMultipartHandle,
)


class _NativeBackend:
    storage_target = None

    def __init__(self, *, fail_begin: bool = False) -> None:
        self.fail_begin = fail_begin
        self.native_multipart_capability = NativeMultipartCapability(
            part_size=4, max_parts=10
        )
        self.handles: list[NativeMultipartHandle] = []

    def begin_native_multipart(self, *, session_id: str, **_kwargs):
        if self.fail_begin:
            raise RuntimeError("checksum unsupported")
        handle = NativeMultipartHandle(
            key=f"private/{session_id}",
            upload_id="provider-secret-id",
            ownership_token="owned-token",
        )
        self.handles.append(handle)
        return handle


def _upload_request() -> UploadRequest:
    return UploadRequest(
        purpose="model",
        target_role="new_model",
        target_id=None,
        filename="part.stl",
        media_type="model/stl",
        size_bytes=8,
        client_sha256=None,
        options={},
    )


class TestSqlArtifactUploadManager:
    def test_rejects_concurrent_finalize_claim(
        self, db_session: Session, make_user, make_artifact_upload, tmp_path
    ) -> None:
        owner = make_user("finalize-owner")
        upload = make_artifact_upload(
            owner,
            state=ArtifactUploadState.VERIFYING,
            error_code="artifact_upload_verification_active",
        )

        with pytest.raises(ArtifactUploadError, match="state_conflict"):
            SqlArtifactUploadManager(db_session, staging_root=tmp_path).finalize(
                upload.id, owner
            )

    def test_selects_native_mode_by_capability(
        self, db_session: Session, make_user, tmp_path
    ) -> None:
        backend = _NativeBackend()
        manager = SqlArtifactUploadManager(
            db_session,
            staging_root=tmp_path,
            backend=backend,  # type: ignore[arg-type]
        )
        owner = make_user("native-owner")

        upload = manager.create(_upload_request(), owner)

        assert upload.adapter_id == "native_parts"
        assert upload.protected_native_id is not None
        assert "provider-secret-id" not in upload.protected_native_id
        assert manager.plan(upload.id, owner).mode == "native_parts"

    def test_falls_back_when_native_initiation_lacks_guarantees(
        self, db_session: Session, make_user, tmp_path
    ) -> None:
        manager = SqlArtifactUploadManager(
            db_session,
            staging_root=tmp_path,
            backend=_NativeBackend(fail_begin=True),  # type: ignore[arg-type]
        )
        owner = make_user("fallback-owner")

        upload = manager.create(_upload_request(), owner)

        assert upload.adapter_id == "api_chunks"
        assert manager.plan(upload.id, owner).mode == "api_chunks"

    def test_rejects_a_stale_state_version(
        self,
        db_session: Session,
        make_user,
        make_artifact_upload,
    ) -> None:
        upload = make_artifact_upload(make_user("cas-owner"))
        db_session.expunge(upload)
        stale = upload
        db_session.execute(
            update(ArtifactUploadSession)
            .where(ArtifactUploadSession.id == upload.id)
            .values(state=ArtifactUploadState.UPLOADING.value, version=1)
        )
        db_session.commit()

        with pytest.raises(ArtifactUploadError, match="state_conflict"):
            SqlArtifactUploadManager(db_session).transition(
                stale, ArtifactUploadState.ABORTED
            )

        current = db_session.get(ArtifactUploadSession, stale.id)
        assert current is not None
        assert current.state == ArtifactUploadState.UPLOADING
        assert current.version == 1
