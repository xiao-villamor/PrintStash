"""A verified resumable upload becomes an Artifact exactly through its Job.

Finalizing an upload records a queued ``ingestion.artifact_upload`` Job that owns the
verified staged bytes; the Job commits them (a new Model's Artifact, or a revision
of an existing Model) and mirrors its outcome onto the upload session. If this goes
red, a verified upload can be left ``ingesting`` forever, a failed commit can report
success to the client, or the staged bytes can outlive the upload they belonged to.

Cancelling or losing the Job fails an upload that is still ingesting (a lost Job
retryably, since the client can upload again) and never touches a settled one. An
hourly occurrence expires abandoned uploads.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.core.config import settings
from app.db.models import (
    ArtifactUploadSession,
    ArtifactUploadState,
    File,
    FileType,
    Job,
    JobKind,
    JobState,
    StagingLease,
    User,
)
from app.db.session import get_session_factory
from app.modules.ingestion import staging_leases
from app.modules.ingestion.artifact_uploads import handoff
from app.modules.work import service as work_service
from app.modules.work.jobs import jobs
from app.modules.work.submission import nudge, submit
from app.runtime.engine.inline import InlineJobEngine
from tests._env import use_local_storage
from tests.factories import build_artifact_upload
from tests.factories.ops import build_job_context
from tests.factories.protocols import MakeJob, MakeModel, MakeUser

RECOVERY, UPLOAD = handoff.definitions()
STL = b"solid handoff\nendsolid handoff\n"
GCODE = b"; handoff revision\nG28\n"


@pytest.fixture
def owner(make_user: MakeUser, tmp_path: Path) -> User:
    use_local_storage(tmp_path)
    return make_user("handoff-owner", superuser=True)


def _verified(
    session: Session,
    make_job: MakeJob,
    owner: User,
    *,
    content: bytes = STL,
    stage: bool = True,
    **upload: object,
) -> tuple[ArtifactUploadSession, str]:
    """An upload whose bytes verified and whose finalize queued the Job."""
    row = build_artifact_upload(
        session, owner, state=ArtifactUploadState.INGESTING, **upload
    )
    job = make_job(
        kind=JobKind.INGESTION_ARTIFACT_UPLOAD,
        owner=owner,
        subject=handoff.subject_key(row.id),
    )
    row.job_id = job.id
    session.add(row)
    session.commit()
    if stage:
        staged = (
            settings.incoming_dir / "artifact-uploads" / row.id / "assembled.upload"
        )
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_bytes(content)
        staging_leases.create_job_lease(
            session,
            job_id=job.id,
            owner_user_id=owner.id,
            path=staged,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            check_capacity=False,
        )
        session.commit()
    return row, job.id


def _settled(session: Session, make_job: MakeJob, owner: User) -> ArtifactUploadSession:
    """An upload whose ingestion already finished."""
    upload, _job_id = _verified(session, make_job, owner)
    upload.state = ArtifactUploadState.COMPLETED
    session.add(upload)
    session.commit()
    return upload


def _run(engine: InlineJobEngine, session: Session) -> None:
    nudge(JobKind.INGESTION_ARTIFACT_UPLOAD)
    engine.drain()
    session.expire_all()


class TestRunVerifiedUploadIngestion:
    def test_commits_a_verified_model_upload_as_an_artifact(
        self,
        db_session: Session,
        make_job: MakeJob,
        owner: User,
        work_engine: InlineJobEngine,
    ) -> None:
        _verified(db_session, make_job, owner, filename="bracket.stl")

        _run(work_engine, db_session)

        artifact = db_session.exec(
            select(File).where(File.original_filename == "bracket.stl")
        ).one()
        assert artifact.sha256 == hashlib.sha256(STL).hexdigest()

    @pytest.mark.parametrize("purpose", ["gcode", "slicer"])
    def test_commits_a_slicer_upload_as_gcode_whatever_its_suffix(
        self,
        db_session: Session,
        make_job: MakeJob,
        owner: User,
        work_engine: InlineJobEngine,
        purpose: str,
    ) -> None:
        _verified(
            db_session,
            make_job,
            owner,
            content=GCODE,
            purpose=purpose,
            filename="plate.upload",
        )

        _run(work_engine, db_session)

        artifact = db_session.exec(
            select(File).where(File.original_filename == "plate.upload")
        ).one()
        assert artifact.file_type == FileType.GCODE

    @pytest.mark.parametrize("empty_quarantine", [False, True])
    def test_completes_the_upload_session(
        self,
        db_session: Session,
        make_job: MakeJob,
        owner: User,
        work_engine: InlineJobEngine,
        empty_quarantine: bool,
    ) -> None:
        upload, job_id = _verified(db_session, make_job, owner)
        directory = settings.incoming_dir / "artifact-uploads" / upload.id
        if empty_quarantine:
            (directory / ".printstash-staging-quarantine").mkdir(mode=0o700)

        _run(work_engine, db_session)

        assert db_session.get(ArtifactUploadSession, upload.id).state == (
            ArtifactUploadState.COMPLETED
        )
        assert db_session.get(Job, job_id).state == JobState.COMPLETED
        assert not directory.exists()

    def test_releases_the_staged_bytes_of_a_completed_upload(
        self,
        db_session: Session,
        make_job: MakeJob,
        owner: User,
        work_engine: InlineJobEngine,
    ) -> None:
        _upload, job_id = _verified(db_session, make_job, owner)

        _run(work_engine, db_session)

        assert (
            db_session.exec(
                select(StagingLease).where(StagingLease.job_id == job_id)
            ).all()
            == []
        )

    def test_attaches_a_revision_upload_to_its_model(
        self,
        db_session: Session,
        make_job: MakeJob,
        make_model: MakeModel,
        owner: User,
        work_engine: InlineJobEngine,
    ) -> None:
        model = make_model("Revised")
        _verified(
            db_session,
            make_job,
            owner,
            content=GCODE,
            purpose="revision",
            target_role="revision",
            target_id=str(model.id),
            filename="revised.gcode",
            request_json='{"revision_label":"faster"}',
        )

        _run(work_engine, db_session)

        revision = db_session.exec(
            select(File).where(
                File.model_id == model.id, File.file_type == FileType.GCODE
            )
        ).one()
        assert revision.revision_label == "faster"

    def test_fails_a_revision_for_a_trashed_model(
        self,
        db_session: Session,
        make_job: MakeJob,
        make_model: MakeModel,
        owner: User,
        work_engine: InlineJobEngine,
    ) -> None:
        model = make_model("Gone", trashed=True)
        _upload, job_id = _verified(
            db_session,
            make_job,
            owner,
            content=GCODE,
            purpose="revision",
            target_role="revision",
            target_id=str(model.id),
            filename="revised.gcode",
        )

        _run(work_engine, db_session)

        status = jobs.get(job_id)
        assert status is not None
        assert (status.state, status.error) == (
            "failed",
            "artifact_revision_ingestion_failed",
        )

    def test_fails_an_unsupported_purpose_without_a_retry(
        self,
        db_session: Session,
        make_job: MakeJob,
        owner: User,
        work_engine: InlineJobEngine,
    ) -> None:
        upload, _job_id = _verified(
            db_session,
            make_job,
            owner,
            purpose="external_writeback",
            filename="notes.txt",
        )

        _run(work_engine, db_session)

        row = db_session.get(ArtifactUploadSession, upload.id)
        assert (row.state, row.retryable) == (ArtifactUploadState.FAILED, False)

    def test_fails_the_job_when_the_staged_bytes_expired(
        self,
        db_session: Session,
        make_job: MakeJob,
        owner: User,
        work_engine: InlineJobEngine,
    ) -> None:
        _upload, job_id = _verified(db_session, make_job, owner, stage=False)

        _run(work_engine, db_session)

        status = jobs.get(job_id)
        assert status is not None
        assert (status.state, status.error) == ("failed", "staging_expired")

    def test_ignores_a_session_that_no_longer_owns_ingestion(
        self, db_session: Session, make_job: MakeJob, owner: User
    ) -> None:
        upload, job_id = _verified(db_session, make_job, owner)
        upload.state = ArtifactUploadState.FAILED
        db_session.add(upload)
        db_session.commit()

        handoff.run_verified_upload_ingestion(
            upload_id=upload.id,
            job_context=build_job_context(job_id),
            session_factory=get_session_factory(),
        )

        assert (
            db_session.exec(
                select(File).where(File.original_filename == upload.filename)
            ).all()
            == []
        )


class TestCancel:
    def test_cancelling_the_job_fails_the_upload(
        self, db_session: Session, make_job: MakeJob, owner: User
    ) -> None:
        upload, job_id = _verified(db_session, make_job, owner)

        work_service.cancel(job_id, actor=owner)

        db_session.expire_all()
        row = db_session.get(ArtifactUploadSession, upload.id)
        assert (row.state, row.error_code) == (
            ArtifactUploadState.FAILED,
            "artifact_upload_cancelled",
        )

    def test_cancelling_the_job_marks_it_cancelled(
        self, db_session: Session, make_job: MakeJob, owner: User
    ) -> None:
        _upload, job_id = _verified(db_session, make_job, owner)

        work_service.cancel(job_id, actor=owner)

        status = jobs.get(job_id)
        assert status is not None and status.state == JobState.CANCELLED.value

    def test_a_settled_upload_is_not_touched(
        self, db_session: Session, make_job: MakeJob, owner: User
    ) -> None:
        upload = _settled(db_session, make_job, owner)

        UPLOAD.cancel(db_session, handoff.subject_key(upload.id))
        db_session.commit()

        db_session.expire_all()
        row = db_session.get(ArtifactUploadSession, upload.id)
        assert (row.state, row.error_code) == (ArtifactUploadState.COMPLETED, None)


class TestFailure:
    def test_a_failed_job_fails_the_upload_retryably(
        self, db_session: Session, make_job: MakeJob, owner: User
    ) -> None:
        upload, _job_id = _verified(db_session, make_job, owner)

        from app.modules.work.contracts import JobOutcome

        build_job_context(_job_id).finish(
            JobOutcome.FAILED, error="boom", retryable=True
        )
        db_session.commit()

        db_session.expire_all()
        row = db_session.get(ArtifactUploadSession, upload.id)
        assert (row.state, row.error_code, row.retryable) == (
            ArtifactUploadState.FAILED,
            "artifact_ingestion_failed",
            True,
        )

    def test_a_lost_job_leaves_a_settled_upload_alone(
        self, db_session: Session, make_job: MakeJob, owner: User
    ) -> None:
        upload = _settled(db_session, make_job, owner)

        UPLOAD.on_failure(db_session, handoff.subject_key(upload.id), "boom")
        db_session.commit()

        db_session.expire_all()
        assert db_session.get(ArtifactUploadSession, upload.id).state == (
            ArtifactUploadState.COMPLETED
        )

    def test_the_job_retry_defers_to_the_client(self, db_session: Session) -> None:
        # The client re-uploads; the Job cannot rebuild bytes it no longer has.
        assert UPLOAD.retry(db_session, handoff.subject_key("gone")) is False


class TestRecovery:
    def test_runs_every_hour(self, db_session: Session) -> None:
        assert RECOVERY.source is not None

        assert RECOVERY.source.cron(db_session) == "55 * * * *"  # type: ignore[attr-defined]

    def test_an_occurrence_records_what_it_reconciled(
        self, db_session: Session, make_job: MakeJob, work_engine: InlineJobEngine
    ) -> None:
        job = make_job(kind=RECOVERY.name, subject=f"{RECOVERY.name}@now")

        submit(job.id)
        work_engine.run_one()

        db_session.expire_all()
        row = db_session.get(Job, job.id)
        assert row is not None and row.state == JobState.COMPLETED
        assert json.loads(row.status_json)["result"]["outcome"] == {
            "reconciled": 0,
            "expired": 0,
            "retained": 0,
        }
