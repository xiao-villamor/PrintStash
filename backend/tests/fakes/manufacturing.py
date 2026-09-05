"""Two independent database sessions confirming physical output at one barrier."""

from app.services import multipart_builds


def race_confirmations(engine, owner_id, build_id, attempt_id, same_request):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from fastapi import HTTPException
    from sqlmodel import Session

    from app.db.models import MultipartBuild, User
    from app.schemas.multipart_builds import BuildConfirm

    barrier = Barrier(2)

    def submit(index):
        with Session(engine) as session:
            owner = session.get(User, owner_id)
            build = session.get(MultipartBuild, build_id)
            barrier.wait(timeout=10)
            try:
                result = multipart_builds.confirm(
                    session,
                    owner,
                    build,
                    attempt_id,
                    BuildConfirm(
                        version=0,
                        valid_units=3,
                        idempotency_key="same" if same_request else f"result-{index}",
                    ),
                )
                return 200, multipart_builds.read(session, owner, result).parts[
                    0
                ].missing_units
            except HTTPException as error:
                session.rollback()
                return error.status_code, error.detail

    with ThreadPoolExecutor(max_workers=2) as executor:
        return list(executor.map(submit, range(2)))


def seed_confirmation_race(engine):
    from sqlmodel import Session, SQLModel

    from app.db.models import FileType, PrintJobState
    from tests import factories

    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        owner = factories.build_user(session, superuser=True)
        composition = factories.build_multipart_model(session)
        model = factories.build_model(session)
        revision = factories.build_file(session, model, file_type=FileType.GCODE)
        build = factories.build_multipart_build(session, composition)
        part = factories.build_multipart_build_part(session, build, model, quantity=4)
        job = factories.build_print_job(session, revision, state=PrintJobState.FAILED)
        attempt = factories.build_multipart_build_attempt(
            session, part, job, planned_units=4
        )
        return owner.id, build.id, attempt.id


def race_queues(engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from fastapi import HTTPException
    from sqlmodel import Session, SQLModel, select

    from app.db.models import FileType, MultipartBuild, MultipartBuildAttempt, User
    from app.schemas.multipart_builds import BuildQueue
    from tests import factories

    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        owner = factories.build_user(session, superuser=True)
        composition = factories.build_multipart_model(session)
        model = factories.build_model(session)
        revision = factories.build_file(session, model, file_type=FileType.GCODE)
        build = factories.build_multipart_build(session, composition)
        part = factories.build_multipart_build_part(
            session, build, model, quantity=4, revision_id=revision.id
        )
        owner_id, build_id, part_id = owner.id, build.id, part.id
    barrier = Barrier(2)

    def submit(_index):
        with Session(engine) as session:
            owner = session.get(User, owner_id)
            build = session.get(MultipartBuild, build_id)
            barrier.wait(timeout=10)
            try:
                jobs = multipart_builds.enqueue(
                    session,
                    owner,
                    build,
                    part_id,
                    BuildQueue(version=0, units_per_job=4),
                )
                return 201, len(jobs)
            except HTTPException as error:
                session.rollback()
                return error.status_code, 0

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, range(2)))
    with Session(engine) as session:
        attempts = session.exec(
            select(MultipartBuildAttempt).where(
                MultipartBuildAttempt.part_id == part_id
            )
        ).all()
        return sorted(results), sum(attempt.planned_units for attempt in attempts)
