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
both priorities compete for a one-item result. When the GPU broker is ready
for covers, interactive mesh work cycles through the smallest, largest and
oldest scan order, with at most one largest-first pick per batch.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import (
    ColumnElement,
    String,
    and_,
    cast,
    exists,
    false,
    func,
    literal,
    not_,
    or_,
    true,
)
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
from app.db.models.work import MeshDiscoveryOrder
from app.db.scopes import live
from app.modules.media.compute_render import render_recipe
from app.modules.work.contracts import DiscoveryBudget, WorkItem
from app.modules.work.jobs import active_job_predicate
from app.runtime.compute import client

from . import policy
from .kinds import DerivativeGroup, subject_key
from .records import STALE_IN_FLIGHT, regenerations

WINDOW = 5000
FRESH_INTERACTIVE = timedelta(minutes=10)
_COVER_PROBE_SECONDS = 0.05


def _cover_gpu_ready() -> bool:
    # Probe the existing owner once per pass. A cold, busy or unavailable GPU
    # leaves discovery in its normal order and never starts a broker here.
    width = settings.model_thumbnail_width
    try:
        return client.render_ready(
            recipe=render_recipe(width, round(width * 3 / 4)),
            deadline=time.monotonic() + _COVER_PROBE_SECONDS,
        )
    except TimeoutError:
        return False


_NEXT_MESH_ORDER = {
    MeshDiscoveryOrder.SMALLEST: MeshDiscoveryOrder.LARGEST,
    MeshDiscoveryOrder.LARGEST: MeshDiscoveryOrder.OLDEST,
    MeshDiscoveryOrder.OLDEST: MeshDiscoveryOrder.SMALLEST,
}


def _mesh_order(
    candidates: dict[int, int],
    cursor: ReconcileCursor,
    chosen: set[int],
) -> Iterator[int]:
    by_order = {
        MeshDiscoveryOrder.SMALLEST: iter(
            sorted(candidates, key=lambda file_id: (candidates[file_id], file_id))
        ),
        MeshDiscoveryOrder.LARGEST: iter(
            sorted(candidates, key=lambda file_id: (-candidates[file_id], file_id))
        ),
        MeshDiscoveryOrder.OLDEST: iter(candidates),
    }
    picked_largest = False
    for _ in candidates:
        turn = cursor.mesh_discovery_order
        order = turn
        if turn is MeshDiscoveryOrder.LARGEST:
            if picked_largest:
                order = MeshDiscoveryOrder.SMALLEST
            picked_largest = True
        file_id = next(value for value in by_order[order] if value not in chosen)
        cursor.mesh_discovery_order = _NEXT_MESH_ORDER[turn]
        yield file_id


