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
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import (
    ArtifactDerivative,
    ArtifactUploadPart,
    ArtifactUploadSession,
    ArtifactUploadState,
    BackupDestinationResult,
    BackupRetryAttempt,
    BackupRun,
    DerivativeGroupRegeneration,
    DerivativeKind,
    DerivativeState,
    Document,
    DocumentKind,
    ExternalLibrary,
    ExternalLibraryCheckpoint,
    ExternalLibraryObservation,
    FilamentProfile,
    File,
    IngestRequest,
    IngestRequestKind,
    Job,
    JobKind,
    JobState,
    LibrarySourceKind,
    Model,
    NotificationChannel,
    NotificationTarget,
    PrinterProfile,
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
    VaultAuditEvent,
    VaultAuditFinding,
    VaultAuditFindingState,
    VaultAuditMode,
    VaultAuditPolicy,
    VaultAuditRun,
    VaultAuditRunState,
    VaultAuditSeverity,
    WorkExecutor,
    WorkFence,
)
from app.modules.storage.storage_identity import StorageTargetIdentity
from tests.factories._support import nth, reject_aliases, save, unique_hash

if TYPE_CHECKING:
    from app.db.models import MeshFingerprintContinuation


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
    overrides.setdefault("vault_edit_version", 1)
    overrides.setdefault("search_edit_version", 1)
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
    overrides.setdefault("edit_version", 1)
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
        from app.modules.sources.root_binding import enroll_external_root

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
    overrides.setdefault("edit_version", 1)
    if kind is DocumentKind.MARKDOWN:
        overrides.setdefault("body", "# Manual\n")
    else:
        overrides.setdefault("filename", f"{name}.pdf")
        overrides.setdefault("size_bytes", 1)
        overrides.setdefault("sha256", unique_hash("document_sha"))
    if trashed:
        overrides.setdefault("deleted_at", utcnow())
    return save(session, Document(name=name, kind=kind, **overrides))


def build_job(
    session: Session,
    *,
    kind: str = JobKind.WORK_HOUSEKEEPING,
    state: JobState = JobState.QUEUED,
    owner: User | None = None,
    subject: str | None = None,
    finished: bool | None = None,
    **overrides: Any,
) -> Job:
    """A Job row: one definition's work on one subject.

    ``kind`` must name a registered definition, or the reconciler treats the
    row as an orphan and fails it. ``subject`` defaults to a unique key: two
    active Jobs of one kind on one subject violate the active-subject index,
    which is the claim the reconciler relies on. ``finished`` defaults to what
    ``state`` implies, so a terminal Job has the ``finished_at`` retention reads.
    """
    reject_aliases(
        overrides,
        {"subject_key": "subject", "owner_user_id": "owner", "finished_at": "finished"},
    )
    overrides.setdefault("id", f"job-{nth('job')}")
    overrides.setdefault("execution_epoch", uuid4().hex)
    if overrides.get("attempts", 0) > 0:
        overrides.setdefault("submitted_epoch", overrides["execution_epoch"])
    terminal = state in {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}
    if finished if finished is not None else terminal:
        overrides["finished_at"] = overrides.get("updated_at") or utcnow()
    return save(
        session,
        Job(
            kind=kind,
            state=state,
            subject_key=subject or f"test/{nth('job_subject')}",
            owner_user_id=owner.id if owner is not None else None,
            **overrides,
        ),
    )


def build_ingest_request(
    session: Session,
    owner: User,
    *,
    kind: IngestRequestKind = IngestRequestKind.URL,
    state: JobState = JobState.QUEUED,
    **overrides: Any,
) -> IngestRequest:
    """An accepted ingest request with the queued Job that owns it.

    A request is the intent of exactly one ``ingest.*`` Job: its primary key is
    the Job's id and the Job's subject names it. A request without that Job, or
    a Job of the wrong definition, is a shape no route produces and one the
    reconciler would fail as an orphan.
    """
    from app.modules.ingestion.requests import DEFINITIONS, subject_key

    reject_aliases(overrides, {"owner_user_id": "owner"})
    job_id = f"ingest-{nth('ingest_request')}"
    build_job(
        session,
        kind=DEFINITIONS[kind],
        state=state,
        owner=owner,
        subject=subject_key(job_id),
        id=job_id,
    )
    overrides.setdefault("selection_json", "{}")
    return save(
        session,
        IngestRequest(job_id=job_id, kind=kind, owner_user_id=owner.id, **overrides),
    )


def build_derivative_group_regeneration(
    session: Session, definition: JobKind, kind: DerivativeKind, **overrides: Any
) -> DerivativeGroupRegeneration:
    """A regeneration scoped to the producer that was enabled when requested."""
    return save(
        session,
        DerivativeGroupRegeneration(definition=definition, kind=kind, **overrides),
    )


