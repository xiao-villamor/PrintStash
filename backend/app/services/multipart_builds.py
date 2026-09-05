"""Manufacturing snapshots and confirmed physical output, separate from job state.

A completed job suggests output; only an explicit result confirmation counts it.
All enqueue mutations hold the build's optimistic database version until the
jobs and their immutable links commit together.
"""

from __future__ import annotations

import json
import math

from fastapi import HTTPException
from sqlalchemy import update
from sqlmodel import Session, select

from app.core.config import settings
from app.core.time import utcnow
from app.db.models import (
    CollectionRole,
    File,
    FileType,
    Model,
    MultipartBuild,
    MultipartBuildAttempt,
    MultipartBuildConfirmation,
    MultipartBuildPart,
    MultipartModelChoice,
    MultipartPart,
    PrinterRole,
    PrintJob,
    PrintJobState,
    RoutingStrategy,
    User,
)
from app.db.scopes import live
from app.schemas.fleet import BatchCreate
from app.schemas.multipart_builds import (
    BuildAttemptRead,
    BuildChoiceRead,
    BuildConfirm,
    BuildCreate,
    BuildPartRead,
    BuildQueue,
    BuildRead,
    BuildSelection,
)
from app.services import fleet, multipart_models, printer_rbac, rbac

TERMINAL = {PrintJobState.COMPLETED, PrintJobState.FAILED, PrintJobState.CANCELLED}


def require(
    session: Session, user: User, build_id: int, role=CollectionRole.VIEW
) -> MultipartBuild:
    build = session.get(MultipartBuild, build_id)
    if build is None:
        raise HTTPException(404, "build_not_found")
    rbac.require_collection_role(session, user, build.collection_id, role)
    return build


def _claim(session: Session, build: MultipartBuild, version: int) -> None:
    if build.archived_at is not None:
        raise HTTPException(409, "build_archived")
    changed = session.execute(
        update(MultipartBuild)
        .where(
            MultipartBuild.id == build.id,
            MultipartBuild.version == version,
        )
        .values(version=version + 1, updated_at=utcnow())
    )
    if changed.rowcount != 1:
        raise HTTPException(409, "build_version_conflict")
    session.refresh(build)


def _model(session: Session, user: User, model_id: int | None) -> Model | None:
    model = session.exec(select(Model).where(Model.id == model_id, live(Model))).first()
    if model is None or not rbac.role_allows(
        rbac.effective_collection_role(session, user, model.collection_id),
        CollectionRole.VIEW,
    ):
        return None
    return model


def _revision(
    session: Session, model: Model | None, revision_id: int | None
) -> File | None:
    if model is None or revision_id is None:
        return None
    return session.exec(
        select(File).where(
            File.id == revision_id,
            File.model_id == model.id,
            File.file_type == FileType.GCODE,
            live(File),
        )
    ).first()


def _part(session: Session, build: MultipartBuild, part_id: int) -> MultipartBuildPart:
    part = session.get(MultipartBuildPart, part_id)
    if part is None or part.build_id != build.id:
        raise HTTPException(404, "build_part_not_found")
    return part


def _parts(session: Session, build: MultipartBuild) -> list[MultipartBuildPart]:
    return list(
        session.exec(
            select(MultipartBuildPart)
            .where(MultipartBuildPart.build_id == build.id)
            .order_by(MultipartBuildPart.sort_order, MultipartBuildPart.id)
        ).all()
    )


