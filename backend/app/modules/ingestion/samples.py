"""Durable sample-collection requests and their ingestion job."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.core.cancellation import checkpoint
from app.core.config import settings
from app.db.models import (
    SENTINEL_MODEL_HASH,
    Collection,
    FileType,
    Job,
    JobKind,
    Model,
    SystemConfig,
    User,
)
from app.db.scopes import live
from app.db.session import get_session_factory
from app.modules.work.contracts import JobContext, JobOutcome
from app.modules.work.jobs import ActiveJobExists, jobs
from app.modules.work.submission import nudge

COLLECTION = "PrintStash samples"
COLLECTION_PATH = "printstash-samples"
KIND = JobKind.INGESTION_SAMPLES


def queue(session: Session, user_id: int) -> str | None:
    if (
        session.exec(
            select(Collection.id).where(
                Collection.path == COLLECTION_PATH, live(Collection)
            )
        ).first()
        is not None
    ):
        return None
    try:
        with session.begin_nested():
            return jobs.create(
                definition=KIND,
                subject_key=COLLECTION_PATH,
                owner_user_id=user_id,
                session=session,
            )
    except ActiveJobExists as exc:
        return exc.job_id
    except IntegrityError:
        active = jobs.active_for_subject(KIND, COLLECTION_PATH)
        if active is None:
            raise
        return active


def queue_first_run(session: Session, config: SystemConfig) -> str | None:
    """Reserve the first-run decision in the same commit that activates storage."""
    if config.samples_initialized:
        return None
    config.samples_initialized = True
    if (
        session.exec(
            select(Model.id).where(Model.hash != SENTINEL_MODEL_HASH).limit(1)
        ).first()
        is not None
    ):
        return None
    owner = session.exec(
        select(User.id).where(col(User.is_superuser)).order_by(col(User.id)).limit(1)
    ).first()
    return queue(session, owner) if owner is not None else None


def wake() -> None:
    nudge(KIND)


def ingest(ctx: JobContext) -> None:
    from app.modules.ingestion.ingestion import StagedArtifact, commit_staged_artifact
    from app.modules.ingestion.sample_geometry import sample_models

    with get_session_factory().scoped_session() as session:
        job = session.get(Job, ctx.job_id)
        if job is None or job.owner_user_id is None:
            raise RuntimeError("sample_job_missing_owner")
        owner = job.owner_user_id
    with TemporaryDirectory(
        prefix="printstash-samples-", dir=settings.staging_dir
    ) as staged:
        models = sample_models()
        for index, (name, mesh) in enumerate(models, start=1):
            checkpoint(force=True)
            ctx.update(current_item=name, total=len(models), processed=index - 1)
            filename = f"{name}.stl"
            path = Path(staged) / filename
            data = mesh.export(file_type="stl")
            # A deliberate re-add is a fresh import. The same Job's retries
            # keep the same bytes and ingestion keys.
            header = f"PrintStash samples {ctx.job_id}".encode("ascii").ljust(
                80, b"\x00"
            )
            path.write_bytes(header + data[80:])
            commit_staged_artifact(
                StagedArtifact(
                    staged_path=path,
                    original_filename=filename,
                    model_name=name,
                    file_type=FileType.STL,
                    collection=COLLECTION,
                ),
                ingestion_key=f"{ctx.job_id}/sample/{index}",
                actor_user_id=owner,
            )
    ctx.finish(
        JobOutcome.COMPLETED,
        total=len(models),
        processed=len(models),
        succeeded=len(models),
    )