def build_derivative(
    session: Session,
    file: File,
    kind: DerivativeKind,
    *,
    state: DerivativeState = DerivativeState.READY,
    recipe_version: int | None = None,
    exhausted: bool = False,
    **overrides: Any,
) -> ArtifactDerivative:
    """One derivative row of an Artifact, at the kind's current recipe by default.

    ``exhausted=True`` makes a failure terminal: the source reads a failed row
    as pending again once its backoff expires, unless its attempts reached
    ``derivative_max_attempts``. A test that means "given up" and writes a
    failed row with one attempt is asserting against a row the source retries.
    """
    from app.core.config import settings
    from app.modules.derivatives.kinds import recipes_for

    assert file.id is not None
    reject_aliases(overrides, {"file_id": "file"})
    if recipe_version is None:
        recipe_version = recipes_for(file)[kind]
    if exhausted:
        overrides.setdefault("attempts", settings.derivative_max_attempts)
    overrides.setdefault("attempts", 1)
    if state is DerivativeState.RUNNING:
        from uuid import uuid4

        overrides.setdefault("attempt_token", str(uuid4()))
    if state in (DerivativeState.FAILED, DerivativeState.SKIPPED):
        # The database refuses a failure or skip that does not say why.
        overrides.setdefault("failure_reason", f"test_{state.value}")
    return save(
        session,
        ArtifactDerivative(
            file_id=file.id,
            kind=kind,
            recipe_version=recipe_version,
            state=state,
            **overrides,
        ),
    )


def build_work_fence(
    session: Session,
    name: str,
    *,
    holder: str = "another-executor",
    expired: bool = False,
    **overrides: Any,
) -> WorkFence:
    """A fence another process holds. ``expired=True`` is one whose holder died."""
    reject_aliases(overrides, {"expires_at": "expired"})
    now = utcnow()
    expires_at = now - timedelta(seconds=1) if expired else now + timedelta(hours=1)
    return save(
        session,
        WorkFence(
            name=name,
            holder=holder,
            reason=overrides.pop("reason", "test"),
            expires_at=expires_at,
            **overrides,
        ),
    )


def build_work_executor(
    session: Session,
    executor_id: str | None = None,
    *,
    stale: bool = False,
    **overrides: Any,
) -> WorkExecutor:
    """Another process that runs jobs. ``stale=True`` stopped heartbeating."""
    from app.core.config import settings

    reject_aliases(overrides, {"heartbeat_at": "stale"})
    beat = utcnow()
    if stale:
        beat -= timedelta(seconds=settings.jobs_executor_stale_seconds + 1)
    overrides.setdefault("role", "worker")
    overrides.setdefault("hostname", "worker-host")
    overrides.setdefault("pid", 4242)
    overrides.setdefault("app_version", settings.app_version)
    return save(
        session,
        WorkExecutor(
            executor_id=executor_id or f"worker-{nth('executor')}",
            heartbeat_at=beat,
            **overrides,
        ),
    )


def build_artifact_upload(
    session: Session,
    owner: User,
    *,
    state: ArtifactUploadState = ArtifactUploadState.CREATED,
    **overrides: Any,
) -> ArtifactUploadSession:
    """One owner-bound resumable upload with a future expiry by default."""

    overrides.setdefault("id", f"upload-{nth('artifact_upload')}")
    overrides.setdefault("purpose", "model")
    overrides.setdefault("target_role", "new_model")
    overrides.setdefault("filename", "part.stl")
    overrides.setdefault("media_type", "model/stl")
    overrides.setdefault("declared_size", 4)
    overrides.setdefault("adapter_id", "api_chunks")
    overrides.setdefault("expires_at", utcnow() + timedelta(hours=24))
    return save(
        session,
        ArtifactUploadSession(
            owner_user_id=owner.id,
            state=state,
            **overrides,
        ),
    )


def build_artifact_upload_part(
    session: Session,
    upload: ArtifactUploadSession,
    *,
    part_number: int = 1,
    **overrides: Any,
) -> ArtifactUploadPart:
    """A durable receipt for one chunk or provider-native part."""

    overrides.setdefault("byte_offset", (part_number - 1) * upload.declared_size)
    overrides.setdefault("size_bytes", upload.declared_size)
    overrides.setdefault("sha256", unique_hash("artifact_upload_part"))
    return save(
        session,
        ArtifactUploadPart(
            session_id=upload.id,
            part_number=part_number,
            **overrides,
        ),
    )


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
    overrides.setdefault("edit_version", 1)
    overrides.setdefault("edit_identity", uuid4().hex)
    return save(
        session,
        FilamentProfile(
            name=name or f"profile-{nth('filament_profile')}",
            material_type=material,
            **overrides,
        ),
    )


