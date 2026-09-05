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
    BackupDestinationResult,
    BackupRetryAttempt,
    BackupRun,
    Document,
    DocumentKind,
    ExternalLibrary,
    ExternalLibraryCheckpoint,
    ExternalLibraryObservation,
    FilamentProfile,
    LibrarySourceKind,
    Model,
    NotificationChannel,
    NotificationTarget,
    RemoteDiscoveryDirectory,
    RemoteDiscoveryEntry,
    RemoteDiscoveryInventory,
    RestoreMarker,
    ShareLink,
    StorageConnection,
    StorageConnectionPurpose,
    StorageFailureDomainDeclaration,
    SystemConfig,
    User,
    VaultAuditFinding,
    VaultAuditFindingState,
    VaultAuditMode,
    VaultAuditRun,
    VaultAuditRunState,
    VaultAuditSeverity,
)
from app.services.storage_identity import StorageTargetIdentity
from tests.factories._support import nth, reject_aliases, save, unique_hash


def build_failure_domain_declaration(
    session: Session,
    target: StorageTargetIdentity,
    *,
    failure_domain: str = "off-site",
    **overrides: Any,
) -> StorageFailureDomainDeclaration:
    return save(
        session,
        StorageFailureDomainDeclaration(
            target_ref=target.target_ref,
            target_identity=target.model_dump_json(),
            failure_domain=failure_domain,
            revision=unique_hash("failure-domain-revision")[:32],
            **overrides,
        ),
    )


def build_system_config(
    session: Session,
    *,
    storage_backend: str | None = None,
    storage_provider: str | None = None,
    s3_root: str | None = None,
    **overrides: Any,
) -> SystemConfig:
    """A persisted runtime configuration row for startup/overlay tests."""
    overrides.setdefault("setup_storage_pending", False)
    return save(
        session,
        SystemConfig(
            storage_backend=storage_backend,
            storage_provider=storage_provider,
            s3_root=s3_root,
            **overrides,
        ),
    )


def build_storage_connection(
    session: Session,
    name: str | None = None,
    *,
    purpose: StorageConnectionPurpose = StorageConnectionPurpose.BACKUP,
    manual_backup_enabled: bool = True,
    automatic_backup_enabled: bool = True,
    **overrides: Any,
) -> StorageConnection:
    """One enabled remote profile with independently selectable backup uses."""
    return save(
        session,
        StorageConnection(
            name=name or nth("storage-connection"),
            kind=LibrarySourceKind.S3,
            purpose=purpose,
            config_json=json.dumps(
                {
                    "provider": "s3",
                    "bucket": "test-backups",
                    "root": "PrintStash",
                    "region": "us-east-1",
                    "endpoint_url": "",
                    "addressing_style": "auto",
                }
            ),
            secret_json=json.dumps(
                {"access_key": "test-access", "secret_key": "test-secret"}
            ),
            manual_backup_enabled=manual_backup_enabled,
            automatic_backup_enabled=automatic_backup_enabled,
            **overrides,
        ),
    )


def build_restore_marker(
    session: Session,
    backup_id: str = "test-backup",
    *,
    state: str = "database_active",
    operation_nonce: str = "a" * 64,
    archive_sha256: str = "b" * 64,
    **overrides: Any,
) -> RestoreMarker:
    """A durable restore PONR marker for recovery tests."""
    return save(
        session,
        RestoreMarker(
            backup_id=backup_id,
            state=state,
            operation_nonce=operation_nonce,
            archive_sha256=archive_sha256,
            **overrides,
        ),
    )


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
    library = save(
        session,
        ExternalLibrary(
            name=name or f"nas-{nth('library')}",
            root_path=str(root),
            **overrides,
        ),
    )
    # Factory rows model an already configured library.  Production creation
    # performs this enrollment explicitly through the API; keeping the marker
    # here prevents every existing scan test from accidentally exercising the
    # legacy-unbound state.
    if "root_identity" not in overrides and Path(root).is_dir():
        from app.services.external_library import enroll_external_root

        enroll_external_root(session, library)
    return library


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


def build_discovery_inventory(
    session: Session, *, prefix: str = "models", **overrides: Any
) -> RemoteDiscoveryInventory:
    return save(
        session,
        RemoteDiscoveryInventory(
            id=unique_hash("inventory")[:32],
            target_ref=unique_hash("target"),
            prefix=prefix,
            **overrides,
        ),
    )


def build_discovery_directory(
    session: Session,
    inventory: RemoteDiscoveryInventory,
    *,
    path: str = "models",
    **overrides: Any,
) -> RemoteDiscoveryDirectory:
    return save(
        session,
        RemoteDiscoveryDirectory(
            inventory_id=inventory.id,
            path=path,
            path_hash=hashlib.sha256(path.encode()).hexdigest(),
            **overrides,
        ),
    )


def build_discovery_entry(
    session: Session,
    directory: RemoteDiscoveryDirectory,
    *,
    key: str = "models/part.gcode",
    size: int = 6,
    **overrides: Any,
) -> RemoteDiscoveryEntry:
    return save(
        session,
        RemoteDiscoveryEntry(
            inventory_id=directory.inventory_id,
            directory_id=directory.id,
            source_key=key,
            key_hash=hashlib.sha256(key.encode()).hexdigest(),
            size=size,
            **overrides,
        ),
    )


def build_library_observation(
    session: Session,
    checkpoint: ExternalLibraryCheckpoint,
    *,
    key: str = "models/part.gcode",
    **overrides: Any,
) -> ExternalLibraryObservation:
    return save(
        session,
        ExternalLibraryObservation(
            checkpoint_id=checkpoint.id,
            key_hash=hashlib.sha256(key.encode()).hexdigest(),
            **overrides,
        ),
    )


def build_backup_run(session: Session, **overrides: Any) -> BackupRun:
    identifier = overrides.pop("id", nth("backup-run"))
    return save(
        session,
        BackupRun(
            id=identifier,
            backup_id=overrides.pop("backup_id", identifier),
            archive_name=overrides.pop("archive_name", f"{identifier}.tar.gz"),
            trigger=overrides.pop("trigger", "manual"),
            storage_backend=overrides.pop("storage_backend", "local"),
            app_version=overrides.pop("app_version", "0.1.0"),
            **overrides,
        ),
    )


def build_backup_destination_result(
    session: Session, run: BackupRun, **overrides: Any
) -> BackupDestinationResult:
    return save(
        session,
        BackupDestinationResult(
            id=overrides.pop("id", nth("backup-result")),
            run_id=run.id,
            kind=overrides.pop("kind", "local"),
            name=overrides.pop("name", "Local backup"),
            **overrides,
        ),
    )


def build_backup_retry_attempt(
    session: Session, result: BackupDestinationResult, **overrides: Any
) -> BackupRetryAttempt:
    return save(
        session,
        BackupRetryAttempt(
            id=overrides.pop("id", nth("backup-retry")),
            destination_result_id=result.id,
            **overrides,
        ),
    )
