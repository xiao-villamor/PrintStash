"""The lifecycle of ``artifact_derivatives`` rows.

A row exists once a derivation was attempted at a recipe. Its absence at the
current recipe is what "pending" means, so every state here answers one
question for the source: is this kind satisfied for now, or does it need work?

- ``ready`` / ``skipped``: satisfied, unless an administrator regenerated the
  kind after the row was written.
- ``cancelled``: satisfied until a retry or a recipe bump; cancelling withdraws
  intent, so the source must not bring it back.
- ``failed``: waiting out its backoff, then pending again, until the attempts
  are exhausted; then satisfied (terminal) until a retry or a recipe bump.
- ``queued`` / ``running``: in flight. A row left in flight by a lost execution
  for longer than ``STALE_IN_FLIGHT`` is pending again.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.time import ensure_utc, utcnow
from app.db.affected import affected
from app.db.models import (
    ArtifactDerivative,
    DerivativeKind,
    DerivativeRegeneration,
    DerivativeState,
    File,
)
from app.schemas.jobs import DerivativeRead, DerivativeStatus

from .kinds import recipes_for

STALE_IN_FLIGHT = timedelta(hours=1)
_SATISFIED = {DerivativeState.READY, DerivativeState.SKIPPED}


def regenerations(session: Session) -> dict[DerivativeKind, datetime]:
    return {
        row.kind: ensure_utc(row.requested_at)
        for row in session.exec(select(DerivativeRegeneration)).all()
    }


def satisfied(
    row: ArtifactDerivative | None,
    *,
    now: datetime,
    regenerated_at: datetime | None = None,
) -> bool:
    """The Python mirror of the source's SQL predicate, for one row."""
    if row is None:
        return False
    state = row.state
    updated = ensure_utc(row.updated_at)
    if state in _SATISFIED:
        return regenerated_at is None or updated >= regenerated_at
    if state is DerivativeState.CANCELLED:
        return True
    if state is DerivativeState.FAILED:
        if row.attempts >= settings.derivative_max_attempts:
            return True
        return row.next_attempt_at is not None and ensure_utc(row.next_attempt_at) > now
    return now - updated < STALE_IN_FLIGHT


def rows_for(session: Session, file: File) -> dict[DerivativeKind, ArtifactDerivative]:
    """This Artifact's rows at the current recipe of each applicable kind."""
    assert file.id is not None
    recipes = recipes_for(file)
    rows = session.exec(
        select(ArtifactDerivative).where(ArtifactDerivative.file_id == file.id)
    ).all()
    return {
        row.kind: row
        for row in rows
        if row.kind in recipes and row.recipe_version == recipes[row.kind]
    }


def needed(
    session: Session, file: File, kinds: dict[DerivativeKind, int], *, now: datetime
) -> set[DerivativeKind]:
    """Which of ``kinds`` still need deriving for ``file`` right now."""
    current = rows_for(session, file)
    regen = regenerations(session)
    return {
        kind
        for kind in kinds
        if not satisfied(current.get(kind), now=now, regenerated_at=regen.get(kind))
    }


def begin(
    session: Session, file: File, kind: DerivativeKind, recipe: int, *, now: datetime
) -> ArtifactDerivative:
    assert file.id is not None
    row = session.exec(
        select(ArtifactDerivative).where(
            ArtifactDerivative.file_id == file.id,
            ArtifactDerivative.kind == kind,
            ArtifactDerivative.recipe_version == recipe,
        )
    ).first()
    if row is None:
        row = ArtifactDerivative(file_id=file.id, kind=kind, recipe_version=recipe)
    row.state = DerivativeState.RUNNING
    row.attempts += 1
    row.next_attempt_at = None
    row.failure_reason = None
    row.updated_at = now
    session.add(row)
    session.flush()
    return row


def mark_ready(
    session: Session,
    row: ArtifactDerivative,
    *,
    now: datetime,
    storage_key: str | None = None,
    output: dict[str, Any] | None = None,
    duration_ms: int | None = None,
    peak_rss_bytes: int | None = None,
) -> None:
    row.state = DerivativeState.READY
    row.storage_key = storage_key if storage_key is not None else row.storage_key
    row.output_json = json.dumps(output or {}, separators=(",", ":"), default=str)
    row.failure_reason = None
    row.next_attempt_at = None
    row.duration_ms = duration_ms
    row.peak_rss_bytes = peak_rss_bytes
    row.updated_at = now
    session.add(row)


def mark_skipped(
    session: Session, row: ArtifactDerivative, reason: str, *, now: datetime
) -> None:
    """Nothing to produce for these bytes (G-code without an embedded image)."""
    row.state = DerivativeState.SKIPPED
    row.failure_reason = reason
    row.next_attempt_at = None
    row.updated_at = now
    session.add(row)


