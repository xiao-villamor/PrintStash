"""SQL persistence for private, expiring import review manifests.

The manifest holds metadata. A staged archive retains a separate exact ownership
receipt; accepting its command transfers that receipt in the source transaction.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, update
from sqlmodel import Session, col, or_, select

from app.core.time import utcnow
from app.db.models import BackgroundJob, IngestionReview, StagingLease
from app.db.session import get_session_factory
from app.modules.storage.hashing import sha256_file
from app.runtime.jobs import registry


def save(kind: str, payload: dict[str, Any], owner_id: int | None, *, staged: Path | None = None) -> str:
    token = uuid.uuid4().hex
    expires = datetime.fromtimestamp(payload.get("created_at", utcnow().timestamp()), timezone.utc) + timedelta(hours=1)
    info = staged.stat(follow_symlinks=False) if staged else None
    digest = sha256_file(staged) if staged else None
    with get_session_factory().scoped_session() as session:
        source_job = None
        if staged is not None:
            lease = session.exec(select(StagingLease).where(StagingLease.path == str(staged))).first()
            if lease is not None:
                source_job = lease.background_job_id
            else:
                source_job = registry.create(owner_id, visible=False, kind="archive_review", session=session)
                row = session.get(BackgroundJob, source_job)
                assert row is not None
                row.state = "completed"
                row.status_json = '{"state":"completed"}'
                row.finished_at = utcnow()
                assert info is not None and digest is not None
                session.add(StagingLease(id=uuid.uuid4().hex, path=str(staged), owner_user_id=owner_id,
                    background_job_id=source_job, size_bytes=info.st_size, sha256=digest,
                    device=info.st_dev, inode=info.st_ino, ctime_ns=info.st_ctime_ns, expires_at=expires))
        session.add(IngestionReview(id=token, kind=kind, owner_user_id=owner_id,
            payload_json=json.dumps(payload), source_job_id=source_job, expires_at=expires))
        session.commit()
    return token


def get(kind: str, token: str, *, claim: bool = False) -> dict[str, Any] | None:
    with get_session_factory().scoped_session() as session:
        row = session.exec(select(IngestionReview).where(IngestionReview.id == token, IngestionReview.kind == kind, IngestionReview.expires_at > utcnow())).first()
        if row is None:
            return None
        if row.source_job_id:
            # A committed handoff may precede deletion of its review row by a
            # process crash. Never offer that already-accepted archive twice.
            lease = session.exec(select(StagingLease.id).where(StagingLease.background_job_id == row.source_job_id)).first()
            if lease is None:
                return None
        if claim:
            changed = session.connection().execute(update(IngestionReview).where(
                IngestionReview.id == token,
                or_(col(IngestionReview.claim_expires_at).is_(None), IngestionReview.claim_expires_at <= utcnow()),
            ).values(claim_expires_at=utcnow() + timedelta(minutes=5)))
            if changed.rowcount != 1:
                return None
            session.commit()
        return json.loads(row.payload_json)


def remove(kind: str, token: str) -> None:
    with get_session_factory().scoped_session() as session:
        row = session.get(IngestionReview, token)
        if row is not None and row.kind == kind:
            session.delete(row)
            session.commit()


def consume(session: Session, kind: str, token: str) -> bool:
    """Accept once, atomically with the caller's command transaction."""
    result = session.connection().execute(delete(IngestionReview).where(
        IngestionReview.id == token, IngestionReview.kind == kind,
        IngestionReview.expires_at > utcnow(),
    ))
    return result.rowcount == 1


def prune_expired(session: Session) -> int:
    result = session.connection().execute(delete(IngestionReview).where(IngestionReview.expires_at <= utcnow()))
    return result.rowcount
