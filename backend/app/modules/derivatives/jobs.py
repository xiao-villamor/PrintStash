"""Job definitions for Artifact derivatives: one per producer group."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, cast

from sqlmodel import Session, select

from app.core.errors import OperationError
from app.core.time import utcnow
from app.db.models import File, JobKind, LaneName
from app.db.scopes import live
from app.modules.work.contracts import (
    DuringAdditions,
    JobContext,
    JobDefinition,
    JobExecution,
    JobOutcome,
    SkipReason,
    Step,
)

from . import policy, previews, producers, records
from .kinds import DerivativeGroup, group
from .preview_source import PreviewSource
from .source import DerivativeSource, file_id_of


class Producer(Protocol):
    def __call__(
        self, file_id: int, *, execution: JobExecution | None = None
    ) -> producers.Outcome: ...


class PreviewProducer(Protocol):
    def __call__(
        self,
        file_id: int,
        *,
        execution: JobExecution | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> producers.Outcome: ...


def _step(
    produce: Producer, derivative_group: DerivativeGroup
) -> Callable[[JobContext], None]:
    def run(ctx: JobContext) -> None:
        try:
            execution = JobExecution(ctx.job_id, ctx.attempt, ctx.execution_epoch)
            if derivative_group.definition is JobKind.DERIVATIVES_PREVIEWS:
                outcome = cast(PreviewProducer, produce)(
                    file_id_of(ctx.subject_key),
                    execution=execution,
                    progress=lambda done, total: ctx.update(
                        processed=done, total=total
                    ),
                )
            else:
                outcome = produce(file_id_of(ctx.subject_key), execution=execution)
        except OperationError as exc:
            if exc.code != SkipReason.DERIVATIVE_GROUP_DISABLED:
                raise
            ctx.finish(
                JobOutcome.CANCELLED,
                error=SkipReason.DERIVATIVE_GROUP_DISABLED,
                retryable=False,
            )
            return
        if derivative_group.definition is JobKind.DERIVATIVES_PREVIEWS:
            ctx.update(result=outcome.as_result())
        else:
            ctx.update(result=outcome.as_result(), processed=1, total=1)

    return run


def _file(session: Session, subject_key: str) -> File | None:
    return session.exec(
        select(File).where(File.id == file_id_of(subject_key), live(File))
    ).first()


def _hooks(derivative_group: DerivativeGroup):
    def cancel(session: Session, subject_key: str) -> None:
        file_row = _file(session, subject_key)
        if file_row is not None:
            if derivative_group.definition is JobKind.DERIVATIVES_MESH:
                from .mesh_continuations import withdraw_current_job

                withdraw_current_job(session, file_id_of(subject_key))
            records.cancel(
                session,
                file_row,
                derivative_group.recipes_for(file_row.file_type),
                now=utcnow(),
            )

    def on_failure(session: Session, subject_key: str, reason: str) -> None:
        # Trashed Artifacts keep their rows; a purged one took them with it.
        file_row = session.get(File, file_id_of(subject_key))
        if file_row is None:
            return
        records.fail_in_flight(
            session,
            file_id_of(subject_key),
            reason,
            now=utcnow(),
            kinds=derivative_group.recipes_for(file_row.file_type),
        )

    def retry(session: Session, subject_key: str) -> bool:
        policy.lock(session)
        policy.require_enabled(session, derivative_group.definition)
        file_row = _file(session, subject_key)
        if file_row is None:
            return False
        records.reset(
            session, file_row, derivative_group.recipes_for(file_row.file_type)
        )
        return True

    return cancel, on_failure, retry


def _during_additions(name: JobKind) -> DuringAdditions:
    """How a derivative definition looks for work while Models are being added.

    A viewer mesh is what opening a Model in 3D waits for; it never yields.
    Mesh outputs follow an import as its Models land, at a pace. G-code and
    toolpath outputs wait for the last addition to settle: a paced look costs
    the import a writer turn every few seconds even when the batch brings no
    G-code, and the settling addition stamps them, so nothing is lost.
    """
    if name is JobKind.DERIVATIVES_VIEWER_STL:
        return DuringAdditions.RUNS
    if name in {JobKind.DERIVATIVES_MESH, JobKind.DERIVATIVES_PREVIEWS}:
        return DuringAdditions.PACED
    return DuringAdditions.WAITS


def _definition(name: JobKind, lane: LaneName, produce: Producer) -> JobDefinition:
    derivative_group = group(name)
    cancel, on_failure, retry = _hooks(derivative_group)
    return JobDefinition(
        name=name,
        lane=lane,
        steps=(Step(f"{name.value}.produce", _step(produce, derivative_group)),),
        source=PreviewSource(derivative_group)
        if name is JobKind.DERIVATIVES_PREVIEWS
        else DerivativeSource(derivative_group),
        cancel=cancel,
        on_failure=on_failure,
        retry=retry,
        completion_nudges=(JobKind.DERIVATIVES_PREVIEWS,)
        if name in {JobKind.DERIVATIVES_MESH, JobKind.DERIVATIVES_GCODE}
        else (),
        label=derivative_group.label,
        admission=lambda session: policy.admission(session, name),
        during_additions=_during_additions(name),
    )


def definitions() -> list[JobDefinition]:
    return [
        _definition(
            JobKind.DERIVATIVES_PREVIEWS, LaneName.DERIVE_NATIVE, previews.derive
        ),
        _definition(
            JobKind.DERIVATIVES_VIEWER_STL,
            LaneName.DERIVE_NATIVE,
            producers.derive_viewer_stl,
        ),
        _definition(
            JobKind.DERIVATIVES_MESH, LaneName.DERIVE_NATIVE, producers.derive_mesh
        ),
        _definition(
            JobKind.DERIVATIVES_GCODE, LaneName.DERIVE_LIGHT, producers.derive_gcode
        ),
        _definition(
            JobKind.DERIVATIVES_PLATES, LaneName.DERIVE_LIGHT, producers.derive_plates
        ),
        _definition(
            JobKind.DERIVATIVES_TOOLPATH,
            LaneName.DERIVE_NATIVE,
            producers.derive_toolpath,
        ),
    ]


def nudge_for(session: Session, file_row: File) -> None:
    """After an Artifact commit: nudge every group that applies to it."""
    from app.modules.work import nudge

    from .kinds import groups_for

    controls = policy.resolve(session)
    for derivative_group in groups_for(file_row):
        if controls[derivative_group.definition].enabled:
            nudge(derivative_group.definition)


def nudge_with_commit(session: Session, file_row: File) -> None:
    """Inside an Artifact's transaction: nudge its groups as that commits.

    The dirty marks are written with the Artifact, so its derivative sources
    cost the commit no second transaction; a rollback nudges nothing.
    """
    from app.modules.work.submission import nudge_after_commit

    from .kinds import groups_for

    controls = policy.resolve(session)
    for derivative_group in groups_for(file_row):
        if controls[derivative_group.definition].enabled:
            nudge_after_commit(session, derivative_group.definition)
