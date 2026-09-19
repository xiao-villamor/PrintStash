"""Durable commands execute with current actor and source authority."""

import pytest

from app.db.models import BackgroundJob
from app.db.session import get_session_factory
from app.modules.ingestion.commands import claim_next, enqueue
from app.runtime.jobs import registry


class TestCommandExecutor:
    @pytest.mark.asyncio
    async def test_revalidates_url_safety_when_executing_durable_work(
        self, db_session, make_user
    ):
        from sqlmodel import select

        from app.db.models import File
        from app.modules.ingestion.command_executor import execute

        user = make_user(superuser=True)
        previous_files = db_session.exec(select(File.id)).all()
        job_id = registry.create(owner_user_id=user.id, session=db_session)
        enqueue(
            db_session,
            job_id,
            "url",
            {
                "request": {"url": "http://127.0.0.1/private.stl"},
                "actor_user_id": user.id,
            },
        )
        db_session.commit()
        claim = claim_next(db_session)

        await execute(claim, get_session_factory())

        status = registry.get(job_id)
        assert status.state == "failed"
        assert status.error == "url_target_not_public"
        assert db_session.exec(select(File.id)).all() == previous_files

    @pytest.mark.asyncio
    async def test_saves_a_staged_artifact_without_request_callbacks(
        self, db_session, make_user, tmp_path
    ):
        import hashlib
        import shutil

        from sqlmodel import select

        from app.api.v1.ingest import _create_staged_job
        from app.db.models import ArtifactAnalysisGeneration, File
        from app.modules.ingestion.command_executor import execute
        from tests._env import use_local_storage
        from tests.paths import TESTDATA_DIR

        use_local_storage(tmp_path)
        user = make_user(superuser=True)
        staged = tmp_path / "cube.stl"
        shutil.copyfile(TESTDATA_DIR / "Calibration Cube.stl", staged)
        job_id = _create_staged_job(
            db_session,
            kind="model",
            staged=staged,
            size=staged.stat().st_size,
            sha256=hashlib.sha256(staged.read_bytes()).hexdigest(),
            owner_user_id=user.id,
            command="artifact",
            arguments={
                "original_filename": "cube.stl",
                "model_name": "Cube",
                "collection": None,
                "tags": None,
                "source_hash": None,
                "actor_user_id": user.id,
            },
        )
        claim = claim_next(db_session)
        await execute(claim, get_session_factory())
        db_session.expire_all()
        file = db_session.exec(select(File).where(File.ingestion_key == job_id)).one()
        assert file.thumbnail_path is None
        assert registry.get(job_id).state == "completed"
        assert (
            db_session.exec(
                select(ArtifactAnalysisGeneration.state).where(
                    ArtifactAnalysisGeneration.file_id == file.id
                )
            ).one()
            == "pending"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("revocation", ["disabled", "deleted", "during_ingestion"])
    async def test_rejects_a_revoked_actor_before_source_publication(
        self, db_session, make_user, tmp_path, monkeypatch, revocation
    ):
        import hashlib
        import shutil

        from sqlmodel import select

        from app.api.v1.ingest import _create_staged_job
        from app.db.models import File
        from app.modules.ingestion.command_executor import execute
        from tests._env import use_local_storage
        from tests.paths import TESTDATA_DIR

        use_local_storage(tmp_path)
        actor = make_user(superuser=True)
        source = tmp_path / "accepted.stl"
        shutil.copyfile(TESTDATA_DIR / "Calibration Cube.stl", source)
        job_id = _create_staged_job(
            db_session,
            kind="model",
            staged=source,
            size=source.stat().st_size,
            sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            owner_user_id=actor.id,
            command="artifact",
            arguments={
                "original_filename": "accepted.stl",
                "model_name": "Revoked",
                "actor_user_id": actor.id,
                "collection": None,
                "tags": None,
                "source_hash": None,
            },
        )

        def revoke(*_args):
            if revocation == "deleted":
                from app.db.models import StagingLease

                job = db_session.get(BackgroundJob, job_id)
                job.owner_user_id = None
                db_session.add(job)
                for lease in db_session.exec(
                    select(StagingLease).where(StagingLease.background_job_id == job_id)
                ):
                    lease.owner_user_id = None
                    db_session.add(lease)
                db_session.flush()
                db_session.delete(actor)
            else:
                actor.is_active = False
                db_session.add(actor)
            db_session.commit()

        if revocation == "during_ingestion":
            monkeypatch.setattr(
                "app.modules.ingestion.ingestion._fault_injection_checkpoint", revoke
            )
        else:
            revoke()
        claim = claim_next(db_session)
        await execute(claim, get_session_factory())
        assert (
            db_session.exec(select(File).where(File.ingestion_key == job_id)).all()
            == []
        )
        assert registry.get(job_id).state == "failed"