def mark_failed(
    session: Session,
    row: ArtifactDerivative,
    reason: str,
    *,
    now: datetime,
    deterministic: bool,
    duration_ms: int | None = None,
    peak_rss_bytes: int | None = None,
) -> None:
    """Record a failure; a deterministic one exhausts the attempts at once."""
    row.state = DerivativeState.FAILED
    row.failure_reason = reason
    row.duration_ms = duration_ms
    row.peak_rss_bytes = peak_rss_bytes
    if deterministic:
        row.attempts = max(row.attempts, settings.derivative_max_attempts)
    if row.attempts >= settings.derivative_max_attempts:
        row.next_attempt_at = None
    else:
        backoff = settings.derivative_backoff_seconds * 2 ** max(row.attempts - 1, 0)
        row.next_attempt_at = now + timedelta(seconds=min(backoff, 86400))
    row.updated_at = now
    session.add(row)


def fail_in_flight(
    session: Session, file_id: int, reason: str, *, now: datetime
) -> int:
    """The job's failure hook: in-flight rows of ``file_id`` become failures."""
    rows = session.exec(
        select(ArtifactDerivative).where(
            ArtifactDerivative.file_id == file_id,
            col(ArtifactDerivative.state).in_(
                [DerivativeState.QUEUED, DerivativeState.RUNNING]
            ),
        )
    ).all()
    for row in rows:
        mark_failed(session, row, reason, now=now, deterministic=False)
    return len(rows)


def cancel(
    session: Session, file: File, kinds: dict[DerivativeKind, int], *, now: datetime
) -> None:
    """Withdraw intent: every kind of the group is cancelled at its recipe."""
    current = rows_for(session, file)
    assert file.id is not None
    for kind, recipe in kinds.items():
        row = current.get(kind) or ArtifactDerivative(
            file_id=file.id, kind=kind, recipe_version=recipe
        )
        if row.state in {DerivativeState.READY, DerivativeState.SKIPPED}:
            continue
        row.state = DerivativeState.CANCELLED
        row.failure_reason = None
        row.next_attempt_at = None
        row.updated_at = now
        session.add(row)


def reset(
    session: Session, file: File, kinds: dict[DerivativeKind, int] | None = None
) -> int:
    """Make failed or cancelled kinds pending again (a retry)."""
    assert file.id is not None
    recipes = recipes_for(file)
    wanted = recipes if kinds is None else kinds
    removed = 0
    for kind, recipe in wanted.items():
        removed += affected(
            session,
            delete(ArtifactDerivative).where(
                col(ArtifactDerivative.file_id) == file.id,
                col(ArtifactDerivative.kind) == kind,
                col(ArtifactDerivative.recipe_version) == recipe,
                col(ArtifactDerivative.state).in_(
                    [DerivativeState.FAILED, DerivativeState.CANCELLED]
                ),
            ),
        )
    return removed


def invalidate(session: Session, file: File, kinds: list[DerivativeKind]) -> int:
    """Forget ``kinds`` at their current recipe, whatever their state.

    Used when an output is known to be broken despite its row (an audit found
    the thumbnail object missing): the kind becomes pending again, and the old
    output stays in place until its replacement is published.
    """
    assert file.id is not None
    recipes = recipes_for(file)
    removed = 0
    for kind in kinds:
        if kind not in recipes:
            raise ValueError(f"derivative_not_applicable:{kind}")
        removed += affected(
            session,
            delete(ArtifactDerivative).where(
                col(ArtifactDerivative.file_id) == file.id,
                col(ArtifactDerivative.kind) == kind,
                col(ArtifactDerivative.recipe_version) == recipes[kind],
            ),
        )
    return removed


def read(
    session: Session, file: File, *, now: datetime | None = None
) -> list[DerivativeRead]:
    """Every applicable kind of ``file`` at its current recipe, pending included."""
    now = now or utcnow()
    current = rows_for(session, file)
    regen = regenerations(session)
    reads: list[DerivativeRead] = []
    for kind, recipe in sorted(recipes_for(file).items()):
        row = current.get(kind)
        if row is None:
            reads.append(
                DerivativeRead(
                    kind=kind, recipe_version=recipe, state=DerivativeStatus.PENDING
                )
            )
            continue
        state = DerivativeStatus(row.state.value)
        stale = (
            row.state in _SATISFIED
            and kind in regen
            and ensure_utc(row.updated_at) < regen[kind]
        )
        reads.append(
            DerivativeRead(
                kind=kind,
                recipe_version=recipe,
                state=DerivativeStatus.PENDING if stale else state,
                attempts=row.attempts,
                failure_reason=row.failure_reason,
                updated_at=row.updated_at,
                retryable=row.state
                in {DerivativeState.FAILED, DerivativeState.CANCELLED},
            )
        )
    return reads
