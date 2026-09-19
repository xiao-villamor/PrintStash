"""Durable ingestion commands. SQL owns acceptance; the runtime owns execution.

Command names are versioned and explicitly dispatched. No callable, credential,
or process-local session is serialized. Staged sources retain their existing
identity receipts until the Artifact transaction has committed.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import update
from sqlmodel import Session, col, or_, select

from app.core.time import utcnow
from app.db.models import BackgroundJob, StagingLease

COMMAND_VERSION = 1
LEASE_SECONDS = 120
COMMANDS = frozenset(
    {
        "artifact",
        "verified_upload",
        "url",
        "archive_inspection",
        "archive",
        "file_selection",
        "collection_selection",
        "inbox",
        "capture_enrichment",
    }
)


_execution_claim: ContextVar[tuple[str, str] | None] = ContextVar(
    "ingestion_execution_claim", default=None
)


@contextmanager
def execution_scope(claim):
    token = _execution_claim.set((claim.job_id, claim.token))
    try:
        yield
    finally:
        _execution_claim.reset(token)


def execution_predicate():
    from sqlalchemy import exists, literal

    identity = _execution_claim.get()
    if identity is None:
        return literal(True)
    parent = BackgroundJob.__table__.alias("execution_owner")
    return exists(
        select(1)
        .select_from(parent)
        .where(
            parent.c.id == identity[0],
            parent.c.claim_token == identity[1],
            parent.c.lease_expires_at > utcnow(),
        )
    )


def require_execution_claim(session: Session) -> None:
    identity = _execution_claim.get()
    if identity is None:
        return
    owned = session.exec(
        select(BackgroundJob.id)
        .where(
            BackgroundJob.id == identity[0],
            BackgroundJob.claim_token == identity[1],
            BackgroundJob.lease_expires_at > utcnow(),
        )
        .with_for_update()
    ).first()
    if owned is None:
        raise RuntimeError("ingestion_claim_lost")


class DependencyPending(RuntimeError):
    """A prerequisite has not committed yet; waiting consumes no attempt."""


class CommandDeferred(RuntimeError):
    """The source is safe; optional work retains its durable retry intent."""


def capture_enrichment_job_id(source_job_id: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"capture-enrichment:{source_job_id}").hex


def retains_staging(session: Session, job_id: str) -> bool:
    """Active intake or a dependent capture stage still owns these bytes."""
    return (
        session.exec(
            select(BackgroundJob.id).where(
                col(BackgroundJob.id).in_((job_id, capture_enrichment_job_id(job_id))),
                col(BackgroundJob.replay_safe).is_(True),
                col(BackgroundJob.state).in_(("pending", "running")),
            )
        ).first()
        is not None
    )


def defer(session: Session, claim: CommandClaim, *, waiting: bool = False) -> bool:
    row = session.get(BackgroundJob, claim.job_id)
    if row is None:
        return False
    attempts = row.attempts
    changed = session.connection().execute(
        update(BackgroundJob)
        .where(
            BackgroundJob.id == claim.job_id,
            BackgroundJob.claim_token == claim.token,
            BackgroundJob.lease_expires_at > utcnow(),
        )
        .values(
            next_attempt_at=utcnow()
            + timedelta(seconds=2 if waiting else min(2**attempts, 300)),
            attempts=max(0, attempts - 1) if waiting else attempts,
        )
    )
    session.commit()
    return changed.rowcount == 1 and attempts < 8


def enqueue(
    session: Session, job_id: str, command: str, arguments: dict[str, Any]
) -> None:
    """Join the caller's transaction; the caller commits acceptance once ready."""
    import json

    if command not in COMMANDS:
        raise ValueError("unsupported_ingestion_command")
    row = session.get(BackgroundJob, job_id)
    if row is None or row.state != "pending" or row.claim_token:
        raise ValueError("ingestion_job_not_pending")
    if command == "url":
        from app.core.secrets import encrypt_secret

        arguments = {**arguments, "request": dict(arguments["request"])}
        credential = arguments["request"].pop("thingiverse_cookie", None)
        if credential:
            arguments["request"]["thingiverse_cookie_encrypted"] = encrypt_secret(
                credential
            )
    row.payload_json = json.dumps(
        {"version": COMMAND_VERSION, "command": command, "arguments": arguments},
        separators=(",", ":"),
    )
    row.replay_safe = True
    session.add(row)
    session.flush()


