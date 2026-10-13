"""Durable pending analysis after basic mesh outputs commit independently.

The pending token is distinct from a terminal derivative's publication token.
Every cache mutation and exact-token retirement remains in one transaction
holding the Job, generation, live source and continuation authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import delete, update
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.time import ensure_utc, utcnow
from app.db.affected import affected
from app.db.models import (
    ACTIVE_JOB_STATES,
    DerivativeKind,
    File,
    Job,
    JobKind,
    MeshFingerprintContinuation,
)
from app.modules.derivatives import records
from app.modules.derivatives.kinds import groups_for, recipes_for
from app.modules.derivatives.mesh_continuation_values import (
    FingerprintPlan,
    decode_source,
    encode_source,
)
from app.modules.ingestion.extensions import (
    MeshFingerprintDeferred,
    MeshFingerprintPublication,
)
from app.modules.media.fingerprints import FingerprintResult, FingerprintResultState
from app.modules.media.mesh_facts import FingerprintFailureCode
from app.modules.work.contracts import JobExecution


@dataclass(frozen=True)
class MeshContinuation:
    context: records.DerivativeAttempt
    plan: FingerprintPlan
    attempts: int


def _captured(
    row: MeshFingerprintContinuation, execution: JobExecution | None
) -> MeshContinuation:
    expected = (
        (None, None, None)
        if execution is None
        else (execution.job_id, execution.execution_epoch, execution.attempt)
    )
    if (row.job_id, row.execution_epoch, row.job_attempt) != expected:
        raise records.AttemptSuperseded()
    return MeshContinuation(
        records.DerivativeAttempt(
            row.file_id,
            DerivativeKind.METADATA,
            row.metadata_recipe,
            row.token,
            decode_source(row.source_identity_json),
            row.regenerated_at,
            execution,
        ),
        FingerprintPlan(row.algorithm_version, row.triangle_cap),
        row.attempts,
    )


def create(
    session: Session, attempt: records.DerivativeAttempt, plan: FingerprintPlan
) -> MeshContinuation:
    """Record intent inside the same transaction that makes metadata READY."""
    if attempt.kind is not DerivativeKind.METADATA:
        raise ValueError("fingerprint_continuation_requires_metadata")
    file, _ = records.claim_running(session, attempt)
    if not any(
        group.definition is JobKind.DERIVATIVES_MESH for group in groups_for(file)
    ):
        raise ValueError("continuation_requires_mesh_source")
    row = session.exec(
        select(MeshFingerprintContinuation)
        .where(MeshFingerprintContinuation.file_id == file.id)
        .with_for_update()
    ).first()
    token = str(uuid4())
    source_identity = encode_source(attempt.source)
    now = utcnow()
    execution = attempt.execution
    job_id = None if execution is None else execution.job_id
    execution_epoch = None if execution is None else execution.execution_epoch
    job_attempt = None if execution is None else execution.attempt
    if row is None:
        row = MeshFingerprintContinuation(
            file_id=attempt.file_id,
            token=token,
            attempts=1,
            source_sha256=attempt.source.sha256,
            metadata_recipe=attempt.recipe,
            algorithm_version=plan.algorithm_version,
            triangle_cap=plan.triangle_cap,
            source_identity_json=source_identity,
            regenerated_at=attempt.regenerated_at,
            updated_at=now,
            available_at=now,
            job_id=job_id,
            execution_epoch=execution_epoch,
            job_attempt=job_attempt,
        )
    else:
        row.token = token
        row.attempts = 1
        row.source_sha256 = attempt.source.sha256
        row.metadata_recipe = attempt.recipe
        row.algorithm_version = plan.algorithm_version
        row.triangle_cap = plan.triangle_cap
        row.source_identity_json = source_identity
        row.regenerated_at = attempt.regenerated_at
        row.updated_at = now
        row.available_at = now
        row.job_id = job_id
        row.execution_epoch = execution_epoch
        row.job_attempt = job_attempt
    session.add(row)
    session.flush()
    return _captured(row, attempt.execution)


def resume(
    session: Session,
    file: File,
    plan: FingerprintPlan | None,
    *,
    execution: JobExecution | None,
) -> MeshContinuation | None:
    """Retoken valid durable intent, or retire obsolete input under current locks."""
    assert file.id is not None
    if not any(
        group.definition is JobKind.DERIVATIVES_MESH for group in groups_for(file)
    ):
        raise ValueError("continuation_requires_mesh_source")
    recipe = recipes_for(file)[DerivativeKind.METADATA]
    regen = records.regenerations(
        session, definitions=[g.definition for g in groups_for(file)]
    )
    current = records.DerivativeAttempt(
        file.id,
        DerivativeKind.METADATA,
        recipe,
        str(uuid4()),
        records.ArtifactSource.of(file),
        regen.get(DerivativeKind.METADATA),
        execution,
    )
    records.require_context(session, current)
    row = session.exec(
        select(MeshFingerprintContinuation)
        .where(MeshFingerprintContinuation.file_id == file.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if row is None:
        return None
    if row.job_id is None:
        stored_execution = None
    else:
        if row.job_attempt is None or row.execution_epoch is None:
            raise ValueError("incomplete_continuation_execution")
        stored_execution = JobExecution(
            row.job_id, row.job_attempt, row.execution_epoch
        )
    captured = _captured(row, stored_execution)
    if (
        plan is None
        or captured.plan != plan
        or captured.context.source != current.source
        or captured.context.recipe != recipe
        or captured.context.regenerated_at != current.regenerated_at
    ):
        session.delete(row)
        session.flush()
        return None
    if ensure_utc(row.available_at) > utcnow():
        return None
    row.token = str(uuid4())
    row.job_id = None if execution is None else execution.job_id
    row.execution_epoch = None if execution is None else execution.execution_epoch
    row.job_attempt = None if execution is None else execution.attempt
    row.updated_at = utcnow()
    session.add(row)
    session.flush()
    if row.attempts >= settings.derivative_max_attempts:
        complete(
            session,
            _captured(row, execution),
            FingerprintResult(
                FingerprintResultState.FAILED,
                failure_code=FingerprintFailureCode.WORKER_FAILED,
                algorithm_version=plan.algorithm_version,
            ),
        )
        return None
    row.attempts += 1
    session.add(row)
    session.flush()
    return _captured(row, execution)


def require_pending(session: Session, pending: MeshContinuation) -> File:
    """Require context plus the exact pending token in the publishing transaction."""
    file = records.require_context(session, pending.context)
    changed = affected(
        session,
        update(MeshFingerprintContinuation)
        .where(
            col(MeshFingerprintContinuation.file_id) == pending.context.file_id,
            col(MeshFingerprintContinuation.token) == pending.context.token,
            col(MeshFingerprintContinuation.metadata_recipe) == pending.context.recipe,
            col(MeshFingerprintContinuation.source_sha256)
            == pending.context.source.sha256,
            col(MeshFingerprintContinuation.algorithm_version)
            == pending.plan.algorithm_version,
            col(MeshFingerprintContinuation.triangle_cap) == pending.plan.triangle_cap,
            col(MeshFingerprintContinuation.attempts) == pending.attempts,
        )
        .values(token=pending.context.token)
        .execution_options(synchronize_session=False),
    )
    if changed != 1:
        raise records.AttemptSuperseded()
    return file


def complete(
    session: Session, pending: MeshContinuation, result: FingerprintResult
) -> MeshFingerprintPublication:
    """Cache, follow-up run intent and pending retirement share the caller commit."""
    from app.modules.ingestion.extensions import publish_mesh_fingerprint_continuation

    if result.algorithm_version != pending.plan.algorithm_version:
        raise ValueError("continuation_fingerprint_algorithm_changed")
    file = require_pending(session, pending)
    state = publish_mesh_fingerprint_continuation(session, file, result)
    if isinstance(state, MeshFingerprintDeferred):
        affected(
            session,
            update(MeshFingerprintContinuation)
            .where(
                col(MeshFingerprintContinuation.file_id) == pending.context.file_id,
                col(MeshFingerprintContinuation.token) == pending.context.token,
            )
            .values(available_at=state.available_at)
            .execution_options(synchronize_session=False),
        )
        return state
    removed = affected(
        session,
        delete(MeshFingerprintContinuation)
        .where(
            col(MeshFingerprintContinuation.file_id) == pending.context.file_id,
            col(MeshFingerprintContinuation.token) == pending.context.token,
        )
        .execution_options(synchronize_session=False),
    )
    if removed != 1:
        raise records.AttemptSuperseded()
    return state


def defer(
    session: Session, pending: MeshContinuation, failure_code: FingerprintFailureCode
) -> None:
    """Bound retries without changing independently committed derivative outputs."""
    require_pending(session, pending)
    failed = FingerprintResult(
        FingerprintResultState.FAILED,
        failure_code=failure_code,
        algorithm_version=pending.plan.algorithm_version,
    )
    if pending.attempts >= settings.derivative_max_attempts:
        complete(session, pending, failed)
        return
    delay = min(
        settings.derivative_backoff_seconds * 2 ** (pending.attempts - 1), 86400
    )
    session.exec(
        update(MeshFingerprintContinuation)
        .where(
            col(MeshFingerprintContinuation.file_id) == pending.context.file_id,
            col(MeshFingerprintContinuation.token) == pending.context.token,
        )
        .values(available_at=utcnow() + timedelta(seconds=delay), updated_at=utcnow())
    )


def withdraw(session: Session, pending: MeshContinuation) -> None:
    """An old token never withdraws a retokened continuation."""
    session.exec(
        delete(MeshFingerprintContinuation).where(
            col(MeshFingerprintContinuation.file_id) == pending.context.file_id,
            col(MeshFingerprintContinuation.token) == pending.context.token,
        )
    )


def withdraw_current_job(session: Session, file_id: int) -> None:
    """Cancellation matches the pending row's current stored execution identity."""
    current = (
        select(Job.id)
        .where(
            col(Job.id) == col(MeshFingerprintContinuation.job_id),
            col(Job.execution_epoch)
            == col(MeshFingerprintContinuation.execution_epoch),
            col(Job.attempts) == col(MeshFingerprintContinuation.job_attempt),
            col(Job.kind) == JobKind.DERIVATIVES_MESH,
            col(Job.state).in_(ACTIVE_JOB_STATES),
            col(Job.subject_key) == f"file/{file_id}",
        )
        .exists()
    )
    session.exec(
        delete(MeshFingerprintContinuation).where(
            col(MeshFingerprintContinuation.file_id) == file_id,
            col(MeshFingerprintContinuation.job_id).is_(None) | current,
        )
    )
