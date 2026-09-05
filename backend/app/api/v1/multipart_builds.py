"""Manufacturing history and explicit physical results for multipart compositions."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.core.security import require_auth, require_user
from app.db.models import CollectionRole, MultipartBuild, User
from app.db.session import get_session
from app.schemas.multipart_builds import (
    BuildArchive,
    BuildConfirm,
    BuildCreate,
    BuildDuplicate,
    BuildQueue,
    BuildRead,
    BuildSelection,
)
from app.services import fleet, rbac
from app.services import multipart_builds as builds
from app.services.task_queue import TaskEnvelope, TaskQueue, get_task_queue

router = APIRouter(prefix="/multipart-builds", tags=["multipart-builds"])


@router.get("", response_model=list[BuildRead])
def list_builds(
    archived: bool = False,
    multipart_model_id: int | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    query = select(MultipartBuild).where(
        MultipartBuild.archived_at.is_not(None)
        if archived
        else MultipartBuild.archived_at.is_(None)
    )
    if multipart_model_id is not None:
        query = query.where(MultipartBuild.multipart_model_id == multipart_model_id)
    if not user.is_superuser:
        query = query.where(
            MultipartBuild.collection_id.in_(
                rbac.accessible_collection_ids(session, user)
            )
        )
    rows = session.exec(
        query.order_by(MultipartBuild.created_at.desc(), MultipartBuild.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return [builds.read(session, user, row) for row in rows]


@router.post(
    "", response_model=BuildRead, status_code=201, dependencies=[Depends(require_auth)]
)
def create_build(
    payload: BuildCreate,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    return builds.read(session, user, builds.create(session, user, payload))


@router.get("/{build_id}", response_model=BuildRead)
def get_build(
    build_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    return builds.read(session, user, builds.require(session, user, build_id))


@router.patch(
    "/{build_id}/parts/{part_id}",
    response_model=BuildRead,
    dependencies=[Depends(require_auth)],
)
def select_revision(
    build_id: int,
    part_id: int,
    payload: BuildSelection,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    build = builds.require(session, user, build_id, CollectionRole.EDIT)
    return builds.read(
        session, user, builds.select_revision(session, user, build, part_id, payload)
    )


@router.post(
    "/{build_id}/parts/{part_id}/queue",
    response_model=BuildRead,
    status_code=201,
    dependencies=[Depends(require_auth)],
)
async def enqueue_part(
    build_id: int,
    part_id: int,
    payload: BuildQueue,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
    task_queue: TaskQueue = Depends(get_task_queue),
):
    build = builds.require(session, user, build_id, CollectionRole.EDIT)
    try:
        jobs = builds.enqueue(session, user, build, part_id, payload)
    except fleet.FleetError as exc:
        raise HTTPException(
            409 if exc.code == "material_mismatch_confirmation_required" else 400,
            exc.code,
        ) from exc
    for job in jobs:
        await task_queue.enqueue(
            TaskEnvelope(job_id=str(job.id), kind="fleet_dispatch", payload={})
        )
    return builds.read(session, user, build)


@router.post(
    "/{build_id}/attempts/{attempt_id}/confirm",
    response_model=BuildRead,
    dependencies=[Depends(require_auth)],
)
def confirm_result(
    build_id: int,
    attempt_id: int,
    payload: BuildConfirm,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    build = builds.require(session, user, build_id, CollectionRole.EDIT)
    return builds.read(
        session, user, builds.confirm(session, user, build, attempt_id, payload)
    )


@router.post(
    "/{build_id}/duplicate",
    response_model=BuildRead,
    status_code=201,
    dependencies=[Depends(require_auth)],
)
def duplicate_build(
    build_id: int,
    payload: BuildDuplicate,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    build = builds.require(session, user, build_id, CollectionRole.EDIT)
    return builds.read(
        session, user, builds.duplicate(session, user, build, payload.name)
    )


@router.patch(
    "/{build_id}/archive",
    response_model=BuildRead,
    dependencies=[Depends(require_auth)],
)
def archive_build(
    build_id: int,
    payload: BuildArchive,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    build = builds.require(session, user, build_id, CollectionRole.EDIT)
    return builds.read(
        session, user, builds.archive(session, build, payload.version, payload.archived)
    )
