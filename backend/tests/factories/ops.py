"""Builders for the operational surface: libraries, documents, jobs, audits, sharing.

Grouped together because each is a small table with one or two fields a test
actually cares about, and a lot of columns it does not. The keywords here name
the *state* a test is setting up rather than the column that encodes it —
`scanning=True` on a library, `expired=True` on a share link — because in every
one of these cases the encoding is a timestamp comparison that is easy to get
backwards.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import (
    BackgroundJob,
    Document,
    DocumentKind,
    ExternalLibrary,
    FilamentProfile,
    Model,
    NotificationChannel,
    NotificationTarget,
    ShareLink,
    User,
    VaultAuditFinding,
    VaultAuditFindingState,
    VaultAuditMode,
    VaultAuditRun,
    VaultAuditRunState,
    VaultAuditSeverity,
)
from tests.factories._support import nth, reject_aliases, save, unique_hash

SHARE_TOKEN = "not-a-real-share-token"


def build_external_library(
    session: Session,
    root: Path | str,
    *,
    name: str | None = None,
    scanning: bool = False,
    **overrides: Any,
) -> ExternalLibrary:
    """A mirrored NAS folder at *root*.

    `scanning=True` holds a live scan claim, which is what makes a second scan
    request coalesce onto the running job instead of starting a duplicate walk of
    the same tree. The claim is a token *plus* an expiry *plus* a job id — all
    three are checked, so setting one by hand is a setup that looks right and
    does nothing.
    """
    if scanning:
        overrides.setdefault("scan_claim_token", f"claim-{nth('scan_claim')}")
        overrides.setdefault("scan_claim_expires_at", utcnow() + timedelta(minutes=30))
        overrides.setdefault("scan_job_id", f"scan-job-{nth('scan_job')}")
    return save(
        session,
        ExternalLibrary(
            name=name or f"nas-{nth('library')}",
            root_path=str(root),
            **overrides,
        ),
    )


def build_document(
    session: Session,
    name: str = "manual",
    *,
    kind: DocumentKind = DocumentKind.MARKDOWN,
    trashed: bool = False,
    **overrides: Any,
) -> Document:
    """A document beside the library.

    A markdown document keeps its content in `body`; a binary one (PDF) keeps
    bytes on the storage backend and only `filename`/`size_bytes`/`sha256` here.
    The builder fills whichever set matches `kind`, because a PDF row with a body
    and no filename is a shape the app never produces.
    """
    if kind is DocumentKind.MARKDOWN:
        overrides.setdefault("body", "# Manual\n")
    else:
        overrides.setdefault("filename", f"{name}.pdf")
        overrides.setdefault("size_bytes", 1)
        overrides.setdefault("sha256", unique_hash("document_sha"))
    if trashed:
        overrides.setdefault("deleted_at", utcnow())
    return save(session, Document(name=name, kind=kind, **overrides))


def build_background_job(
    session: Session,
    *,
    kind: str = "generic",
    state: str = "pending",
    owner: User | None = None,
    **overrides: Any,
) -> BackgroundJob:
    """A durable job row. `id` is a string the app generates, not an integer."""
    overrides.setdefault("id", f"job-{nth('background_job')}")
    if owner is not None:
        overrides.setdefault("owner_user_id", owner.id)
    return save(session, BackgroundJob(kind=kind, state=state, **overrides))


def build_audit_run(
    session: Session,
    requested_by: User,
    *,
    mode: VaultAuditMode = VaultAuditMode.QUICK,
    state: VaultAuditRunState = VaultAuditRunState.COMPLETED,
    **overrides: Any,
) -> VaultAuditRun:
    return save(
        session,
        VaultAuditRun(
            requested_by=requested_by.id, mode=mode, state=state, **overrides
        ),
    )


def build_audit_finding(
    session: Session,
    run: VaultAuditRun,
    *,
    code: str = "orphan_blob",
    severity: VaultAuditSeverity = VaultAuditSeverity.WARNING,
    state: VaultAuditFindingState = VaultAuditFindingState.OPEN,
    **overrides: Any,
) -> VaultAuditFinding:
    """One audit finding.

    `code="managed_storage_namespace_escape"` with `state=OPEN` is the one that
    blocks every purge and the whole GC — that combination is a switch, not just
    a record, so it is worth naming deliberately in a test.
    """
    overrides.setdefault("resource_type", "storage")
    overrides.setdefault("resource_identifier", "vault")
    return save(
        session,
        VaultAuditFinding(
            run_id=run.id, code=code, severity=severity, state=state, **overrides
        ),
    )


def build_share_link(
    session: Session,
    model: Model,
    *,
    token: str = SHARE_TOKEN,
    expired: bool = False,
    revoked: bool = False,
    **overrides: Any,
) -> ShareLink:
    """A public read-only link to one model.

    Only the SHA-256 of the token is stored, so a test that wants to *use* the
    link passes the raw token to the endpoint and lets this hash it — comparing
    against the stored hash directly would assert on the storage format instead
    of the behaviour.
    """
    reject_aliases(overrides, {"expires_at": "expired"} if expired else {})
    reject_aliases(overrides, {"revoked_at": "revoked"} if revoked else {})
    overrides.setdefault("token_hash", hashlib.sha256(token.encode()).hexdigest())
    overrides.setdefault(
        "expires_at",
        utcnow() - timedelta(days=1) if expired else utcnow() + timedelta(days=7),
    )
    if revoked:
        overrides.setdefault("revoked_at", utcnow())
    return save(session, ShareLink(model_id=model.id, **overrides))


def build_filament_profile(
    session: Session,
    name: str | None = None,
    *,
    material: str = "PLA",
    **overrides: Any,
) -> FilamentProfile:
    """A filament profile. `name` is unique, so it is generated by default."""
    overrides.setdefault("cost_per_kg", 25.0)
    return save(
        session,
        FilamentProfile(
            name=name or f"profile-{nth('filament_profile')}",
            material_type=material,
            **overrides,
        ),
    )


def build_notification_channel(
    session: Session,
    *,
    target: NotificationTarget = NotificationTarget.WEBHOOK,
    events: list[str] | None = None,
    **overrides: Any,
) -> NotificationChannel:
    """A notification channel.

    An empty `events` list means the channel is subscribed to nothing and will
    never fire, so a test asserting a delivery must name the events it wants.
    """
    overrides.setdefault("name", f"channel-{nth('notification_channel')}")
    overrides.setdefault("events_json", json.dumps(events or []))
    overrides.setdefault(
        "config_json", json.dumps({"url": "https://hooks.invalid/printstash"})
    )
    return save(session, NotificationChannel(target=target, **overrides))