def read_part(session: Session, user: User, part: MultipartBuildPart) -> BuildPartRead:
    model = _model(session, user, part.selected_model_id)
    revision = _revision(session, model, part.revision_id)
    attempts = []
    valid = active = unreviewed = 0
    for attempt in session.exec(
        select(MultipartBuildAttempt)
        .where(MultipartBuildAttempt.part_id == part.id)
        .order_by(MultipartBuildAttempt.id)
    ).all():
        job = session.get(PrintJob, attempt.job_id) if attempt.job_id else None
        state = job.state.value if job is not None else "unavailable"
        pending = job is not None and job.state not in TERMINAL
        valid += attempt.valid_units or 0
        if attempt.valid_units is None:
            if pending:
                active += attempt.planned_units
            else:
                unreviewed += attempt.planned_units
        readable = _revision(
            session, _model(session, user, attempt.model_id), attempt.revision_id
        )
        attempts.append(
            BuildAttemptRead(
                id=attempt.id,
                job_id=attempt.job_id,
                historical_job_id=attempt.historical_job_id,
                revision_id=attempt.revision_id if readable else None,
                planned_units=attempt.planned_units,
                valid_units=attempt.valid_units,
                suggested_valid_units=attempt.planned_units
                if job and job.state == PrintJobState.COMPLETED
                else 0,
                state=state,
                version=attempt.version,
            )
        )
    choices = []
    for choice in json.loads(part.choices_json):
        member = _model(session, user, choice["model_id"])
        choices.append(
            BuildChoiceRead(
                choice_id=choice.get("choice_id"),
                model_id=choice["model_id"],
                name=choice["name"] if member else None,
                available=member is not None,
            )
        )
    missing = max(part.required_units - valid, 0)
    return BuildPartRead(
        id=part.id,
        name=part.name,
        quantity=part.quantity,
        required_units=part.required_units,
        valid_units=valid,
        missing_units=missing,
        active_units=active,
        unreviewed_units=unreviewed,
        unreserved_units=max(missing - active - unreviewed, 0),
        selected_model_id=part.selected_model_id,
        selected_choice_id=part.selected_choice_id,
        revision_id=part.revision_id if revision else None,
        queueable=revision is not None,
        choices=choices,
        attempts=attempts,
    )


def read(session: Session, user: User, build: MultipartBuild) -> BuildRead:
    parts = [read_part(session, user, part) for part in _parts(session, build)]
    return BuildRead(
        effective_role=rbac.effective_collection_role(
            session, user, build.collection_id
        ),
        id=build.id,
        name=build.name,
        multipart_model_id=build.multipart_model_id,
        composition_name=build.composition_name,
        object_quantity=build.object_quantity,
        version=build.version,
        archived_at=build.archived_at,
        created_at=build.created_at,
        completed=bool(parts) and all(part.missing_units == 0 for part in parts),
        parts=parts,
    )


def create(session: Session, user: User, payload: BuildCreate) -> MultipartBuild:
    composition = multipart_models.require(
        session, user, payload.multipart_model_id, CollectionRole.EDIT
    )
    parts = session.exec(
        select(MultipartPart)
        .where(MultipartPart.multipart_model_id == composition.id)
        .order_by(MultipartPart.sort_order)
    ).all()
    if not parts:
        raise HTTPException(400, "build_composition_empty")
    build = MultipartBuild(
        name=payload.name.strip(),
        multipart_model_id=composition.id,
        composition_name=composition.name,
        collection_id=composition.collection_id,
        object_quantity=payload.object_quantity,
        created_by=user.id,
    )
    if not build.name:
        raise HTTPException(400, "build_name_empty")
    session.add(build)
    session.flush()
    for part in parts:
        choices = []
        for choice in session.exec(
            select(MultipartModelChoice)
            .where(MultipartModelChoice.multipart_part_id == part.id)
            .order_by(MultipartModelChoice.sort_order)
        ).all():
            member = _model(session, user, choice.model_id)
            choices.append(
                {
                    "model_id": choice.model_id,
                    "choice_id": choice.id,
                    "name": member.name if member else None,
                }
            )
        selected_id = next(
            (choice["model_id"] for choice in choices if choice["name"] is not None),
            None,
        )
        recommended = (
            session.exec(
                select(File)
                .where(
                    File.model_id == selected_id,
                    File.file_type == FileType.GCODE,
                    File.is_recommended,
                    live(File),
                )
                .order_by(File.id)
            ).first()
            if selected_id
            else None
        )
        session.add(
            MultipartBuildPart(
                build_id=build.id,
                name=part.name,
                sort_order=part.sort_order,
                quantity=part.quantity,
                required_units=part.quantity * payload.object_quantity,
                choices_json=json.dumps(choices),
                selected_model_id=selected_id,
                selected_choice_id=next(
                    (
                        choice["choice_id"]
                        for choice in choices
                        if choice["model_id"] == selected_id
                    ),
                    None,
                ),
                revision_id=recommended.id if recommended else None,
            )
        )
    session.commit()
    session.refresh(build)
    return build


