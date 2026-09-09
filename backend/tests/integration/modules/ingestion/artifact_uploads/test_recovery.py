"""Defend restart reconciliation and fail-closed upload expiry.

A failure here means a restart could strand state or delete staging it cannot own.
"""

from datetime import timedelta

from sqlmodel import Session

from app.core.config import settings
from app.core.time import utcnow
from app.db.models import ArtifactUploadState
from app.db.session import SQLiteSessionFactory
from app.modules.ingestion.artifact_uploads import reconcile_artifact_uploads
from tests._env import use_local_storage


class TestReconcileArtifactUploads:
    def test_expires_owned_staging(
        self,
        db_session: Session,
        make_user,
        make_artifact_upload,
        make_artifact_upload_part,
        tmp_path,
    ) -> None:
        use_local_storage(tmp_path)
        upload = make_artifact_upload(
            make_user("expired-upload-owner"),
            state=ArtifactUploadState.UPLOADING,
            expires_at=utcnow() - timedelta(seconds=1),
        )
        make_artifact_upload_part(upload, size_bytes=4)
        directory = settings.incoming_dir / "artifact-uploads" / upload.id
        directory.mkdir(parents=True)
        (directory / "00000000.chunk").write_bytes(b"part")

        result = reconcile_artifact_uploads(
            SQLiteSessionFactory(db_session.get_bind()), now=utcnow()
        )

        db_session.expire_all()
        assert result.expired == 1
        assert upload.state == ArtifactUploadState.EXPIRED
        assert upload.received_bytes == 0
        assert not directory.exists()

    def test_retains_unproven_staging(
        self,
        db_session: Session,
        make_user,
        make_artifact_upload,
        tmp_path,
    ) -> None:
        use_local_storage(tmp_path)
        upload = make_artifact_upload(
            make_user("retained-upload-owner"),
            state=ArtifactUploadState.UPLOADING,
            expires_at=utcnow() - timedelta(seconds=1),
        )
        directory = settings.incoming_dir / "artifact-uploads" / upload.id
        directory.mkdir(parents=True)
        foreign = directory / "unknown-entry"
        foreign.write_bytes(b"preserve")

        result = reconcile_artifact_uploads(
            SQLiteSessionFactory(db_session.get_bind()), now=utcnow()
        )

        db_session.expire_all()
        assert result.retained == 1
        assert upload.state == ArtifactUploadState.FAILED
        assert upload.error_code == "artifact_upload_cleanup_unproven"
        assert upload.retryable is True
        assert foreign.read_bytes() == b"preserve"

    def test_mirrors_an_interrupted_ingestion_job(
        self,
        db_session: Session,
        make_user,
        make_background_job,
        make_artifact_upload,
        tmp_path,
    ) -> None:
        use_local_storage(tmp_path)
        owner = make_user("interrupted-upload-owner")
        job = make_background_job(
            kind="artifact_upload_model",
            state="failed",
            owner=owner,
            status_json='{"retryable":true}',
        )
        upload = make_artifact_upload(
            owner,
            state=ArtifactUploadState.INGESTING,
            background_job_id=job.id,
        )

        result = reconcile_artifact_uploads(SQLiteSessionFactory(db_session.get_bind()))

        db_session.expire_all()
        assert result.reconciled == 1
        assert upload.state == ArtifactUploadState.FAILED
        assert upload.error_code == "artifact_upload_ingestion_interrupted"
        assert upload.retryable is True

    def test_preserves_replayable_verification(
        self,
        db_session: Session,
        make_user,
        make_artifact_upload,
        tmp_path,
    ) -> None:
        use_local_storage(tmp_path)
        upload = make_artifact_upload(
            make_user("verifying-upload-owner"),
            state=ArtifactUploadState.VERIFYING,
            expires_at=utcnow() + timedelta(hours=1),
        )

        result = reconcile_artifact_uploads(SQLiteSessionFactory(db_session.get_bind()))

        db_session.expire_all()
        assert result == result.__class__()
        assert upload.state == ArtifactUploadState.VERIFYING

    def test_marks_an_active_verification_as_interrupted(
        self,
        db_session: Session,
        make_user,
        make_artifact_upload,
        tmp_path,
    ) -> None:
        use_local_storage(tmp_path)
        upload = make_artifact_upload(
            make_user("active-verification-owner"),
            state=ArtifactUploadState.VERIFYING,
            error_code="artifact_upload_verification_active",
            retryable=False,
            expires_at=utcnow() + timedelta(hours=1),
        )

        result = reconcile_artifact_uploads(SQLiteSessionFactory(db_session.get_bind()))

        db_session.expire_all()
        assert result.reconciled == 1
        assert upload.state == ArtifactUploadState.VERIFYING
        assert upload.error_code == "artifact_upload_verification_interrupted"
        assert upload.retryable is True