@dataclass(frozen=True)
class CommandClaim:
    job_id: str
    token: str
    command: str
    arguments: dict[str, Any]


def decode(payload: str | None) -> tuple[str, dict[str, Any]] | None:
    import json

    try:
        data = json.loads(payload or "null")
        if (
            not isinstance(data, dict)
            or data.get("version") != COMMAND_VERSION
            or data.get("command") not in COMMANDS
            or not isinstance(data.get("arguments"), dict)
        ):
            return None
        return data["command"], data["arguments"]
    except (ValueError, TypeError):
        return None


def claim_next(session: Session, *, enrichment: bool = False) -> CommandClaim | None:
    now = utcnow()
    eligible = (
        BackgroundJob.kind == "capture_enrichment"
        if enrichment
        else BackgroundJob.kind != "capture_enrichment",
        col(BackgroundJob.replay_safe).is_(True),  # noqa: E712
        col(BackgroundJob.state).in_(("pending", "running")),
        col(BackgroundJob.payload_json).is_not(None),
        or_(
            col(BackgroundJob.lease_expires_at).is_(None),
            BackgroundJob.lease_expires_at <= now,
        ),
        or_(
            col(BackgroundJob.next_attempt_at).is_(None),
            BackgroundJob.next_attempt_at <= now,
        ),
    )
    rows = session.exec(
        select(BackgroundJob)
        .where(*eligible)
        .order_by(BackgroundJob.created_at, BackgroundJob.id)
        .limit(16)
        .with_for_update(skip_locked=True)
    ).all()
    for row in rows:
        command = decode(row.payload_json)
        if command is None:
            continue
        token = uuid.uuid4().hex
        changed = session.execute(
            update(BackgroundJob)
            .execution_options(synchronize_session=False)
            .where(BackgroundJob.id == row.id, *eligible)
            .values(
                claim_token=token,
                lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
                attempts=BackgroundJob.attempts + 1,
            )
        ).rowcount
        if changed:
            result = CommandClaim(row.id, token, *command)
            session.commit()
            return result
    session.rollback()
    return None


def renew(session: Session, claim: CommandClaim) -> bool:
    changed = session.execute(
        update(BackgroundJob)
        .execution_options(synchronize_session=False)
        .where(
            BackgroundJob.id == claim.job_id,
            BackgroundJob.claim_token == claim.token,
            BackgroundJob.lease_expires_at > utcnow(),
        )
        .values(lease_expires_at=utcnow() + timedelta(seconds=LEASE_SECONDS))
    ).rowcount
    if changed:
        owner = (
            claim.arguments.get("source_job_id")
            if claim.command == "capture_enrichment"
            else claim.job_id
        )
        session.execute(
            update(StagingLease)
            .execution_options(synchronize_session=False)
            .where(StagingLease.background_job_id == owner)
            .values(expires_at=utcnow() + timedelta(hours=24))
        )
    session.commit()
    return bool(changed)


def release(session: Session, claim: CommandClaim) -> None:
    session.execute(
        update(BackgroundJob)
        .execution_options(synchronize_session=False)
        .where(
            BackgroundJob.id == claim.job_id, BackgroundJob.claim_token == claim.token
        )
        .values(claim_token=None, lease_expires_at=None)
    )
    session.commit()


def foreground_pending(session: Session) -> bool:
    """Scheduling hint: favor accepted source work over new expensive derivatives."""
    return (
        session.exec(
            select(BackgroundJob.id)
            .where(
                col(BackgroundJob.replay_safe).is_(True),  # noqa: E712
                BackgroundJob.kind != "capture_enrichment",
                col(BackgroundJob.state).in_(("pending", "running")),
            )
            .limit(1)
        ).first()
        is not None
    )
