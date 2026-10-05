"""Persisted enums shared by domain tables and their public representations."""

from enum import Enum, StrEnum

from app.core.work_priority import WorkPriority as WorkPriority


class FileType(str, Enum):
    STL = "stl"
    THREE_MF = "3mf"
    GCODE = "gcode"
    OBJ = "obj"
    STEP = "step"
    DXF = "dxf"


class FileRevisionStatus(str, Enum):
    KNOWN_GOOD = "known_good"
    NEEDS_TEST = "needs_test"
    FAILED = "failed"
    ARCHIVED = "archived"


# Mapping from filesystem suffix to ``FileType``. Used by ingest routers.
SUFFIX_TO_FILE_TYPE: dict[str, FileType] = {
    ".stl": FileType.STL,
    ".3mf": FileType.THREE_MF,
    ".obj": FileType.OBJ,
    ".step": FileType.STEP,
    ".stp": FileType.STEP,
    ".dxf": FileType.DXF,
    ".gcode": FileType.GCODE,
    ".g": FileType.GCODE,
    ".gco": FileType.GCODE,
    # PrusaSlicer binary G-code: metadata + thumbnail parse like a text G-code.
    ".bgcode": FileType.GCODE,
}


class PrinterStatus(str, Enum):
    UNKNOWN = "unknown"
    OFFLINE = "offline"
    READY = "ready"
    PRINTING = "printing"
    PAUSED = "paused"
    ERROR = "error"


class PrinterProvider(str, Enum):
    MOONRAKER = "moonraker"
    BAMBU_LAN = "bambu_lan"
    PRUSALINK = "prusalink"
    ELEGOO_CENTAURI = "elegoo_centauri"
    OCTOPRINT = "octoprint"


class PrintJobState(str, Enum):
    QUEUED = "queued"
    UPLOADING = "uploading"
    STARTED = "started"
    PRINTING = "printing"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class VaultAuditMode(str, Enum):
    QUICK = "quick"
    FULL = "full"


class VaultAuditRunState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class GcRunState(str, Enum):
    """Durable phases of a destructive garbage-collection decision."""

    PREVIEW = "preview"
    QUARANTINED = "quarantined"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    ABORTED = "aborted"
    BLOCKED = "blocked"


class VaultAuditSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class VaultAuditFindingState(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    IGNORED = "ignored"


class InboxSourceKind(str, Enum):
    URL = "url"
    BROWSER = "browser"
    UPLOAD = "upload"
    EXTERNAL = "external"


class InboxItemState(str, Enum):
    CAPTURED = "captured"
    RESOLVING = "resolving"
    REVIEW = "review"
    IMPORTING = "importing"
    COMPLETED = "completed"
    FAILED = "failed"
    DISMISSED = "dismissed"


class InboxItemCompletion(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"


class InboxItemResultState(str, Enum):
    IMPORTED = "imported"
    DEDUPLICATED = "deduplicated"
    FAILED = "failed"


class CaptureUploadSlotState(str, Enum):
    PENDING = "pending"
    UPLOADED = "uploaded"


class ArtifactUploadState(str, Enum):
    CREATED = "created"
    UPLOADING = "uploading"
    VERIFYING = "verifying"
    INGESTING = "ingesting"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"
    EXPIRED = "expired"


class StorageObjectState(str, Enum):
    PENDING = "pending"
    COMMITTED = "committed"
    BLOCKED = "blocked"


class JobState(StrEnum):
    """Lifecycle of one background Job, shared by every job definition.

    ``interrupted`` is not terminal: the execution was lost (crash, upgrade,
    restore) and the reconciler resubmits the same Job as its next attempt.
    """

    QUEUED = "queued"
    RUNNING = "running"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobKind(StrEnum):
    """Every Job Definition, named ``<owner>.<verb>`` after the owning module.

    A Job's ``kind`` is one of these and nothing else: the catalog refuses a
    definition whose name is not listed here, and the API publishes the list,
    so a client can switch over it exhaustively. Adding a definition adds its
    member here, in the owner's block.
    """

    ADMINISTRATION_AUDIT = "administration.audit"
    BACKUPS_AUTOMATIC = "backups.automatic"
    BACKUPS_CREATE = "backups.create"
    BACKUPS_RETRY_DESTINATION = "backups.retry_destination"
    BACKUPS_TRASH_GC = "backups.trash_gc"
    DERIVATIVES_GCODE = "derivatives.gcode"
    DERIVATIVES_MESH = "derivatives.mesh"
    DERIVATIVES_TOOLPATH = "derivatives.toolpath"
    DERIVATIVES_VIEWER_STL = "derivatives.viewer_stl"
    IDENTITY_RETENTION = "identity.retention"
    INFERENCE_MODEL_DOWNLOAD = "inference.model_download"
    INGESTION_ARCHIVE_INSPECT = "ingestion.archive_inspect"
    INGESTION_ARCHIVE_SELECTION = "ingestion.archive_selection"
    INGESTION_ARTIFACT_UPLOAD = "ingestion.artifact_upload"
    INGESTION_COLLECTION = "ingestion.collection"
    INGESTION_INBOX_IMPORT = "ingestion.inbox_import"
    INGESTION_INBOX_RESOLVE = "ingestion.inbox_resolve"
    INGESTION_INBOX_RETENTION = "ingestion.inbox_retention"
    INGESTION_LIBRARY_IMPORT = "ingestion.library_import"
    INGESTION_UPLOAD = "ingestion.upload"
    INGESTION_UPLOAD_RECOVERY = "ingestion.upload_recovery"
    INGESTION_URL = "ingestion.url"
    INGESTION_URL_SELECTION = "ingestion.url_selection"
    NOTIFICATIONS_DELIVER = "notifications.deliver"
    NOTIFICATIONS_RETENTION = "notifications.retention"
    PRINTING_DISPATCH = "printing.dispatch"
    SEARCH_CAPTION = "search.caption"
    SEARCH_CAPTION_QUEUE = "search.caption_queue"
    SEARCH_EXPAND = "search.expand"
    SEARCH_GENERATION = "search.generation"
    SEARCH_INDEX = "search.index"
    SEARCH_PROJECT = "search.project"
    SEARCH_REPAIR = "search.repair"
    SIMILARITY_ANALYZE = "similarity.analyze"
    SOURCES_SCAN = "sources.scan"
    STORAGE_INVENTORY = "storage.inventory"
    STORAGE_MIGRATE = "storage.migrate"
    WORK_HOUSEKEEPING = "work.housekeeping"


class LaneName(StrEnum):
    """A lane: a concurrency class of work, and the engine queue behind it."""

    CAPTIONS = "captions"
    DERIVE_LIGHT = "derive.light"
    DERIVE_NATIVE = "derive.native"
    EXPANSION = "expansion"
    INGEST = "ingest"
    MAINTENANCE = "maintenance"
    NETWORK = "network"
    NOTIFY = "notify"
    PRINTING = "printing"
    RECONCILE = "reconcile"
    SEARCH = "search"
    SIMILARITY = "similarity"


class DerivativeKind(StrEnum):
    """An output derived from an Artifact's bytes, each at its own recipe."""

    METADATA = "metadata"
    THUMBNAIL = "thumbnail"
    TOOLPATH = "toolpath"
    VIEWER_STL = "viewer_stl"


class DerivativeState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    READY = "ready"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"


class IngestionEntryState(StrEnum):
    PENDING = "pending"
    IMPORTED = "imported"
    DEDUPLICATED = "deduplicated"
    FAILED = "failed"
    SKIPPED = "skipped"


class IngestRequestKind(StrEnum):
    """What an accepted ingest request asks the ``ingest`` jobs to do."""

    UPLOAD = "upload"
    URL = "url"
    ARCHIVE_INSPECT = "archive_inspect"
    ARCHIVE_SELECTION = "archive_selection"
    URL_SELECTION = "url_selection"
    COLLECTION = "collection"


class CaptureProvider(str, Enum):
    MYMINIFACTORY = "myminifactory"
    CULTS = "cults"


class RoutingStrategy(str, Enum):
    MANUAL = "manual"
    DEFAULT = "default"
    LEAST_BUSY = "least_busy"


class MaterialSlotState(str, Enum):
    LOADED = "loaded"
    EMPTY = "empty"
    UNKNOWN = "unknown"


class MaterialSource(str, Enum):
    MANUAL = "manual"
    BAMBU_AMS = "bambu_ams"
    MOONRAKER_SPOOLMAN = "moonraker_spoolman"


class JobPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    RUSH = "rush"


class CompatibilityPolicy(str, Enum):
    SAFE = "safe"
    ALLOW_MISMATCH = "allow_mismatch"


class OperatorGateState(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    RELEASED = "released"
    HELD = "held"


class CollectionRole(str, Enum):
    VIEW = "view"
    EDIT = "edit"
    ADMIN = "admin"


class PrinterRole(str, Enum):
    """Ordered access levels for one physical printer."""

    VIEW = "view"
    PRINT = "print"
    CONTROL = "control"
    ADMIN = "admin"


class NotificationEventType(str, Enum):
    """Lifecycle events users can subscribe to.

    ``print_cancelled`` is split from ``print_failed`` so a user-initiated
    cancellation can be muted without silencing genuine print failures.
    """

    PRINT_COMPLETED = "print_completed"
    PRINT_FAILED = "print_failed"
    PRINT_CANCELLED = "print_cancelled"
    PRINTER_OFFLINE = "printer_offline"
    STORAGE_REGRESSION = "storage_regression"
    STORAGE_RECOVERY = "storage_recovery"
    STORAGE_AUDIT_FAILED = "storage_audit_failed"
    STORAGE_AUDIT_CANCELLED = "storage_audit_cancelled"
    STORAGE_AUDIT_OVERDUE = "storage_audit_overdue"
    STORAGE_REPAIR_FAILED = "storage_repair_failed"
    VAULT_MIGRATION = "vault_migration"


class NotificationTarget(str, Enum):
    """Where a notification is delivered."""

    WEBHOOK = "webhook"
    DISCORD = "discord"
    TELEGRAM = "telegram"
    NTFY = "ntfy"


class NotificationDeliveryStatus(str, Enum):
    PENDING = "pending"  # queued, awaiting first/next attempt
    SENDING = "sending"  # claimed by a dispatcher, in flight
    SENT = "sent"  # delivered successfully
    FAILED = "failed"  # gave up after exhausting retries


class DocumentKind(str, Enum):
    MARKDOWN = "markdown"  # editable in-app, content in ``body``
    PDF = "pdf"  # binary blob under document_file_key
    OTHER = "other"  # any other uploaded binary


class ExternalLibraryCollectionMode(str, Enum):
    """How a scanned file's NAS subfolder maps to a vault Collection."""

    # Mirror the folder tree: ``{root}/functional/brackets/x.stl`` -> "functional/brackets".
    MIRROR = "mirror"
    # Drop everything into one fixed target collection (``target_collection_id``).
    SINGLE = "single"


class ExternalLibraryScanStatus(str, Enum):
    OK = "ok"
    ERROR = "error"
    RUNNING = "running"
    # Scan completed but one or more files failed to index — terminal, like OK,
    # but surfaces the partial failure instead of a misleading green status.
    PARTIAL = "partial"


class ExternalLibraryWatchMode(str, Enum):
    """Whether a library is watched for real-time changes (watchfiles).

    Real-time watching only works on local filesystems; on network mounts
    (NFS/SMB/CIFS) the kernel does not deliver inotify events, so the library
    falls back to its scheduled scan. ``AUTO`` decides from the detected
    filesystem; ``EVENTS``/``OFF`` are explicit user overrides.
    """

    # Watch only when the root is on a local filesystem (auto-detected).
    AUTO = "auto"
    # Force watching regardless of detected filesystem.
    EVENTS = "events"
    # Never watch; rely solely on the schedule / manual scans.
    OFF = "off"


class LibrarySourceKind(str, Enum):
    MOUNTED = "mounted"
    S3 = "s3"
    WEBDAV = "webdav"
    SFTP = "sftp"
    GDRIVE = "gdrive"


class StorageConnectionPurpose(str, Enum):
    """Product workflows allowed to reuse one remote-storage connection."""

    LIBRARY = "library"
    BACKUP = "backup"
    BOTH = "both"

    def allows(self, required: "StorageConnectionPurpose") -> bool:
        """Return whether this profile may serve the requested workflow."""
        return self in {required, StorageConnectionPurpose.BOTH}


# Sentinel hashes for external (non-vault) print jobs.
SENTINEL_MODEL_HASH = "ext-model-sentinel-000000000000000000000000000000000000000000"

SENTINEL_FILE_HASH = "ext-file-sentinel-0000000000000000000000000000000000000000000"