def _satisfied(
    kind: str,
    recipe: ColumnElement[int],
    *,
    now: datetime,
    regenerated_at: datetime | None,
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
        not_(
            _satisfied(
                kind,
                group.recipe_column(kind),
                now=now,
                regenerated_at=regen.get(kind),
            )
        )
        for kind in group.kinds
    ]
    if group.definition is JobKind.DERIVATIVES_MESH:
        missing.append(
            exists().where(
                col(MeshFingerprintContinuation.file_id) == col(File.id),
                col(MeshFingerprintContinuation.available_at) <= now,
            )
        )
    return and_(live(File), group.applies(), or_(*missing))


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

    def _recency(self, now: datetime) -> tuple[Any, Any]:
        """The timestamp that orders this group's work, and the interactive test."""
        if self.group.definition is JobKind.DERIVATIVES_VIEWER_STL:
            timestamp = col(File.viewer_requested_at)
            return timestamp, (timestamp.is_not(None) if self.interactive else false())
        timestamp = col(File.uploaded_at)
        if not self.interactive:
            return timestamp, false()
        return timestamp, timestamp >= now - FRESH_INTERACTIVE

    def _active(self) -> Any:
        return exists().where(
            col(Job.kind) == self.group.definition,
            col(Job.subject_key) == literal("file/") + cast(col(File.id), String),
            active_job_predicate(),
        )

    def owes_interactive(self, session: Session, *, now: datetime) -> bool:
        """Whether a recent Artifact still owes outputs and has no active Job.

        A read only: the scan cursor is left to discovery.
        """
        if not policy.resolve(session)[self.group.definition].enabled:
            return False
        _timestamp, recent = self._recency(now)
        owed = session.exec(
            select(File.id)
            .where(
                pending_predicate(self.group, session, now=now),
                not_(self._active()),
                recent,
            )
            .limit(1)
        ).first()
        return owed is not None

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

    # Whether a fresh upload's gap is discovered at INTERACTIVE priority.
    interactive = True

    def prerequisite(self, session: Session) -> ColumnElement[bool]:
        """A File predicate that must hold before this group's gap is owed."""
        return true()

    def pending_prioritized(
        self, session: Session, *, now: datetime, budget: DiscoveryBudget
    ) -> Sequence[WorkItem]:
        if (
            budget.total == 0
            or not policy.resolve(session)[self.group.definition].enabled
        ):
            return []
        size_ordered = (
            budget.interactive > 0
            and self.group.definition is JobKind.DERIVATIVES_MESH
            and _cover_gpu_ready()
        )
        cursor = self._cursor(session)
        # The cursor is advanced as the scan goes. Flushing it before a later
        # read would take the writer for every query that follows; held back,
        # it is one short write as this pass's choice commits.
        with session.no_autoflush:
            timestamp, recent = self._recency(now)
            active = self._active()
            predicate = and_(
                pending_predicate(self.group, session, now=now), not_(active)
            )

            predicate = and_(predicate, self.prerequisite(session))

            def eligible(
                ids: list[int],
                priority: WorkPriority,
                cap: int,
                *,
                newest: bool = False,
                by_time: bool = False,
            ) -> dict[int, int]:
                if not ids or cap == 0:
                    return {}
                by_size = size_ordered and priority is WorkPriority.INTERACTIVE
                statement = select(
                    File.id, File.size_bytes if by_size else literal(0)
                ).where(
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
                # The size rotation must see both extremes of the scanned
                # windows before applying the discovery cap.
                if not by_size:
                    statement = statement.limit(cap + 1)
                return {
                    value: size
                    for value, size in session.exec(statement)
                    if value is not None
                }

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
            head_i: dict[int, int] = {}
            tail_i: dict[int, int] = {}
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
            interactive = old_i | tail_i | head_i
            chosen: set[int] = set()
            candidates = {
                WorkPriority.INTERACTIVE: (
                    _mesh_order(interactive, cursor, chosen)
                    if size_ordered
                    else iter(interactive)
                ),
                WorkPriority.BACKFILL: iter(old_b),
            }
            caps = {
                WorkPriority.INTERACTIVE: min(budget.interactive, len(interactive)),
                WorkPriority.BACKFILL: min(budget.backfill, len(old_b)),
            }
            items: list[WorkItem] = []
            turn = cursor.discovery_next_priority
            while len(items) < budget.total:
                opposite = (
                    WorkPriority.BACKFILL
                    if turn is WorkPriority.INTERACTIVE
                    else WorkPriority.INTERACTIVE
                )
                priority = next(
                    (value for value in (turn, opposite) if caps[value]),
                    None,
                )
                if priority is None:
                    break
                file_id = next(candidates[priority])
                chosen.add(file_id)
                caps[priority] -= 1
                items.append(WorkItem(subject_key(file_id), priority=priority))
                turn = (
                    WorkPriority.BACKFILL
                    if priority is WorkPriority.INTERACTIVE
                    else WorkPriority.INTERACTIVE
                )
            cursor.discovery_next_priority = turn
            unreturned_old = [
                value for value in old_i.keys() | old_b.keys() if value not in chosen
            ]
            if old_round_enabled:
                # Position equal to the frozen upper bound marks a completed round.
                # Position zero can also mean its first pending subject was not taken.
                cursor.scan_position = (
                    min(unreturned_old) - 1 if unreturned_old else upper
                )
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
