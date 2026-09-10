"""Durable bounded scans: normalized scopes, expiring ownership and checkpoints."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import timedelta
from typing import Literal

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, or_, select

from app.core.errors import ErrorKind, OperationError
from app.core.time import utcnow
from app.db.models import (
    Collection,
    ExternalLibrary,
    File,
    FileType,
    Model,
    SimilarityRun,
    User,
)
from app.db.scopes import live
from app.modules.media.fingerprints import ALGORITHM_VERSION
from app.modules.similarity.configuration import read_settings
from app.modules.similarity.fingerprints import encode_json, live_source_predicates
from app.modules.similarity.retrieval import editable_models

Scope = Literal["library", "collections", "models", "sources"]
TERMINAL = ("completed", "cancelled", "failed")
LEASE_SECONDS = 900


def normalize_scope(
    session: Session, actor: User, scope: Scope, ids: list[int]
) -> tuple[int, ...]:
    if scope not in ("library", "collections", "models", "sources"):
        raise OperationError("similarity_scope_invalid")
    if len(ids) > 1000 or any(
        type(value) is not int or not 1 <= value < 2**63 for value in ids
    ):
        raise OperationError("similarity_scope_invalid")
    normalized = tuple(sorted(set(ids)))
    if scope == "library":
        if normalized:
            raise OperationError("similarity_scope_invalid")
        if not actor.is_superuser:
            raise OperationError("admin_required", kind=ErrorKind.FORBIDDEN)
    elif not normalized:
        raise OperationError("similarity_scope_empty")
    elif scope == "sources":
        if not actor.is_superuser:
            raise OperationError("admin_required", kind=ErrorKind.FORBIDDEN)
        found = session.exec(
            select(ExternalLibrary.id).where(col(ExternalLibrary.id).in_(normalized))
        ).all()
        if len(found) != len(normalized):
            raise OperationError(
                "similarity_scope_unavailable", kind=ErrorKind.NOT_FOUND
            )
    elif scope == "models":
        found = session.exec(
            select(Model.id).where(
                col(Model.id).in_(normalized),
                live(Model),
                editable_models(session, actor),
            )
        ).all()
        if len(found) != len(normalized):
            raise OperationError(
                "similarity_scope_unavailable", kind=ErrorKind.NOT_FOUND
            )
    else:
        from app.db.models import CollectionRole
        from app.modules.identity.rbac import accessible_collection_ids

        allowed = accessible_collection_ids(session, actor, CollectionRole.EDIT)
        if not set(normalized) <= allowed:
            raise OperationError(
                "similarity_scope_unavailable", kind=ErrorKind.NOT_FOUND
            )
    return normalized


def start(
    session: Session,
    actor: User,
    *,
    scope: Scope = "library",
    ids: list[int] | None = None,
    trigger: str = "manual",
) -> SimilarityRun:
    normalized = normalize_scope(session, actor, scope, ids or [])
    config = read_settings(session)
    if not config.enabled:
        raise OperationError("similarity_disabled", kind=ErrorKind.CONFLICT)
    # Actor-specific progress must not leak through another editor's conflicts.
    # Fingerprint leases still coalesce work between overlapping actors/scopes.
    key = hashlib.sha256(
        encode_json([actor.id, scope, normalized, ALGORITHM_VERSION]).encode()
    ).hexdigest()
    run = SimilarityRun(
        actor_id=actor.id,
        scope=scope,
        scope_ids_json=encode_json(normalized),
        active_scope_key=key,
        algorithm_version=ALGORITHM_VERSION,
        settings_json=config.model_dump_json(),
        trigger=trigger,
    )
    session.add(run)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise OperationError("similarity_run_active", kind=ErrorKind.CONFLICT) from exc
    session.refresh(run)
    return run


def require(session: Session, actor: User, run_id: int) -> SimilarityRun:
    run = session.get(SimilarityRun, run_id)
    if run is None or not (actor.is_superuser or run.actor_id == actor.id):
        raise OperationError("similarity_run_not_found", kind=ErrorKind.NOT_FOUND)
    normalize_scope(session, actor, run.scope, json.loads(run.scope_ids_json))
    return run


def cancel(session: Session, actor: User, run_id: int) -> SimilarityRun:
    run = require(session, actor, run_id)
    if run.state not in TERMINAL:
        session.connection().execute(
            update(SimilarityRun)
            .where(
                SimilarityRun.id == run_id, col(SimilarityRun.state).not_in(TERMINAL)
            )
            .values(
                cancel_requested=True, state="cancelling", last_activity_at=utcnow()
            )
        )
        session.commit()
        session.refresh(run)
    return run


def claim(
    session: Session, *, cancellations_only: bool = False
) -> tuple[SimilarityRun, str] | None:
    now = utcnow()
    available = or_(
        col(SimilarityRun.lease_token).is_(None),
        col(SimilarityRun.lease_expires_at) <= now,
    )
    conditions = [col(SimilarityRun.state).not_in(TERMINAL), available]
    if cancellations_only:
        conditions.append(SimilarityRun.cancel_requested == True)  # noqa: E712
    ids = session.exec(
        select(SimilarityRun.id)
        .where(*conditions)
        .order_by(SimilarityRun.last_activity_at, SimilarityRun.id)
        .limit(8)
        .with_for_update(skip_locked=True)
    ).all()
    for run_id in ids:
        token = secrets.token_hex(32)
        changed = session.connection().execute(
            update(SimilarityRun)
            .where(
                SimilarityRun.id == run_id,
                col(SimilarityRun.state).not_in(TERMINAL),
                available,
            )
            .values(
                lease_token=token,
                lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
            )
        )
        if changed.rowcount == 1:
            session.commit()
            run = session.get(SimilarityRun, run_id)
            assert run is not None
            session.refresh(run)
            return run, token
        session.rollback()
    return None


def checkpoint(
    session: Session,
    run: SimilarityRun,
    token: str,
    *,
    progress: dict | None = None,
    counters: dict | None = None,
    phase: str | None = None,
    state: str = "running",
    failure_code: str | None = None,
) -> bool:
    """Commit a bounded unit only while its lease is current; cancellation wins."""
    now = utcnow()
    cancelling = session.exec(
        select(SimilarityRun.cancel_requested).where(SimilarityRun.id == run.id)
    ).first()
    if cancelling:
        state = "cancelled"
    values = dict(
        state=state,
        phase=phase or run.phase,
        checkpoint_json=encode_json(
            progress if progress is not None else json.loads(run.checkpoint_json)
        ),
        counters_json=encode_json(
            counters if counters is not None else json.loads(run.counters_json)
        ),
        failure_code=failure_code,
        lease_token=None,
        lease_expires_at=None,
        last_activity_at=now,
        started_at=run.started_at or now,
    )
    if state in TERMINAL:
        values.update(finished_at=now, active_scope_key=None)
    changed = session.connection().execute(
        update(SimilarityRun)
        .where(
            SimilarityRun.id == run.id,
            SimilarityRun.lease_token == token,
            col(SimilarityRun.lease_expires_at) > now,
            col(SimilarityRun.state).not_in(TERMINAL),
        )
        .values(**values)
    )
    session.commit()
    return changed.rowcount == 1


def source_query(session: Session, run: SimilarityRun, actor: User):
    """A cutoff and stable id cursor exclude new arrivals until the next run."""
    query = (
        select(File)
        .join(Model, Model.id == File.model_id)
        .where(
            *live_source_predicates(),
            File.uploaded_at <= run.cutoff,
            col(File.file_type).in_(
                (FileType.STL, FileType.OBJ, FileType.THREE_MF, FileType.STEP)
            ),
            editable_models(session, actor),
        )
    )
    ids = json.loads(run.scope_ids_json)
    if run.scope == "models":
        query = query.where(col(Model.id).in_(ids))
    elif run.scope == "sources":
        query = query.where(col(File.external_library_id).in_(ids))
    elif run.scope == "collections":
        paths = session.exec(
            select(Collection.path).where(col(Collection.id).in_(ids), live(Collection))
        ).all()
        clauses = []
        for path in paths:
            escaped = path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.extend(
                (
                    Collection.path == path,
                    col(Collection.path).like(escaped + "/%", escape="\\"),
                )
            )
        query = query.join(Collection, Collection.id == Model.collection_id).where(
            or_(*clauses)
        )
    return query.order_by(File.id)


def schedule_due(session: Session) -> SimilarityRun | None:
    """Opt-in local cadence; the durable active-scope constraint deduplicates it."""
    from app.core.time import ensure_utc

    config = read_settings(session)
    if not config.enabled or config.schedule_hours == 0:
        return None
    latest = session.exec(
        select(SimilarityRun.created_at)
        .where(SimilarityRun.trigger == "schedule")
        .order_by(col(SimilarityRun.created_at).desc())
        .limit(1)
    ).first()
    if latest is not None and utcnow() < ensure_utc(latest) + timedelta(
        hours=config.schedule_hours
    ):
        return None
    actor = session.exec(
        select(User)
        .where(col(User.is_superuser).is_(True), col(User.is_active).is_(True))
        .order_by(User.id)
        .limit(1)
    ).first()
    if actor is None:
        return None
    try:
        return start(session, actor, trigger="schedule")
    except OperationError as exc:
        if exc.code == "similarity_run_active":
            return None
        raise
