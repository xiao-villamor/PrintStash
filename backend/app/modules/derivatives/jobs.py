"""Job definitions for Artifact derivatives: one per producer group."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from sqlmodel import Session, select

from app.core.errors import OperationError
from app.core.time import utcnow
from app.db.models import File, JobKind, LaneName
from app.db.scopes import live
from app.modules.work.contracts import (
    JobContext,
    JobDefinition,
    JobExecution,
    JobOutcome,
    SkipReason,
    Step,
)

from . import policy, producers, records
from .kinds import DerivativeGroup, group
from .source import DerivativeSource, file_id_of


class Producer(Protocol):
    def __call__(
        self, file_id: int, *, execution: JobExecution | None = None
    ) -> producers.Outcome: ...


def _step(
    produce: Producer, derivative_group: DerivativeGroup
) -> Callable[[JobContext], None]:
    def run(ctx: JobContext) -> None:
        try:
            outcome = produce(
                file_id_of(ctx.subject_key),
                execution=JobExecution(
                    ctx.job_id, ctx.attempt, ctx.execution_epoch
                ),
            )
        except OperationError as exc:
            if exc.code != SkipReason.DERIVATIVE_GROUP_DISABLED:
                raise
            ctx.finish(
                JobOutcome.CANCELLED,
                error=SkipReason.DERIVATIVE_GROUP_DISABLED,
                retryable=False,
            )
            return
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
            records.cancel(session, file_row, derivative_group.kinds, now=utcnow())

    def on_failure(session: Session, subject_key: str, reason: str) -> None:
        records.fail_in_flight(
            session,
            file_id_of(subject_key),
            reason,
            now=utcnow(),
            kinds=derivative_group.kinds,
        )

    def retry(session: Session, subject_key: str) -> bool:
        policy.lock(session)
        policy.require_enabled(session, derivative_group.definition)
        file_row = _file(session, subject_key)
        if file_row is None:
            return False
        records.reset(session, file_row, derivative_group.kinds)
        return True

    return cancel, on_failure, retry


def _definition(name: JobKind, lane: LaneName, produce: Producer) -> JobDefinition:
    derivative_group = group(name)
    cancel, on_failure, retry = _hooks(derivative_group)
    return JobDefinition(
        name=name,
        lane=lane,
        steps=(Step(f"{name.value}.produce", _step(produce, derivative_group)),),
        source=DerivativeSource(derivative_group),
        cancel=cancel,
        on_failure=on_failure,
        retry=retry,
        label=derivative_group.label,
        admission=lambda session: policy.admission(session, name),
    )


def definitions() -> list[JobDefinition]:
    return [
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