def select_revision(
    session: Session,
    user: User,
    build: MultipartBuild,
    part_id: int,
    payload: BuildSelection,
) -> MultipartBuild:
    part = _part(session, build, part_id)
    choices = json.loads(part.choices_json)
    if payload.choice_id is not None:
        selected = next(
            (
                choice
                for choice in choices
                if choice.get("choice_id") == payload.choice_id
            ),
            None,
        )
    elif payload.model_id is not None:
        selected = next(
            (choice for choice in choices if choice["model_id"] == payload.model_id),
            None,
        )
    else:
        selected = next(
            (
                choice
                for choice in choices
                if choice.get("choice_id") == part.selected_choice_id
                and choice["model_id"] == part.selected_model_id
            ),
            None,
        )
    if selected is None or (
        payload.model_id is not None and payload.model_id != selected["model_id"]
    ):
        raise HTTPException(400, "build_choice_invalid")
    model_id = selected["model_id"]
    model = _model(session, user, model_id)
    if model is None:
        raise HTTPException(400, "build_model_unavailable")
    if (
        payload.revision_id is not None
        and _revision(session, model, payload.revision_id) is None
    ):
        raise HTTPException(400, "build_revision_invalid")
    _claim(session, build, payload.version)
    part.selected_model_id = model_id
    part.selected_choice_id = selected.get("choice_id")
    part.revision_id = payload.revision_id
    part.updated_at = utcnow()
    session.add(part)
    session.commit()
    return build


def enqueue(
    session: Session,
    user: User,
    build: MultipartBuild,
    part_id: int,
    payload: BuildQueue,
) -> list[PrintJob]:
    part = _part(session, build, part_id)
    model = _model(session, user, part.selected_model_id)
    revision = _revision(session, model, part.revision_id)
    if revision is None:
        raise HTTPException(409, "build_revision_required")
    rbac.require_model_collection_role(
        session, user, model.collection_id, CollectionRole.EDIT
    )
    routing = payload.routing
    if not user.is_superuser and (
        routing.strategy != RoutingStrategy.MANUAL or routing.printer_id is None
    ):
        raise HTTPException(403, "printer_permission_denied")
    if routing.printer_id is not None:
        printer_rbac.require_printer_role(
            session, user, routing.printer_id, PrinterRole.PRINT
        )
    _claim(session, build, payload.version)
    available = read_part(session, user, part).unreserved_units
    if available == 0:
        raise HTTPException(409, "build_no_unreserved_units")
    count = payload.job_count or math.ceil(available / payload.units_per_job)
    if count > settings.fleet_batch_max_quantity:
        raise HTTPException(400, "batch_quantity_exceeds_limit")
    if count * payload.units_per_job > available and not payload.confirm_excess:
        raise HTTPException(409, "build_excess_confirmation_required")
    _, jobs = fleet.create_batch(
        session,
        BatchCreate(file_id=revision.id, quantity=count, **routing.model_dump()),
        user,
        commit=False,
    )
    for job in jobs:
        session.add(
            MultipartBuildAttempt(
                part_id=part.id,
                job_id=job.id,
                historical_job_id=job.id,
                model_id=model.id,
                revision_id=revision.id,
                planned_units=payload.units_per_job,
            )
        )
    session.commit()
    return jobs


