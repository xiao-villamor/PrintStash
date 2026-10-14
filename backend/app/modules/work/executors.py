"""Executor identity and liveness: which processes run work, and are they alive.

Every process registers one ``work_executors`` row and heartbeats it. The
heartbeat also renews the process's fences and publishes how many
write-capable operations it has in flight, which is what lets a restore drain
work running in *other* processes.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import delete, update
from sqlmodel import Session, col, select

from app.core.config import ProcessRole, settings
from app.core.time import ensure_utc, utcnow
from app.db.affected import affected
from app.db.models import LaneName, WorkExecutor
from app.db.session import get_session_factory

_executor_id: str | None = None


def executor_id() -> str:
    """This process's stable executor id.

    ``VAULT_EXECUTOR_ID`` pins it (a worker container that restarts keeps its
    id, so the engine recovers what it was running). Otherwise it is derived
    once per process from role, hostname and a random suffix.
    """
    global _executor_id
    current = _executor_id
    if current is None:
        current = (
            settings.executor_id
            or (
                f"{settings.process_role}-{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
            )[:128]
        )
        _executor_id = current
    return current


def reset_executor_id() -> None:
    global _executor_id
    _executor_id = None


def register(
    *, role: ProcessRole, lanes: Sequence[LaneName], now: datetime | None = None
) -> None:
    now = now or utcnow()
    with get_session_factory().scoped_session() as session:
        row = session.get(WorkExecutor, executor_id())
        if row is None:
            row = WorkExecutor(
                executor_id=executor_id(),
                role=role,
                hostname=socket.gethostname()[:255],
                pid=os.getpid(),
                app_version=settings.app_version,
                started_at=now,
            )
        row.role = role
        row.pid = os.getpid()
        row.app_version = settings.app_version
        row.lanes = ",".join(sorted(lanes))
        row.heartbeat_at = now
        row.active_mutations = 0
        session.add(row)
        session.commit()


def lanes_of(row: WorkExecutor) -> list[LaneName]:
    """The lanes an executor registered; a value outside ``LaneName`` raises."""
    return [LaneName(lane) for lane in row.lanes.split(",")] if row.lanes else []


def heartbeat(*, active_mutations: int, now: datetime | None = None) -> None:
    now = now or utcnow()
    with get_session_factory().scoped_session() as session:
        row = session.get(WorkExecutor, executor_id())
        if row is None:
            return
        row.heartbeat_at = now
        row.active_mutations = max(0, active_mutations)
        session.add(row)
        session.commit()


def deregister() -> None:
    with get_session_factory().scoped_session() as session:
        session.execute(
            delete(WorkExecutor).where(col(WorkExecutor.executor_id) == executor_id())
        )
        session.commit()


def is_stale(row: WorkExecutor, *, now: datetime) -> bool:
    return ensure_utc(row.heartbeat_at) < now - timedelta(
        seconds=settings.jobs_executor_stale_seconds
    )


def live(*, now: datetime | None = None) -> list[WorkExecutor]:
    now = now or utcnow()
    with get_session_factory().scoped_session() as session:
        rows = list(session.exec(select(WorkExecutor)).all())
        for row in rows:
            session.expunge(row)
    return [row for row in rows if not is_stale(row, now=now)]


def _all(session: Session) -> list[WorkExecutor]:
    rows = list(
        session.exec(select(WorkExecutor).order_by(col(WorkExecutor.started_at))).all()
    )
    for row in rows:
        session.expunge(row)
    return rows


def all_executors() -> list[WorkExecutor]:
    with get_session_factory().scoped_session() as session:
        return _all(session)


def stale_ids(
    *, now: datetime | None = None, session: Session | None = None
) -> set[str]:
    """Executors whose heartbeat lapsed, read in ``session`` when one is open."""
    now = now or utcnow()
    rows = all_executors() if session is None else _all(session)
    return {row.executor_id for row in rows if is_stale(row, now=now)}


API_ROLES = (ProcessRole.ALL, ProcessRole.API)


def retire_predecessors(*, now: datetime | None = None) -> int:
    """Mark every other API-process executor stale, now.

    Only the process holding the vault's API lock may call this: one API per
    vault means any other API-role executor is a process that already died
    (a crash, an upgrade, a restart), so the work it held is rerun at once
    instead of after the stale window. Workers are never touched; they share
    no lock and may be alive. The rows stay, backdated, because an executor
    only counts as lost while its row says so.
    """
    now = now or utcnow()
    backdated = now - timedelta(seconds=settings.jobs_executor_stale_seconds + 1)
    with get_session_factory().scoped_session() as session:
        retired = affected(
            session,
            update(WorkExecutor)
            .where(
                col(WorkExecutor.role).in_(API_ROLES),
                col(WorkExecutor.executor_id) != executor_id(),
                col(WorkExecutor.heartbeat_at) > backdated,
            )
            .values(heartbeat_at=backdated),
        )
        session.commit()
    return retired


def forget_stale(*, now: datetime | None = None) -> int:
    """Drop executors that stopped heartbeating long ago (ten stale windows)."""
    now = now or utcnow()
    cutoff = now - timedelta(seconds=settings.jobs_executor_stale_seconds * 10)
    with get_session_factory().scoped_session() as session:
        removed = affected(
            session,
            delete(WorkExecutor).where(col(WorkExecutor.heartbeat_at) < cutoff),
        )
        session.commit()
    return removed
