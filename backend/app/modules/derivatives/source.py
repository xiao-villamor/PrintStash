"""The derivative source: which Artifacts need a group's derivatives now.

Pull, not push. Ingestion commits a bare Artifact and knows nothing about
derivatives; this source finds the gap with an anti-join of live Artifacts
against their ``artifact_derivatives`` rows at the current recipes. That is
what makes a recipe bump, a new kind or an administrator's "regenerate all"
need no backfill code: the anti-join simply starts matching again.

Every pass is bounded twice. The result is capped by the reconciler's limit
(batch size and lane headroom). The *examined* range is capped too, so a pass
over a fully derived library of any size costs the same:

1. **Fresh Artifacts** above the high-water mark (newest uploads) are checked
   first, at interactive priority when they are recent, so an upload's
   thumbnail never waits behind a backfill.
2. **A rotating window** of ``WINDOW`` Artifact ids is then checked at
   backfill priority, advancing each pass and wrapping around. Every Artifact
   is re-examined within ``ceil(artifacts / WINDOW)`` passes, which is how
   recipe bumps, regenerations and expired failure backoffs are found.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, exists, func, not_, or_, true
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.time import ensure_utc
from app.db.models import (
    ArtifactDerivative,
    DerivativeState,
    File,
    JobKind,
    MeshFingerprintContinuation,
    ReconcileCursor,
    WorkPriority,
)
from app.db.scopes import live
from app.modules.work.contracts import WorkItem

from . import policy
from .kinds import DerivativeGroup
from .records import STALE_IN_FLIGHT, regenerations

WINDOW = 5000
FRESH_INTERACTIVE = timedelta(minutes=10)


def _satisfied(
    kind: str, recipe: int, *, now: datetime, regenerated_at: datetime | None
) -> Any:
    d = ArtifactDerivative
    done = col(d.state).in_(
        [DerivativeState.READY.value, DerivativeState.SKIPPED.value]
    )
    if regenerated_at is not None:
        done = and_(done, col(d.updated_at) >= regenerated_at)
    return exists().where(
        col(d.file_id) == col(File.id),
        col(d.kind) == kind,
        col(d.recipe_version) == recipe,
        or_(
            done,
            col(d.state) == DerivativeState.CANCELLED.value,
            and_(
                col(d.state) == DerivativeState.FAILED.value,
                or_(
                    col(d.attempts) >= settings.derivative_max_attempts,
                    col(d.next_attempt_at) > now,
                ),
            ),
            and_(
                col(d.state).in_(
                    [DerivativeState.QUEUED.value, DerivativeState.RUNNING.value]
                ),
                col(d.updated_at) > now - STALE_IN_FLIGHT,
                true()
                if regenerated_at is None
                else col(d.updated_at) >= regenerated_at,
            ),
        ),
    )


def pending_predicate(
    group: DerivativeGroup, session: Session, *, now: datetime
) -> Any:
    """Live Artifacts of the group missing any kind at its current recipe."""
    regen = regenerations(session, definitions=[group.definition])
    missing = [
        not_(_satisfied(kind, recipe, now=now, regenerated_at=regen.get(kind)))
        for kind, recipe in group.kinds.items()
    ]
    if group.definition is JobKind.DERIVATIVES_MESH:
        missing.append(
            exists().where(
                col(MeshFingerprintContinuation.file_id) == col(File.id),
                col(MeshFingerprintContinuation.available_at) <= now,
            )
        )
    return and_(live(File), group.applies(), or_(*missing))


def subject_key(file_id: int) -> str:
    return f"file/{file_id}"


def file_id_of(subject: str) -> int:
    prefix, _, value = subject.partition("/")
    if prefix != "file" or not value.isdigit():
        raise ValueError(f"not_a_derivative_subject:{subject}")
    return int(value)


class DerivativeSource:
    """The ``WorkSource`` of one derivative group."""

    def __init__(self, group: DerivativeGroup) -> None:
        self.group = group

    def _cursor(self, session: Session) -> ReconcileCursor:
        cursor = session.get(ReconcileCursor, self.group.definition)
        if cursor is None:
            cursor = ReconcileCursor(source=self.group.definition)
            session.add(cursor)
            session.flush()
        return cursor

    def pending(
        self, session: Session, *, now: datetime, limit: int
    ) -> Sequence[WorkItem]:
        if limit <= 0 or not policy.resolve(session)[self.group.definition].enabled:
            return []
        cursor = self._cursor(session)
        predicate = pending_predicate(self.group, session, now=now)
        items: list[WorkItem] = []
        seen: set[int] = set()

        fresh = session.exec(
            select(File.id, File.uploaded_at)
            .where(predicate, col(File.id) > cursor.scan_high_water)
            .order_by(col(File.id))
            .limit(limit)
        ).all()
        recent = now - FRESH_INTERACTIVE
        for file_id, uploaded_at in fresh:
            assert file_id is not None
            seen.add(file_id)
            items.append(
                WorkItem(
                    subject_key=subject_key(file_id),
                    priority=WorkPriority.INTERACTIVE
                    if self.group.definition is JobKind.DERIVATIVES_VIEWER_STL
                    or ensure_utc(uploaded_at) >= recent
                    else WorkPriority.BACKFILL,
                )
            )
        if len(fresh) < limit:
            top = session.exec(select(func.max(File.id))).one()
            cursor.scan_high_water = int(top or 0)
        elif fresh:
            cursor.scan_high_water = max(seen)

        room = limit - len(items)
        if room > 0:
            start = cursor.scan_position
            window = session.exec(
                select(File.id)
                .where(
                    predicate,
                    col(File.id) > start,
                    col(File.id) <= start + WINDOW,
                    col(File.id) <= cursor.scan_high_water,
                )
                .order_by(col(File.id))
                .limit(room)
            ).all()
            last = start
            for file_id in window:
                assert file_id is not None
                last = file_id
                if file_id in seen:
                    continue
                items.append(
                    WorkItem(
                        subject_key=subject_key(file_id),
                        priority=WorkPriority.INTERACTIVE
                        if self.group.definition is JobKind.DERIVATIVES_VIEWER_STL
                        else WorkPriority.BACKFILL,
                    )
                )
            if len(window) >= room and window:
                cursor.scan_position = last
            else:
                advanced = start + WINDOW
                cursor.scan_position = (
                    0 if advanced >= cursor.scan_high_water else advanced
                )
        session.add(cursor)
        session.commit()
        return items

    def next_due(self, session: Session, *, now: datetime) -> datetime | None:
        """The earliest failure backoff that expires, so a retry needs no tick."""
        if not policy.resolve(session)[self.group.definition].enabled:
            return None
        kinds = list(self.group.kinds)
        due = session.exec(
            select(func.min(ArtifactDerivative.next_attempt_at)).where(
                col(ArtifactDerivative.kind).in_(kinds),
                col(ArtifactDerivative.state) == DerivativeState.FAILED.value,
                col(ArtifactDerivative.next_attempt_at) > now,
            )
        ).one()
        dates = [] if due is None else [ensure_utc(due)]
        if self.group.definition is JobKind.DERIVATIVES_MESH:
            continuation_due = session.exec(
                select(func.min(MeshFingerprintContinuation.available_at))
                .join(File, col(File.id) == col(MeshFingerprintContinuation.file_id))
                .where(
                    live(File),
                    self.group.applies(),
                    col(MeshFingerprintContinuation.available_at) > now,
                )
            ).one()
            if continuation_due is not None:
                dates.append(ensure_utc(continuation_due))
        return min(dates) if dates else None
