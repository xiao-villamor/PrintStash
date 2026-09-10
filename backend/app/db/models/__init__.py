"""Register every domain table and expose the shared SQLModel metadata."""

from .administration import (
    AuditLog as AuditLog,
)
from .administration import (
    SystemConfig as SystemConfig,
)
from .administration import VaultAuditEvent as VaultAuditEvent
from .administration import (
    VaultAuditFinding as VaultAuditFinding,
)
from .administration import VaultAuditPolicy as VaultAuditPolicy
from .administration import (
    VaultAuditRun as VaultAuditRun,
)
from .artifact_uploads import ArtifactUploadPart as ArtifactUploadPart
from .artifact_uploads import ArtifactUploadSession as ArtifactUploadSession
from .backups import (
    BackupDestinationResult as BackupDestinationResult,
)
from .backups import (
    BackupRetryAttempt as BackupRetryAttempt,
)
from .backups import (
    BackupRun as BackupRun,
)
from .backups import (
    GcItem as GcItem,
)
from .backups import (
    GcRun as GcRun,
)
from .base import (
    SQLModel as SQLModel,
)
from .capacity import CapacityAdmissionEvent as CapacityAdmissionEvent
from .capacity import CapacityLock as CapacityLock
from .capacity import CapacityReservation as CapacityReservation
from .capacity import StorageInventorySample as StorageInventorySample
from .identity import (
    ApiKey as ApiKey,
)
from .identity import (
    BrowserDevice as BrowserDevice,
)
from .identity import (
    BrowserPairingCode as BrowserPairingCode,
)
from .identity import (
    CollectionPermission as CollectionPermission,
)
from .identity import (
    PrinterPermission as PrinterPermission,
)
from .identity import (
    ProviderConnection as ProviderConnection,
)
from .identity import (
    ProviderOAuthState as ProviderOAuthState,
)
from .identity import (
    RefreshToken as RefreshToken,
)
from .identity import (
    ShareLink as ShareLink,
)
from .identity import (
    User as User,
)
from .inference import EmbeddingSpace as EmbeddingSpace
from .inference import IndexGeneration as IndexGeneration
from .inference import PassageVector as PassageVector
from .ingestion import (
    BackgroundJob as BackgroundJob,
)
from .ingestion import (
    CaptureUploadSlot as CaptureUploadSlot,
)
from .ingestion import (
    InboxItem as InboxItem,
)
from .ingestion import (
    InboxItemResult as InboxItemResult,
)
from .ingestion import (
    StagingLease as StagingLease,
)
from .library import (
    ArtifactMaterialRequirement as ArtifactMaterialRequirement,
)
from .library import (
    Collection as Collection,
)
from .library import (
    CollectionTagLink as CollectionTagLink,
)
from .library import (
    Document as Document,
)
from .library import (
    File as File,
)
from .library import (
    FileTagLink as FileTagLink,
)
from .library import (
    Metadata as Metadata,
)
from .library import (
    Model as Model,
)
from .library import (
    ModelStar as ModelStar,
)
from .library import (
    ModelTagLink as ModelTagLink,
)
from .library import (
    MultipartModel as MultipartModel,
)
from .library import (
    MultipartModelChoice as MultipartModelChoice,
)
from .library import (
    MultipartModelStar as MultipartModelStar,
)
from .library import (
    MultipartModelTagLink as MultipartModelTagLink,
)
from .library import (
    MultipartPart as MultipartPart,
)
from .library import (
    PartGroup as PartGroup,
)
from .library import (
    PartOption as PartOption,
)
from .library import (
    SavedView as SavedView,
)
from .library import (
    Tag as Tag,
)
from .media import (
    ThumbnailGeneration as ThumbnailGeneration,
)
from .media import (
    ThumbnailRenderSlot as ThumbnailRenderSlot,
)
from .notifications import (
    NotificationChannel as NotificationChannel,
)
from .notifications import (
    NotificationDelivery as NotificationDelivery,
)
from .printing import (
    FilamentProfile as FilamentProfile,
)
from .printing import (
    MultipartBuild as MultipartBuild,
)
from .printing import (
    MultipartBuildAttempt as MultipartBuildAttempt,
)
from .printing import (
    MultipartBuildConfirmation as MultipartBuildConfirmation,
)
from .printing import (
    MultipartBuildPart as MultipartBuildPart,
)
from .printing import (
    PrintBatch as PrintBatch,
)
from .printing import (
    Printer as Printer,
)
from .printing import (
    PrinterFile as PrinterFile,
)
from .printing import (
    PrinterMaintenanceLog as PrinterMaintenanceLog,
)
from .printing import (
    PrinterMaintenanceWindow as PrinterMaintenanceWindow,
)
from .printing import (
    PrinterMaterialSlot as PrinterMaterialSlot,
)
from .printing import (
    PrinterProfile as PrinterProfile,
)
from .printing import (
    PrinterTool as PrinterTool,
)
from .printing import (
    PrintJob as PrintJob,
)
from .provenance import (
    ArtifactProvenanceLink as ArtifactProvenanceLink,
)
from .provenance import (
    ModelProvenanceField as ModelProvenanceField,
)
from .provenance import (
    ModelProvenanceSource as ModelProvenanceSource,
)
from .provenance import (
    ModelSourceCover as ModelSourceCover,
)
from .provenance import (
    ProvenanceCapture as ProvenanceCapture,
)
from .similarity import GeometryFingerprint as GeometryFingerprint
from .similarity import SimilarityCandidate as SimilarityCandidate
from .similarity import SimilarityCandidateObservation as SimilarityCandidateObservation
from .similarity import SimilarityReviewDecision as SimilarityReviewDecision
from .similarity import SimilarityRun as SimilarityRun
from .sources import (
    ExternalLibrary as ExternalLibrary,
)
from .sources import (
    ExternalLibraryCheckpoint as ExternalLibraryCheckpoint,
)
from .sources import (
    ExternalLibraryObservation as ExternalLibraryObservation,
)
from .sources import (
    ExternalLibraryTombstone as ExternalLibraryTombstone,
)
from .sources import (
    RemoteDiscoveryDirectory as RemoteDiscoveryDirectory,
)
from .sources import (
    RemoteDiscoveryEntry as RemoteDiscoveryEntry,
)
from .sources import (
    RemoteDiscoveryInventory as RemoteDiscoveryInventory,
)
from .storage import (
    OwnedStorageObject as OwnedStorageObject,
)
from .storage import (
    RestoreMarker as RestoreMarker,
)
from .storage import (
    StorageConnection as StorageConnection,
)
from .storage import (
    StorageDeleteIntent as StorageDeleteIntent,
)
from .storage import (
    StorageFailureDomainDeclaration as StorageFailureDomainDeclaration,
)
from .types import (
    SENTINEL_FILE_HASH as SENTINEL_FILE_HASH,
)
from .types import (
    SENTINEL_MODEL_HASH as SENTINEL_MODEL_HASH,
)
from .types import (
    SUFFIX_TO_FILE_TYPE as SUFFIX_TO_FILE_TYPE,
)
from .types import ArtifactUploadState as ArtifactUploadState
from .types import (
    CaptureProvider as CaptureProvider,
)
from .types import (
    CaptureUploadSlotState as CaptureUploadSlotState,
)
from .types import (
    CollectionRole as CollectionRole,
)
from .types import (
    CompatibilityPolicy as CompatibilityPolicy,
)
from .types import (
    DocumentKind as DocumentKind,
)
from .types import (
    ExternalLibraryCollectionMode as ExternalLibraryCollectionMode,
)
from .types import (
    ExternalLibraryScanStatus as ExternalLibraryScanStatus,
)
from .types import (
    ExternalLibraryWatchMode as ExternalLibraryWatchMode,
)
from .types import (
    FileRevisionStatus as FileRevisionStatus,
)
from .types import (
    FileType as FileType,
)
from .types import (
    GcRunState as GcRunState,
)
from .types import (
    InboxItemCompletion as InboxItemCompletion,
)
from .types import (
    InboxItemResultState as InboxItemResultState,
)
from .types import (
    InboxItemState as InboxItemState,
)
from .types import (
    InboxSourceKind as InboxSourceKind,
)
from .types import (
    JobPriority as JobPriority,
)
from .types import (
    LibrarySourceKind as LibrarySourceKind,
)
from .types import (
    MaterialSlotState as MaterialSlotState,
)
from .types import (
    MaterialSource as MaterialSource,
)
from .types import (
    NotificationDeliveryStatus as NotificationDeliveryStatus,
)
from .types import (
    NotificationEventType as NotificationEventType,
)
from .types import (
    NotificationTarget as NotificationTarget,
)
from .types import (
    OperatorGateState as OperatorGateState,
)
from .types import (
    PrinterProvider as PrinterProvider,
)
from .types import (
    PrinterRole as PrinterRole,
)
from .types import (
    PrinterStatus as PrinterStatus,
)
from .types import (
    PrintJobState as PrintJobState,
)
from .types import (
    RoutingStrategy as RoutingStrategy,
)
from .types import (
    StorageConnectionPurpose as StorageConnectionPurpose,
)
from .types import (
    StorageObjectState as StorageObjectState,
)
from .types import (
    ThumbnailGenerationState as ThumbnailGenerationState,
)
from .types import (
    VaultAuditFindingState as VaultAuditFindingState,
)
from .types import (
    VaultAuditMode as VaultAuditMode,
)
from .types import (
    VaultAuditRunState as VaultAuditRunState,
)
from .types import (
    VaultAuditSeverity as VaultAuditSeverity,
)
from .vault_migration import VaultGeneration as VaultGeneration
from .vault_migration import VaultMigrationObject as VaultMigrationObject
from .vault_migration import VaultMigrationRun as VaultMigrationRun