def _receipt_matches(
    receipt: MultipartBuildConfirmation | None, payload: BuildConfirm
) -> bool:
    if receipt is None:
        return False
    if (
        receipt.valid_units != payload.valid_units
        or receipt.requested_version != payload.version
    ):
        raise HTTPException(409, "build_idempotency_conflict")
    return True


def confirm(
    session: Session,
    user: User,
    build: MultipartBuild,
    attempt_id: int,
    payload: BuildConfirm,
) -> MultipartBuild:
    # Take the same database write lock as enqueue/archive, without consuming a
    # version for a repeated confirmation. Read the receipt only after the lock.
    session.execute(
        update(MultipartBuild)
        .where(MultipartBuild.id == build.id)
        .values(version=MultipartBuild.version)
    )
    session.refresh(build)
    attempt = session.get(MultipartBuildAttempt, attempt_id)
    if attempt is None:
        raise HTTPException(404, "build_attempt_not_found")
    _part(session, build, attempt.part_id)
    receipt_query = select(MultipartBuildConfirmation).where(
        MultipartBuildConfirmation.attempt_id == attempt.id,
        MultipartBuildConfirmation.idempotency_key == payload.idempotency_key,
    )
    if _receipt_matches(session.exec(receipt_query).first(), payload):
        return build
    if build.archived_at is not None:
        raise HTTPException(409, "build_archived")
    if payload.valid_units > attempt.planned_units:
        raise HTTPException(400, "build_valid_units_exceed_planned")
    job = session.get(PrintJob, attempt.job_id) if attempt.job_id else None
    if job and job.state not in TERMINAL:
        raise HTTPException(409, "build_job_not_finished")
    changed = session.execute(
        update(MultipartBuildAttempt)
        .where(
            MultipartBuildAttempt.id == attempt.id,
            MultipartBuildAttempt.version == payload.version,
        )
        .values(
            valid_units=payload.valid_units,
            version=payload.version + 1,
            confirmed_at=utcnow(),
            confirmed_by=user.id,
            updated_at=utcnow(),
        )
    )
    if changed.rowcount != 1:
        if _receipt_matches(session.exec(receipt_query).first(), payload):
            return build
        raise HTTPException(409, "build_result_version_conflict")
    session.add(
        MultipartBuildConfirmation(
            attempt_id=attempt.id,
            idempotency_key=payload.idempotency_key,
            requested_version=payload.version,
            valid_units=payload.valid_units,
        )
    )
    session.execute(
        update(MultipartBuild)
        .where(MultipartBuild.id == build.id)
        .values(version=MultipartBuild.version + 1, updated_at=utcnow())
    )
    session.commit()
    session.refresh(build)
    return build


def duplicate(
    session: Session, user: User, original: MultipartBuild, name: str
) -> MultipartBuild:
    name = name.strip()
    if not name:
        raise HTTPException(400, "build_name_empty")
    copy = MultipartBuild(
        name=name,
        multipart_model_id=original.multipart_model_id,
        composition_name=original.composition_name,
        collection_id=original.collection_id,
        object_quantity=original.object_quantity,
        created_by=user.id,
    )
    session.add(copy)
    session.flush()
    for part in _parts(session, original):
        session.add(
            MultipartBuildPart(
                **part.model_dump(
                    exclude={"id", "build_id", "created_at", "updated_at"}
                ),
                build_id=copy.id,
            )
        )
    session.commit()
    session.refresh(copy)
    return copy


def archive(
    session: Session, build: MultipartBuild, version: int, archived: bool
) -> MultipartBuild:
    changed = session.execute(
        update(MultipartBuild)
        .where(
            MultipartBuild.id == build.id,
            MultipartBuild.version == version,
        )
        .values(
            version=version + 1,
            archived_at=utcnow() if archived else None,
            updated_at=utcnow(),
        )
    )
    if changed.rowcount != 1:
        raise HTTPException(409, "build_version_conflict")
    session.commit()
    session.refresh(build)
    return build
