"""Backup policy constants, lifecycle errors and result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol

from app.core.logging import get_logger
from app.modules.storage.storage_backend.contracts import CreationReceipt

logger = get_logger(__name__)


class BackupStage(StrEnum):
    SNAPSHOTTING = "snapshotting"
    ARCHIVING = "archiving"
    VERIFYING = "verifying"
    PUBLISHING = "publishing"
    FINALIZING = "finalizing"


class BackupProgress(Protocol):
    def __call__(
        self,
        stage: BackupStage,
        *,
        processed: int | None = None,
        total: int | None = None,
        destination: str | None = None,
    ) -> None: ...


class DatabaseBackupNotSupportedError(RuntimeError):
    """Raised when the configured database has no integrated snapshot adapter."""


class BackupOwnershipError(RuntimeError):
    """A backup target lacks current operation-level ownership proof."""


class BackupDeleteUnsupportedError(BackupOwnershipError):
    """The destination has no safe deletion operation for this owned copy."""

    def __init__(self) -> None:
        super().__init__("backup_exact_delete_unsupported")


class _BackupConfigUnstableError(RuntimeError):
    """Settings changed during a target snapshot; retry the next operation."""


@dataclass(frozen=True)
class DatabaseBackupCapability:
    database_backend: str
    create_supported: bool
    restore_supported: bool


MANIFEST_VERSION = "3"

_LEGACY_MANIFEST_V2 = "2"

_SUPPORTED_MANIFEST_VERSIONS = {"1", _LEGACY_MANIFEST_V2, MANIFEST_VERSION}

_RESTORE_JOURNAL_VERSION = 2

_BACKUP_S3_PREFIX = "printstash-backups/"

_LEGACY_BACKUP_S3_PREFIX = "nexus3d-backups/"

_BACKUP_NAME_PREFIX = "printstash-backup-"

_LEGACY_BACKUP_NAME_PREFIX = "nexus3d-backup-"


@dataclass
class BackupMeta:
    id: str
    created_at: str
    size_bytes: int
    storage_backend: str
    file_count: int
    app_version: str
    path: str  # local path to the tar.gz, or S3 key if cloud-only
    location: str = "local"  # "local" | "s3"
    # Content identity is deliberately separate from the human-facing id.  An
    # id is a filename convention and can collide after a copy or migration.
    archive_sha256: str | None = None
    provider_ref: str | None = None
    source_ref: str | None = None
    namespace: str | None = None
    # For a logical id with several sources these make the deterministic
    # precedence visible to operators.  They are presentation metadata only;
    # ``source_ref`` remains the sole authorization for an operation.
    canonical: bool = False
    precedence: int = 0
    run_id: str | None = None
    outcome: str | None = None
    destination_results: list[dict] | None = None


@dataclass
class BackupVerification:
    backup_id: str
    valid: bool
    app_compatible: bool
    manifest_version: str | None
    checked_members: int
    findings: list[dict[str, str | int]]


BackupOwnershipVerificationStatus = Literal[
    "valid", "missing", "inaccessible", "identity", "digest", "corrupt"
]


@dataclass(frozen=True)
class BackupOwnershipVerification:
    """Verification result for one exact ownership-ledger row."""

    ownership_id: int
    status: BackupOwnershipVerificationStatus
    verification: BackupVerification | None = None
    error: str | None = None


@dataclass(frozen=True)
class _StagedBlob:
    key: str
    path: Path
    size: int
    sha256: str
    namespace: str


@dataclass(frozen=True)
class _AppliedBlob:
    key: str
    receipt: CreationReceipt
    sha256: str
    generation: int


@dataclass(frozen=True)
class _RestoreJournalState:
    started: dict[str, object]
    intents: dict[str, dict[str, object]]
    published: dict[str, dict[str, object]]
    generations: dict[str, int]
    database_swap_intent: bool = False
    database_active: bool = False
    complete: bool = False
    cache_paths: set[str] = field(default_factory=set)
