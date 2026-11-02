"""Bounded secondary discovery with a finite primary-backlog grace period."""

from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, exists
from sqlmodel import Session, col, select

from app.core.time import ensure_utc
from app.db.models import (
    ArtifactDerivative,
    DerivativeKind,
    DerivativeState,
    File,
    Job,
    JobKind,
    ReconcileCursor,
)
from app.modules.work.contracts import DiscoveryBudget
from app.modules.work.jobs import active_job_predicate

from . import policy
from .source import DerivativeSource

PRIMARY_GRACE = timedelta(minutes=2)


class PreviewSource(DerivativeSource):
    # Optional output never competes with a recent upload's first cover.
    interactive = False

    def prerequisite(self) -> ColumnElement[bool]:
        return primary_settled()

    def pending_prioritized(
        self, session: Session, *, now: datetime, budget: DiscoveryBudget
    ):
        if budget.backfill == 0:
            return []
        # One queued/running batch, regardless of the native lane's configured width.
        active = session.exec(
            select(Job.id)
            .where(
                col(Job.kind) == JobKind.DERIVATIVES_PREVIEWS, active_job_predicate()
            )
            .limit(1)
        ).first()
        if active is not None:
            return []
        primary = session.exec(
            select(Job.created_at)
            .where(
                col(Job.kind).in_(
                    (JobKind.DERIVATIVES_MESH, JobKind.DERIVATIVES_GCODE)
                ),
                active_job_predicate(),
            )
            .order_by(col(Job.created_at))
            .limit(1)
        ).first()
        cursor = self._cursor(session)
        if primary is not None:
            if cursor.secondary_deferred_at is None:
                cursor.secondary_deferred_at = now
                session.add(cursor)
                session.commit()
                return []
            if now < ensure_utc(cursor.secondary_deferred_at) + PRIMARY_GRACE:
                return []
        items = super().pending_prioritized(
            session, now=now, budget=DiscoveryBudget(total=1, interactive=0, backfill=1)
        )
        if items:
            cursor.secondary_deferred_at = now
            session.add(cursor)
            session.commit()
        return items

    def next_due(self, session: Session, *, now: datetime) -> datetime | None:
        if not policy.resolve(session)[self.group.definition].enabled:
            return None
        retry = super().next_due(session, now=now)
        cursor = session.get(ReconcileCursor, self.group.definition)
        grace = (
            ensure_utc(cursor.secondary_deferred_at) + PRIMARY_GRACE
            if cursor is not None and cursor.secondary_deferred_at is not None
            else None
        )
        dates = [value for value in (retry, grace) if value is not None and value > now]
        return min(dates) if dates else None


def primary_settled() -> ColumnElement[bool]:
    return exists().where(
        col(ArtifactDerivative.file_id) == col(File.id),
        col(ArtifactDerivative.kind) == DerivativeKind.THUMBNAIL,
        col(ArtifactDerivative.state).in_(
            (
                DerivativeState.READY,
                DerivativeState.SKIPPED,
                DerivativeState.FAILED,
                DerivativeState.CANCELLED,
            )
        ),
    )
