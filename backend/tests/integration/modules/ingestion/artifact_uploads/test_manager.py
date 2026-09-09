"""Defend durable upload-manager compare-and-set transitions.

A failure here means concurrent workers could overwrite newer upload state.
"""

from datetime import timedelta

import pytest
from sqlalchemy import update
from sqlmodel import Session

from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import ArtifactUploadSession, ArtifactUploadState
from app.modules.ingestion.artifact_uploads import (
    ArtifactUploadError,
    NativeMultipartError,
    SqlArtifactUploadManager,
    UploadRequest,
)
from app.modules.ingestion.artifact_uploads.api_chunks import CHUNK_SIZE
from app.modules.ingestion.artifact_uploads.contracts import ChunkReceipt
from app.modules.storage.storage_backend.contracts import (
    NativeMultipartCapability,
    NativeMultipartHandle,
    NativeMultipartPart,
)


class _NativeBackend:
    storage_target = None

    def __init__(self, *, fail_begin: bool = False) -> None:
        self.fail_begin = fail_begin
        self.native_multipart_capability = NativeMultipartCapability(
            part_size=4, max_parts=10
        )
        self.handles: list[NativeMultipartHandle] = []
        self.remote_parts: list[NativeMultipartPart] = []
        self.completed = False

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

    def list_native_multipart_parts(self, _handle):
        return self.remote_parts

    def complete_native_multipart(self, _handle, _parts):
        self.completed = True
        raise AssertionError("mismatched receipts must not be completed")


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
    def test_rejects_inactive_sessions_at_every_transfer_boundary(
        self, db_session: Session, make_user, make_artifact_upload, tmp_path
    ) -> None:
        owner = make_user("inactive-upload-owner")
        manager = SqlArtifactUploadManager(db_session, staging_root=tmp_path)
        expired = make_artifact_upload(
            owner,
            expires_at=utcnow() - timedelta(seconds=1),
        )
        with pytest.raises(ArtifactUploadError, match="artifact_upload_expired"):
            manager.plan(expired.id, owner)

        completed = make_artifact_upload(
            owner,
            state=ArtifactUploadState.COMPLETED,
        )
        with pytest.raises(ArtifactUploadError, match="state_conflict"):
            manager.plan(completed.id, owner)
        with pytest.raises(ArtifactUploadError, match="state_conflict"):
            manager.abort(completed.id, owner)

    def test_rejects_transfer_mode_plus_state_conflicts(
        self, db_session: Session, make_user, make_artifact_upload, tmp_path
    ) -> None:
        owner = make_user("transfer-guard-owner")
        manager = SqlArtifactUploadManager(
            db_session,
            staging_root=tmp_path,
            backend=_NativeBackend(),  # type: ignore[arg-type]
        )
        receipt = ChunkReceipt(index=0, offset=0, size_bytes=4, sha256="a" * 64)
        native = make_artifact_upload(owner, adapter_id="native_parts")
        with pytest.raises(ArtifactUploadError, match="mode_conflict"):
            manager.record_chunk(native.id, receipt, b"data", owner)

        verifying = make_artifact_upload(
            owner,
            state=ArtifactUploadState.VERIFYING,
            adapter_id="api_chunks",
        )
        with pytest.raises(ArtifactUploadError, match="state_conflict"):
            manager.record_chunk(verifying.id, receipt, b"data", owner)

        native_verifying = make_artifact_upload(
            owner,
            state=ArtifactUploadState.VERIFYING,
            adapter_id="native_parts",
        )
        with pytest.raises(ArtifactUploadError, match="state_conflict"):
            manager.sign_native_part(
                native_verifying.id,
                part_number=1,
                checksum_sha256="a" * 64,
                actor=owner,
            )
        with pytest.raises(ArtifactUploadError, match="state_conflict"):
            manager.record_native_part(
                native_verifying.id,
                part_number=1,
                size_bytes=4,
                checksum_sha256="a" * 64,
                etag='"part-1"',
                actor=owner,
            )

    def test_rejects_a_conflicting_native_receipt_retry(
        self, db_session: Session, make_user, tmp_path
    ) -> None:
        backend = _NativeBackend()
        manager = SqlArtifactUploadManager(
            db_session,
            staging_root=tmp_path,
            backend=backend,  # type: ignore[arg-type]
        )
        owner = make_user("native-retry-owner")
        upload = manager.create(_upload_request(), owner)
        manager.record_native_part(
            upload.id,
            part_number=1,
            size_bytes=4,
            checksum_sha256="a" * 64,
            etag='"first"',
            actor=owner,
        )

        with pytest.raises(ArtifactUploadError, match="chunk_conflict"):
            manager.record_native_part(
                upload.id,
                part_number=1,
                size_bytes=4,
                checksum_sha256="a" * 64,
                etag='"second"',
                actor=owner,
            )

    def test_refuses_a_native_plan_after_capability_disappears(
        self, db_session: Session, make_user, tmp_path
    ) -> None:
        backend = _NativeBackend()
        manager = SqlArtifactUploadManager(
            db_session,
            staging_root=tmp_path,
            backend=backend,  # type: ignore[arg-type]
        )
        owner = make_user("native-capability-owner")
        upload = manager.create(_upload_request(), owner)
        backend.native_multipart_capability = None

        with pytest.raises(ArtifactUploadError, match="capability_unavailable"):
            manager.plan(upload.id, owner)

    def test_a_retryable_failure_can_request_a_fresh_plan(
        self, db_session: Session, make_user, make_artifact_upload, tmp_path
    ) -> None:
        owner = make_user("retry-owner")
        upload = make_artifact_upload(
            owner,
            state=ArtifactUploadState.FAILED,
            retryable=True,
            adapter_id="api_chunks",
        )

        plan = SqlArtifactUploadManager(db_session, staging_root=tmp_path).plan(
            upload.id, owner
        )

        db_session.refresh(upload)
        assert plan.mode == "api_chunks"
        assert upload.state == ArtifactUploadState.UPLOADING

    def test_reserves_worst_case_api_staging_before_accepting_bytes(
        self, db_session: Session, make_user, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setitem(_overlay, "staging_max_gb", 1)
        monkeypatch.setitem(_overlay, "max_upload_mb", 700)
        request = UploadRequest(
            purpose="model",
            target_role="new_model",
            target_id=None,
            filename="large.stl",
            media_type="model/stl",
            size_bytes=600 * 1024 * 1024,
            client_sha256=None,
            options={},
        )

        with pytest.raises(ArtifactUploadError, match="staging_capacity_exceeded"):
            SqlArtifactUploadManager(db_session, staging_root=tmp_path).create(
                request, make_user("capacity-owner")
            )

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

    def test_external_writeback_uses_the_resumable_api_family(
        self, db_session: Session, make_user, tmp_path
    ) -> None:
        manager = SqlArtifactUploadManager(
            db_session,
            staging_root=tmp_path,
            backend=_NativeBackend(),  # type: ignore[arg-type]
        )
        owner = make_user("writeback-owner")
        request = UploadRequest(
            purpose="external_writeback",
            target_role="external_library",
            target_id="7",
            filename="part.stl",
            media_type="model/stl",
            size_bytes=CHUNK_SIZE + 1,
            client_sha256=None,
            options={"target_library_id": 7},
        )

        upload = manager.create(request, owner)

        assert upload.adapter_id == "api_chunks"
        assert manager.plan(upload.id, owner).mode == "api_chunks"

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

        assert upload.adapter_id == "simple"
        assert manager.plan(upload.id, owner).mode == "simple"

    def test_refuses_native_completion_when_provider_receipts_disagree(
        self, db_session: Session, make_user, tmp_path
    ) -> None:
        backend = _NativeBackend()
        manager = SqlArtifactUploadManager(
            db_session,
            staging_root=tmp_path,
            backend=backend,  # type: ignore[arg-type]
        )
        owner = make_user("receipt-owner")
        upload = manager.create(_upload_request(), owner)
        for part_number in (1, 2):
            manager.record_native_part(
                upload.id,
                part_number=part_number,
                size_bytes=4,
                checksum_sha256=str(part_number) * 64,
                etag=f'"durable-{part_number}"',
                actor=owner,
            )
        backend.remote_parts = [
            NativeMultipartPart(1, 4, "1" * 64, '"different"'),
            NativeMultipartPart(2, 4, "2" * 64, '"durable-2"'),
        ]

        with pytest.raises(
            NativeMultipartError, match="native_upload_receipts_mismatch"
        ):
            manager.finalize(upload.id, owner)

        assert backend.completed is False
        db_session.refresh(upload)
        assert upload.state == ArtifactUploadState.FAILED

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