def build_printer_profile(
    session: Session, name: str | None = None, **overrides: Any
) -> PrinterProfile:
    """A local slicer preset with a unique name."""
    overrides.setdefault("edit_version", 1)
    overrides.setdefault("edit_identity", uuid4().hex)
    return save(
        session,
        PrinterProfile(
            name=name or f"printer-profile-{nth('printer_profile')}", **overrides
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


def build_audit_policy(
    session: Session, requested_by: User, *, mode: str = "quick", **overrides: Any
) -> VaultAuditPolicy:
    """A disabled policy unless its caller explicitly enables it."""
    overrides.setdefault("cadence", "weekly" if mode == "quick" else "monthly")
    return save(
        session, VaultAuditPolicy(mode=mode, requested_by=requested_by.id, **overrides)
    )


def build_audit_event(
    session: Session, run: VaultAuditRun, **overrides: Any
) -> VaultAuditEvent:
    """A uniquely deduplicated aggregate storage event."""
    overrides.setdefault("dedup_key", f"audit-event-{nth('audit_event')}")
    overrides.setdefault("event_type", "storage_regression")
    return save(session, VaultAuditEvent(run_id=run.id, **overrides))


def build_job_context(job_id: str):
    """Claim a real stored Job before invoking a worker body directly in a test."""
    from app.db.session import get_session_factory
    from app.modules.work.runner import ExecutionContext, _begin
    from app.modules.work.submission import execution_id

    with get_session_factory().scoped_session() as session:
        job = session.get(Job, job_id)
        assert job is not None, f"worker context requires stored Job: {job_id}"
        attempt = (
            job.attempts
            if job.state in {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}
            else max(1, job.attempts)
        )
        context = ExecutionContext(
            job.id,
            job.kind,
            job.subject_key,
            job.priority,
            execution_id(job.id, attempt, job.execution_epoch),
            attempt,
            job.execution_epoch,
        )
    _begin(job_id, attempt, context.execution_epoch)
    return context


def build_mesh_continuation(
    session: Session, file: File, *, job: Job | None = None, **overrides: Any
) -> MeshFingerprintContinuation:
    """Durable mesh analysis input, separate from completed basic outputs."""
    from app.core.config import settings
    from app.db.models import MeshFingerprintContinuation
    from app.modules.derivatives.kinds import recipes_for
    from app.modules.derivatives.mesh_continuation_values import encode_source
    from app.modules.derivatives.records import ArtifactSource
    from app.modules.media.fingerprints import ALGORITHM_VERSION

    assert file.id is not None
    reject_aliases(overrides, {"file_id": "file"})
    overrides.setdefault("token", str(uuid4()))
    overrides.setdefault("source_sha256", file.sha256)
    overrides.setdefault("metadata_recipe", recipes_for(file)[DerivativeKind.METADATA])
    overrides.setdefault("algorithm_version", ALGORITHM_VERSION)
    overrides.setdefault("triangle_cap", settings.similarity_triangle_cap)
    overrides.setdefault("source_identity_json", encode_source(ArtifactSource.of(file)))
    overrides.setdefault("regenerated_at", None)
    overrides.setdefault("job_id", None if job is None else job.id)
    overrides.setdefault(
        "execution_epoch", None if job is None else job.execution_epoch
    )
    overrides.setdefault("job_attempt", None if job is None else job.attempts)
    return save(session, MeshFingerprintContinuation(file_id=file.id, **overrides))


def build_job_history(
    session: Session, *, count: int, kind: JobKind, subject: str
) -> None:
    """Seed terminal history from the canonical Job builder in one bulk batch.

    The original row supplies valid state, timestamps and attempt authority;
    historical copies differ only in their independent Job identities.
    """
    from sqlalchemy import insert

    if type(count) is not int or count <= 0:
        raise ValueError("history count must be a positive integer")
    template = build_job(
        session, kind=kind, state=JobState.COMPLETED, subject=subject, attempts=1
    )
    values = template.model_dump()
    if count > 1:
        session.execute(
            insert(Job),
            [dict(values, id=uuid4().hex) for _ in range(count - 1)],
        )
        session.commit()


def build_ingestion_entry(session: Session, job: Job, **overrides: Any):
    """A pending frozen unit under a real Job, ready for schema-boundary tests."""
    from app.db.models import IngestionEntry
    from app.modules.ingestion.batch_contracts import (
        LocalSource,
        encode_source_descriptor,
    )

    identity = overrides.pop("identity", f"entry-{nth('ingestion_entry')}")
    values = {
        "job_id": job.id,
        "entry_key": hashlib.sha256(identity.encode()).hexdigest(),
        "identity": identity,
        "display_name": "part.stl",
        "descriptor_json": encode_source_descriptor(LocalSource("factory")),
        "ordinal": 0,
    }
    values.update(overrides)
    return save(session, IngestionEntry(**values))
