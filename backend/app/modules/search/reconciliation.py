"""Restart-safe change watermarks plus rolling source/orphan partitions."""

from datetime import timedelta

from printstash_core.search.passages import SearchSubject, SubjectType
from sqlalchemy import and_, or_, union
from sqlmodel import Session, select

from app.core.time import ensure_utc, utcnow
from app.db.models import (
    Collection,
    SearchDependency,
    SearchPassage,
    SearchReconciliationState,
)
from app.modules.search.dependencies import SUBJECT_MODELS
from app.modules.search.passages import sync_subject


def reconcile_partition(
    session: Session,
    kind: SubjectType,
    *,
    limit: int = 64,
    checkpoint_key: str | None = None,
) -> int:
    """Repair at most three bounded pages and checkpoint in the same transaction.

    The timestamp stream catches ordinary edits quickly. The independent ID
    partition catches relation deletions, ancestor context, unchanged timestamps
    and late commits outside the overlap. The third page removes orphan text.
    """
    if not 1 <= limit <= 1024:
        raise ValueError("search_reconciliation_limit")
    key = checkpoint_key or kind.value
    if len(key) > 32:
        raise ValueError("search_reconciliation_key")
    state = session.exec(
        select(SearchReconciliationState)
        .where(
            SearchReconciliationState.subject_type == key,
        )
        .with_for_update()
    ).first()
    if state is None:
        state = SearchReconciliationState(subject_type=key)
        session.add(state)
        session.flush()
    table = SUBJECT_MODELS[kind]
    timestamp = table.created_at if table is Collection else table.updated_at
    changed = session.exec(
        select(table.id, timestamp)
        .where(
            or_(
                timestamp > state.watermark_at,
                and_(timestamp == state.watermark_at, table.id > state.watermark_id),
            )
        )
        .order_by(timestamp, table.id)
        .limit(limit)
    ).all()
    partition = session.exec(
        select(table.id)
        .where(table.id > state.partition_after_id)
        .order_by(table.id)
        .limit(limit)
    ).all()
    indexed_subjects = union(
        select(SearchPassage.subject_id).where(
            SearchPassage.subject_type == kind.value
        ),
        select(SearchDependency.subject_id).where(
            SearchDependency.subject_type == kind.value
        ),
    ).subquery()
    orphans = session.exec(
        select(indexed_subjects.c.subject_id)
        .where(indexed_subjects.c.subject_id > state.orphan_after_id)
        .order_by(indexed_subjects.c.subject_id)
        .limit(limit)
    ).all()
    subjects = {id for id, _ in changed} | set(partition) | set(orphans)
    for id in sorted(subjects):
        sync_subject(session, SearchSubject(kind, id))
    if changed:
        state.watermark_id, state.watermark_at = changed[-1]
    # Leave a three-second overlap for transactions committing just after this
    # read. A full rolling partition remains the correctness backstop.
    overlap = utcnow() - timedelta(seconds=3)
    if len(changed) < limit and ensure_utc(state.watermark_at) < overlap:
        state.watermark_at, state.watermark_id = overlap, 0
    state.partition_after_id = partition[-1] if len(partition) == limit else 0
    state.orphan_after_id = orphans[-1] if len(orphans) == limit else 0
    state.updated_at = utcnow()
    session.add(state)
    session.flush()
    return len(subjects)
