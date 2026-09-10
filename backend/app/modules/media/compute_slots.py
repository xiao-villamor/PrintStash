"""One durable compute budget shared by thumbnails and geometric analysis."""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Callable, TypeVar

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, col, or_, select

from app.core.config import settings
from app.core.time import utcnow
from app.db.models import ThumbnailRenderSlot

T = TypeVar("T")


def _retry(session: Session, operation: Callable[[], T]) -> T:
    for attempt in range(8):
        try:
            return operation()
        except OperationalError as exc:
            if (
                session.get_bind().dialect.name != "sqlite"
                or "locked" not in str(exc).lower()
                or attempt == 7
            ):
                raise
            session.rollback()
            time.sleep(min(0.005 * 2**attempt, 0.1))
    raise AssertionError("unreachable")


def acquire(
    session: Session,
    token: str,
    *,
    generation_id: int | None = None,
    lease_seconds: int = 900,
) -> ThumbnailRenderSlot | None:
    if not token or not 1 <= lease_seconds <= 900:
        raise ValueError("invalid_compute_lease")
    limit = max(int(settings.max_render_jobs), 1)

    def ensure_slots() -> None:
        existing = set(session.exec(select(ThumbnailRenderSlot.slot_number)).all())
        for number in range(1, limit + 1):
            if number not in existing:
                session.add(ThumbnailRenderSlot(slot_number=number))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()

    _retry(session, ensure_slots)
    now = utcnow()
    available = or_(
        col(ThumbnailRenderSlot.lease_token).is_(None),
        col(ThumbnailRenderSlot.lease_expires_at) < now,
    )

    def claim() -> int | None:
        candidates = session.exec(
            select(ThumbnailRenderSlot.id)
            .where(col(ThumbnailRenderSlot.slot_number) <= limit, available)
            .order_by(col(ThumbnailRenderSlot.slot_number))
            .with_for_update(skip_locked=True)
        ).all()
        for candidate_id in candidates:
            changed = session.connection().execute(
                update(ThumbnailRenderSlot)
                .where(col(ThumbnailRenderSlot.id) == candidate_id, available)
                .values(
                    lease_token=token,
                    generation_id=generation_id,
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    updated_at=now,
                )
            )
            if changed.rowcount == 1:
                session.commit()
                return candidate_id
            session.rollback()
        return None

    slot_id = _retry(session, claim)
    return session.get(ThumbnailRenderSlot, slot_id) if slot_id is not None else None


def release(session: Session, slot_id: int | None, token: str) -> None:
    """Caller commits; a late owner cannot release a successor's permit."""
    if slot_id is None:
        return
    _retry(
        session,
        lambda: session.connection().execute(
            update(ThumbnailRenderSlot)
            .where(
                col(ThumbnailRenderSlot.id) == slot_id,
                ThumbnailRenderSlot.lease_token == token,
            )
            .values(
                generation_id=None,
                lease_token=None,
                lease_expires_at=None,
                updated_at=utcnow(),
            )
        ),
    )


def native_memory_budget_bytes() -> int:
    """Apply the existing render-job RSS policy to optional native inference."""
    from app.modules.media.mesh_processing import _step_memory_budget_bytes

    # Native embeddings remain bounded even when automatic geometry RAM caps are
    # disabled on a platform where cgroup/host memory cannot be detected.
    return min(_step_memory_budget_bytes() or 1024**3, 2 * 1024**3)


def native_process_rss_bytes(pid: int) -> int | None:
    from app.modules.media.mesh_processing import _process_rss_bytes

    return _process_rss_bytes(pid)
