"""The derivative source: which Artifacts need a group's derivatives now.

Pull, not push. Ingestion commits a bare Artifact and knows nothing about
derivatives; this source finds the gap with an anti-join of live Artifacts
against their ``artifact_derivatives`` rows at the current recipes. That is
what makes a recipe bump, a new kind or an administrator's "regenerate all"
need no backfill code: the anti-join simply starts matching again.

Every pass is bounded twice. The result is capped by the reconciler's limit
(batch size and lane headroom). The *examined* range is capped too, so a pass
over a fully derived library of any size costs the same:

A bounded newest head and a timestamp-keyset tail find interactive intent.
A separate frozen ID rotation discovers backfill, even while uploads arrive.
Each priority has a finite queue allowance; a durable turn alternates when
both priorities compete for a one-item result.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import String, and_, cast, exists, func, literal, not_, or_, true
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.time import ensure_utc
from app.db.models import (
    ArtifactDerivative,
    DerivativeState,
    File,
    Job,
    JobKind,
    MeshFingerprintContinuation,
    ReconcileCursor,
    WorkPriority,
)
from app.db.scopes import live
from app.modules.work.contracts import DiscoveryBudget, WorkItem
from app.modules.work.jobs import active_job_predicate

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
        if limit <= 0:
            return []
        return self.pending_prioritized(
            session,
            now=now,
            budget=DiscoveryBudget(total=limit, interactive=limit, backfill=limit),
        )

    def pending_prioritized(
        self, session: Session, *, now: datetime, budget: DiscoveryBudget
    ) -> Sequence[WorkItem]:
        if (
            budget.total == 0
            or not policy.resolve(session)[self.group.definition].enabled
        ):
            return []
        cursor = self._cursor(session)
        timestamp = (
            col(File.viewer_requested_at)
            if self.group.definition is JobKind.DERIVATIVES_VIEWER_STL
            else col(File.uploaded_at)
        )
        recent = (
            timestamp.is_not(None)
            if self.group.definition is JobKind.DERIVATIVES_VIEWER_STL
            else timestamp >= now - FRESH_INTERACTIVE
        )
        active = exists().where(
            col(Job.kind) == self.group.definition,
            col(Job.subject_key) == literal("file/") + cast(col(File.id), String),
            active_job_predicate(),
        )
        predicate = and_(pending_predicate(self.group, session, now=now), not_(active))

        def eligible(
            ids: list[int],
            priority: WorkPriority,
            cap: int,
            *,
            newest: bool = False,
            by_time: bool = False,
        ) -> list[int]:
            if not ids or cap == 0:
                return []
            statement = select(File.id).where(
                col(File.id).in_(ids),
                predicate,
                recent if priority is WorkPriority.INTERACTIVE else not_(recent),
            )
            if by_time:
                statement = statement.order_by(
                    timestamp.desc() if newest else timestamp.asc(),
                    col(File.id).desc() if newest else col(File.id),
                )
            else:
                statement = statement.order_by(col(File.id))
            return [
                value
                for value in session.exec(statement.limit(cap + 1)).all()
                if value is not None
            ]

        # Freeze a whole old-library round; arrivals cannot extend its upper bound.
        old_round_enabled = (
            budget.backfill > 0
            or self.group.definition is JobKind.DERIVATIVES_VIEWER_STL
        )
        if old_round_enabled and (
            cursor.scan_high_water == 0
            or cursor.scan_position >= cursor.scan_high_water
        ):
            cursor.scan_high_water = int(
                session.exec(select(func.max(File.id))).one() or 0
            )
            cursor.scan_position = 0
        start = cursor.scan_position
        upper = min(start + WINDOW, cursor.scan_high_water)
        old_ids = [
            value
            for value in session.exec(
                select(File.id)
                .where(col(File.id) > start, col(File.id) <= upper)
                .order_by(col(File.id))
            ).all()
            if value is not None
        ]
        old_i = eligible(old_ids, WorkPriority.INTERACTIVE, budget.interactive)
        old_b = eligible(old_ids, WorkPriority.BACKFILL, budget.backfill)
        tail: list[tuple[int, datetime]] = []
        head_i: list[int] = []
        tail_i: list[int] = []
        if budget.interactive:
            tail_query = select(File.id, timestamp).where(recent)
            if cursor.scan_recent_at is not None:
                tail_query = tail_query.where(
                    or_(
                        timestamp > cursor.scan_recent_at,
                        and_(
                            timestamp == cursor.scan_recent_at,
                            col(File.id) > cursor.scan_recent_file_id,
                        ),
                    )
                )
            tail = [
                (value, ensure_utc(at))
                for value, at in session.exec(
                    tail_query.order_by(timestamp, col(File.id)).limit(WINDOW)
                ).all()
                if value is not None and at is not None
            ]
            head_ids = [
                value
                for value in session.exec(
                    select(File.id)
                    .where(recent)
                    .order_by(timestamp.desc(), col(File.id).desc())
                    .limit(WINDOW)
                ).all()
                if value is not None
            ]
            tail_i = eligible(
                [value for value, _ in tail],
                WorkPriority.INTERACTIVE,
                budget.interactive,
                by_time=True,
            )
            head_i = eligible(
                head_ids,
                WorkPriority.INTERACTIVE,
                budget.interactive,
                newest=True,
                by_time=True,
            )
        candidates = {
            WorkPriority.INTERACTIVE: list(dict.fromkeys(old_i + tail_i + head_i)),
            WorkPriority.BACKFILL: old_b.copy(),
        }
        caps = {
            WorkPriority.INTERACTIVE: budget.interactive,
            WorkPriority.BACKFILL: budget.backfill,
        }
        chosen: set[int] = set()
        items: list[WorkItem] = []
        turn = cursor.discovery_next_priority
        while len(items) < budget.total:
            opposite = (
                WorkPriority.BACKFILL
                if turn is WorkPriority.INTERACTIVE
                else WorkPriority.INTERACTIVE
            )
            priority = next(
                (
                    value
                    for value in (turn, opposite)
                    if caps[value] and candidates[value]
                ),
                None,
            )
            if priority is None:
                break
            file_id = candidates[priority].pop(0)
            if file_id in chosen:
                continue
            chosen.add(file_id)
            caps[priority] -= 1
            items.append(WorkItem(subject_key(file_id), priority=priority))
            turn = (
                WorkPriority.BACKFILL
                if priority is WorkPriority.INTERACTIVE
                else WorkPriority.INTERACTIVE
            )
        cursor.discovery_next_priority = turn
        unreturned_old = [value for value in old_i + old_b if value not in chosen]
        if old_round_enabled:
            # Position equal to the frozen upper bound marks a completed round.
            # Position zero can also mean its first pending subject was not taken.
            cursor.scan_position = min(unreturned_old) - 1 if unreturned_old else upper
        if tail:
            remaining = set(tail_i) - chosen
            consumed: tuple[int, datetime] | None = None
            for row in tail:
                if row[0] in remaining:
                    break
                consumed = row
            if consumed is not None:
                cursor.scan_recent_file_id, cursor.scan_recent_at = consumed
            if not remaining and len(tail) < WINDOW:
                cursor.scan_recent_at = None
                cursor.scan_recent_file_id = None
        elif budget.interactive:
            cursor.scan_recent_at = None
            cursor.scan_recent_file_id = None
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
