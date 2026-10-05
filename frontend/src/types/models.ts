import type {
  PrintJobIdentityRead,
  PrintJobReportedMetadataRead,
  PrintJobReproducibilityRead,
  PrintJobState,
  ReproducibilityLevel,
} from "./printers";

/** Volume evidence uses cubic millimetres; legacy values carry no topology proof. */
export type VolumeMeasurement =
  | {
      state: "measured";
      unit: "mm3";
      method: "mesh_surface_integral";
      value_mm3: number;
      cause: null;
    }
  | {
      state: "unavailable";
      unit: "mm3";
      method: "mesh_surface_integral";
      value_mm3: null;
      cause:
        | "not_watertight"
        | "inconsistent_winding"
        | "non_positive_integral"
        | "nonfinite_integral"
        | "measurement_failed";
    }
  | {
      state: "not_calculated";
      unit: "mm3";
      method: null;
      value_mm3: null;
      cause:
        | "enrichment_pending"
        | "not_applicable"
        | "not_requested"
        | "geometry_unavailable"
        | "topology_not_evaluated";
    }
  | {
      state: "legacy_unassessed";
      unit: "mm3";
      method: null;
      value_mm3: number | null;
      cause: null;
    };

export interface MetadataRead {
  slicer_name: string | null;
  slicer_version: string | null;
  printer_model: string | null;
  nozzle_diameter_mm: number | null;
  layer_height_mm: number | null;
  first_layer_height_mm: number | null;
  infill_percent: number | null;
  wall_loops: number | null;
  top_shell_layers: number | null;
  bottom_shell_layers: number | null;
  support_material: boolean | null;
  nozzle_temperature_c: number | null;
  bed_temperature_c: number | null;
  estimated_time_s: number | null;
  filament_weight_g: number | null;
  filament_length_mm: number | null;
  filament_cost: number | null;
  material_type: string | null;
  material_brand: string | null;
  bbox_x_mm: number | null;
  bbox_y_mm: number | null;
  bbox_z_mm: number | null;
  volume_mm3: number | null;
  volume_measurement: VolumeMeasurement;
  triangle_count: number | null;
}

export type FileRevisionStatus = "known_good" | "needs_test" | "failed" | "archived";

export type CollectionRole = "view" | "edit" | "admin";

export interface FileRead {
  id: number;
  model_id: number;
  original_filename: string;
  file_type: "stl" | "3mf" | "gcode" | "obj" | "step" | "dxf";
  version: number;
  gcode_revision_number?: number | null;
  size_bytes: number;
  sha256: string;
  revision_label?: string | null;
  revision_status: FileRevisionStatus | null;
  revision_notes: string | null;
  is_recommended: boolean;
  is_external?: boolean;
  uploaded_at: string;
  metadata: MetadataRead | null;
  tags: string[];
}

export interface TrashedSourceFileRead {
  id: number;
  original_filename: string;
}

export interface FileRevisionUpdate {
  revision_label?: string | null;
  revision_status?: FileRevisionStatus | null;
  revision_notes?: string | null;
  is_recommended?: boolean;
}

export interface ModelSimilarityRead {
  open_candidates: number;
  confirmed: number;
}

export interface ModelRead {
  similarity?: ModelSimilarityRead;
  id: number;
  name: string;
  slug: string;
  hash: string;
  collection: string | null;
  collection_id: number | null;
  collection_label: string | null;
  description: string | null;
  source_url: string | null;
  effective_role: CollectionRole | null;
  tags: string[];
  thumbnail_url: string | null;
  created_at: string;
  updated_at: string;
  files: FileRead[];
  trashed_source_files?: TrashedSourceFileRead[];
  starred: boolean;
}

export interface ModelPrinterFileRead {
  file_id: number;
  printer_id: number;
  printer_name: string;
  remote_filename: string;
  matched_by: string;
  last_seen_at: string;
  missing_since: string | null;
}

export interface ModelPrinterPresenceRead {
  printer_id: number;
  printer_name: string;
  file_count: number;
}

export interface ModelPrintJobRead {
  id: number;
  printer_id: number | null;
  printer_name: string;
  file_id: number;
  remote_filename: string;
  source: string;
  external_display_name: string | null;
  artifact_evidence: string;
  artifact_capture_error?: string | null;
  artifact_capture_error_code?: string | null;
  artifact_capture_error_message?: string | null;
  reproducibility_level?: ReproducibilityLevel;
  toolpath_preview_url?: string | null;
  identity?: PrintJobIdentityRead;
  metadata?: PrintJobReportedMetadataRead;
  reproducibility?: PrintJobReproducibilityRead;
  download_url?: string | null;
  gcode_revision_number: number | null;
  revision_label: string | null;
  state: PrintJobState;
  material_type: string | null;
  error: string | null;
  filament_used_g: number | null;
  actual_duration_s: number | null;
  filament_cost: number | null;
  spool_id: number | null;
  spool_name: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export interface ArtifactOutcomeRead {
  file_id: number;
  print_count: number;
  completed_count: number;
  failed_count: number;
  cancelled_count: number;
  success_rate: number | null;
  average_duration_s: number | null;
  total_filament_g: number | null;
  total_cost: number | null;
}

export interface PrintSummaryRead {
  layer_height_mm: number | null;
  estimated_time_s: number | null;
  filament_weight_g: number | null;
  material_type: string | null;
  slicer_name: string | null;
  success_rate?: number | null;
  last_printed_at?: string | null;
  average_duration_s?: number | null;
  total_cost?: number | null;
}

export interface ModelListItem {
  similarity?: ModelSimilarityRead;
  id: number;
  name: string;
  slug: string;
  collection: string | null;
  collection_id: number | null;
  /** Names of the collection's visible ancestors, e.g. `Parts/Brackets`; null outside one. */
  collection_label: string | null;
  source_url: string | null;
  effective_role: CollectionRole | null;
  tags: string[];
  thumbnail_url: string | null;
  file_count: number;
  /** Newest mesh file (STL/3MF/OBJ), used to preload the 3D preview. */
  mesh_file_id: number | null;
  printer_presence: ModelPrinterPresenceRead[];
  updated_at: string;
  print_summary: PrintSummaryRead | null;
  recommended_revision_status?: FileRevisionStatus | null;
  recommended_revision_label?: string | null;
  starred: boolean;
}

export interface TrashedModelRead {
  id: number;
  name: string;
  slug: string;
  collection: string | null;
  tags: string[];
  thumbnail_url: string | null;
  file_count: number;
  size_bytes: number;
  deleted_at: string;
  expires_at: string | null;
}

export interface TrashPurgeRead {
  purged_model_ids: number[];
  purged_count: number;
  /** The physical-storage outcome after the catalog rows were purged. */
  storage_cleanup_status?: StorageCleanupStatus;
  storage_completed?: number;
  storage_pending?: number;
  storage_blocked?: number;
}

export type StorageCleanupStatus = "completed" | "pending" | "blocked" | "partial";

function isStorageCleanupStatus(value: string | null | undefined): value is StorageCleanupStatus {
  return value === "completed" || value === "pending" || value === "blocked" || value === "partial";
}

/**
 * Normalize responses from pre-0.13 servers and derive a useful status when a
 * rolling upgrade returns the legacy counters without the new discriminator.
 */
export function normalizeTrashPurgeRead(
  value: Partial<TrashPurgeRead> | null | undefined,
): TrashPurgeRead & { storage_cleanup_status: StorageCleanupStatus } {
  const payload = value ?? {};
  const completed = payload.storage_completed ?? 0;
  const pending = payload.storage_pending ?? 0;
  const blocked = payload.storage_blocked ?? 0;
  const hasMixedOutcome =
    (completed > 0 && (pending > 0 || blocked > 0)) || (pending > 0 && blocked > 0);
  const explicitStatus = payload.storage_cleanup_status;

  let status: StorageCleanupStatus;
  if (isStorageCleanupStatus(explicitStatus)) {
    status = explicitStatus;
  } else if (completed === 0 && pending === 0 && blocked === 0) {
    status = "completed";
  } else if (hasMixedOutcome) {
    status = "partial";
  } else if (blocked > 0) {
    status = "blocked";
  } else if (pending > 0) {
    status = "pending";
  } else {
    status = "completed";
  }

  return {
    purged_model_ids: payload.purged_model_ids ?? [],
    purged_count: payload.purged_count ?? 0,
    storage_completed: completed,
    storage_pending: pending,
    storage_blocked: blocked,
    storage_cleanup_status: status,
  };
}

export interface ModelBatchFailure {
  model_id: number;
  reason: string;
}

export interface ModelBatchResult {
  succeeded_ids: number[];
  failed: ModelBatchFailure[];
  succeeded_count: number;
  failed_count: number;
}

export interface RevisionBatchResult {
  succeeded_ids: number[];
  succeeded_count: number;
}

export interface StorageUsageRead {
  backend: string;
  prefix: string | null;
  bucket: string | null;
  object_count: number;
  total_size_bytes: number;
  ok: boolean;
  error: string | null;
}

export interface VaultStatsRead {
  model_count: number;
  file_count: number;
  source_file_count: number;
  gcode_file_count: number;
  collection_count: number;
  tag_count: number;
  printer_count: number;
  indexed_size_bytes: number;
  storage: StorageUsageRead;
}

export interface CollectionStatRead {
  collection_id: number | null;
  name: string;
  path: string | null;
  print_count: number;
  total_cost: number | null;
}

export interface FilamentStatRead {
  material_type: string | null;
  material_brand: string | null;
  print_count: number;
  total_g: number | null;
  total_cost: number | null;
}

export interface TimeBucketRead {
  bucket: string;
  cost: number | null;
  filament_g: number | null;
  print_count: number;
}

export interface ModelStatRead {
  model_id: number;
  name: string;
  print_count: number;
  total_g: number | null;
}

export interface PrinterStatRead {
  printer_id: number | null;
  name: string;
  print_count: number;
  print_time_s: number;
}

export interface PrintStatisticsRead {
  period: string;
  start_at: string | null;
  end_at: string;
  total_prints: number;
  total_cost: number | null;
  total_filament_g: number | null;
  avg_filament_g: number | null;
  total_print_time_s: number;
  top_collections: CollectionStatRead[];
  top_filaments: FilamentStatRead[];
  top_models: ModelStatRead[];
  top_printers: PrinterStatRead[];
  cost_over_time: TimeBucketRead[];
}

export interface ModelUpdate {
  name?: string;
  description?: string;
  source_url?: string | null;
  collection?: string;
  tags?: string[];
}

export interface ManualPrintJobCreate {
  printer_id?: number | null;
  printer_name?: string | null;
  file_id: number;
  state?: string;
  spool_id?: number | null;
  spool_name?: string | null;
  spool_filament_id?: number | null;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
}

export interface ImportedPrintJobRead {
  filename: string;
  status: string;
  print_duration?: number | null;
  start_time?: number | null;
  end_time?: number | null;
  matched_file_id?: number | null;
  imported: boolean;
}

/**
 * Where a background Job is. `interrupted` is transient: the process running it
 * stopped and the reconciler is about to run it again.
 */
export type JobState = "queued" | "running" | "interrupted" | "completed" | "failed" | "cancelled";

/** Returned by every endpoint that accepts background work (HTTP 202). */
export interface JobAccepted {
  job_id: string;
  state: JobState;
  message: string;
}

/**
 * Every Job Definition, named `<owner>.<verb>`: the backend's `JobKind`, and
 * nothing else (`tests/repo/test_frontend_enums.py` keeps the two in step).
 */
export type JobKind =
  | "administration.audit"
  | "backups.automatic"
  | "backups.create"
  | "backups.retry_destination"
  | "backups.trash_gc"
  | "derivatives.gcode"
  | "derivatives.mesh"
  | "derivatives.toolpath"
  | "derivatives.viewer_stl"
  | "identity.retention"
  | "inference.model_download"
  | "ingestion.archive_inspect"
  | "ingestion.archive_selection"
  | "ingestion.artifact_upload"
  | "ingestion.collection"
  | "ingestion.inbox_import"
  | "ingestion.inbox_resolve"
  | "ingestion.inbox_retention"
  | "ingestion.library_import"
  | "ingestion.upload"
  | "ingestion.upload_recovery"
  | "ingestion.url"
  | "ingestion.url_selection"
  | "ingestion.scratch_cleanup"
  | "notifications.deliver"
  | "notifications.retention"
  | "printing.dispatch"
  | "search.caption"
  | "search.caption_queue"
  | "search.expand"
  | "search.generation"
  | "search.index"
  | "search.project"
  | "search.repair"
  | "similarity.analyze"
  | "sources.scan"
  | "storage.inventory"
  | "storage.migrate"
  | "work.housekeeping";

/** A concurrency class of background work: the backend's `LaneName`. */
export type LaneName =
  | "captions"
  | "derive.light"
  | "derive.native"
  | "expansion"
  | "ingest"
  | "maintenance"
  | "network"
  | "notify"
  | "printing"
  | "reconcile"
  | "search"
  | "similarity";

/** An output derived from an Artifact's bytes: the backend's `DerivativeKind`. */
export type DerivativeKind = "metadata" | "thumbnail" | "toolpath" | "viewer_stl";

export type WorkPriority = "interactive" | "backfill";

/** One background Job, from `/api/v1/jobs`. */
export interface JobStagingSummary {
  retained_bytes: number;
  lease_count: number;
  earliest_expiry: string;
  discard_available: boolean;
}

export interface JobStatus {
  staging: JobStagingSummary | null;
  job_id: string;
  kind: JobKind;
  state: JobState;
  priority: WorkPriority;
  attempts: number;
  resubmits: number;
  model_id: number | null;
  file_id: number | null;
  error: string | null;
  retryable: boolean;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
  committed_at: string | null;
  step: number | null;
  total_steps: number | null;
  label: string | null;
  progress: number | null;
  result: IngestJobResult | null;
  stage:
    | "resolving"
    | "downloading"
    | "inspecting"
    | "extracting"
    | "hashing"
    | "ingesting"
    | "snapshotting"
    | "archiving"
    | "verifying"
    | "publishing"
    | "finalizing"
    | "completed"
    | null;
  current_item: string | null;
  processed: number;
  total: number | null;
  succeeded: number;
  deduplicated: number;
  skipped: number;
  failed: number;
  completion: "complete" | "partial" | null;
  failed_items: Array<{ name: string; reason: string; retryable: boolean }>;
}

/**
 * Where one derivative of an Artifact is. `pending` means nothing has been
 * attempted at the current recipe: every value it would supply is unknown.
 */
export type DerivativeState =
  | "disabled"
  | "pending"
  | "queued"
  | "running"
  | "ready"
  | "skipped"
  | "failed"
  | "cancelled";

export interface DerivativeRead {
  kind: DerivativeKind;
  recipe_version: number;
  state: DerivativeState;
  attempts: number;
  failure_reason: string | null;
  updated_at: string | null;
  retryable: boolean;
}

export interface WorkLane {
  name: LaneName;
  concurrency: number;
  default_concurrency: number;
  overridden: boolean;
  scope: "worker" | "global";
  partitioned: boolean;
  queued: number;
  running: number;
}

export interface WorkDefinition {
  enabled: boolean;
  default_enabled: boolean;
  overridden: boolean;
  name: JobKind;
  label: string;
  lane: LaneName;
  queued: number;
  running: number;
  interrupted: number;
  failed: number;
  completed: number;
  derivative_kinds: DerivativeKind[];
  next_due_at: string | null;
  last_finished_at: string | null;
}

export interface WorkExecutor {
  executor_id: string;
  role: "all" | "api" | "worker";
  hostname: string;
  app_version: string;
  lanes: LaneName[];
  started_at: string;
  heartbeat_at: string;
  stale: boolean;
}

/** `GET /api/v1/admin/work`: the Background work settings page. */
export interface WorkOverview {
  lanes: WorkLane[];
  definitions: WorkDefinition[];
  executors: WorkExecutor[];
  failed_jobs: JobStatus[];
  failed_derivatives: number;
}

/** One member/file outcome inside a multi-item import result. */
export interface IngestJobResultItem {
  model_id?: number | null;
  file_id?: number | null;
  name?: string;
  member?: string;
  deduplicated?: boolean;
  error?: string | null;
}

/**
 * The job-kind-specific payload a finished ingest job carries. Which keys arrive
 * depends on `kind`: the review flows stage one of the manifests, a collection
 * import reports counts plus per-member outcomes, and a plain single-artifact
 * ingest reports whether it created a new Model.
 */
export interface IngestJobResult {
  kind?: "archive_manifest" | "model_files_manifest" | "collection_manifest" | "collection_import";
  // kind === "archive_manifest"
  archive_id?: string;
  archive_name?: string;
  entries?: ArchiveEntry[];
  // kind === "model_files_manifest"
  files_token?: string;
  page_title?: string;
  files?: ModelFile[];
  // kind === "collection_manifest"
  collection_token?: string;
  collection_name?: string;
  target_collection?: string;
  members?: CollectionMember[];
  // kind === "collection_import"
  collection?: string | null;
  imported?: number;
  total?: number;
  items?: IngestJobResultItem[];
  // Plain single-artifact ingest.
  created?: boolean;
  resumed?: boolean;
  name?: string;
  // `backups.create`: the new backup as `GET /api/v1/backups` lists it.
  backup_id?: string;
  created_at?: string;
  size_bytes?: number;
  file_count?: number;
  storage_backend?: string;
  app_version?: string;
  location?: string;
  source_ref?: string | null;
  provider_ref?: string | null;
  namespace?: string | null;
  run_id?: string | null;
  outcome?: "running" | "completed" | "partial" | "failed" | null;
}

export interface ArchiveEntry {
  name: string;
  size_bytes: number;
  file_type: string | null; // FileType value if importable, else null
  is_image: boolean;
}

export interface ArchiveManifest {
  archive_id: string;
  archive_name: string;
  entries: ArchiveEntry[];
}

export interface ModelFile {
  file_id: string;
  name: string;
  file_type: string; // stl / gcode / sla / other
  size: number | null;
}

export interface ModelFilesManifest {
  files_token: string;
  page_title: string;
  files: ModelFile[];
}

export interface CollectionMember {
  source_id: string;
  title: string;
  page_url: string;
}

export interface CollectionManifest {
  collection_token: string;
  collection_name: string;
  target_collection: string;
  members: CollectionMember[];
}

export interface PublicFileRead {
  id: number;
  original_filename: string;
  file_type: string;
  size_bytes: number;
  version: number;
  gcode_revision_number: number | null;
  revision_label: string | null;
  revision_status: FileRevisionStatus | null;
  revision_notes: string | null;
  is_recommended: boolean;
  bbox_x_mm: number | null;
  bbox_y_mm: number | null;
  bbox_z_mm: number | null;
  triangle_count: number | null;
  slicer_name: string | null;
  slicer_version: string | null;
  printer_model: string | null;
  nozzle_diameter_mm: number | null;
  layer_height_mm: number | null;
  first_layer_height_mm: number | null;
  infill_percent: number | null;
  wall_loops: number | null;
  support_material: boolean | null;
  nozzle_temperature_c: number | null;
  bed_temperature_c: number | null;
  estimated_time_s: number | null;
  filament_weight_g: number | null;
  filament_length_mm: number | null;
  filament_cost: number | null;
  material_type: string | null;
  material_brand: string | null;
}

export interface PublicModelRead {
  name: string;
  description: string | null;
  has_thumbnail: boolean;
  allow_download: boolean;
  files: PublicFileRead[];
}

export interface ShareLinkRead {
  id: number;
  model_id: number;
  expires_at: string;
  revoked_at: string | null;
  allow_download: boolean;
  revision_file_ids: number[] | null;
  access_count: number;
  created_at: string;
  is_active: boolean;
}

export interface ShareLinkCreated extends ShareLinkRead {
  token: string;
  url: string;
}

export interface ShareLinkCreate {
  expires_in_days: number;
  allow_download: boolean;
  revision_file_ids?: number[] | null;
}

export interface ListModelsParams {
  collection?: string;
  direct?: boolean;
  tag?: string[];
  q?: string;
  printer_id?: number;
  printer_presence?: "any" | "none";
  favorites?: boolean;
  file_type?: ArtifactFileType[];
  material_type?: string[];
  slicer_name?: string[];
  printer_model?: string[];
  revision_status?: FileRevisionStatus[];
  has_similar_candidates?: boolean;
  printed?: boolean;
  print_outcome?: PrintJobState[];
  storage?: ("vault" | "external")[];
  uploaded_after?: string;
  uploaded_before?: string;
  printed_after?: string;
  printed_before?: string;
  print_duration_min_s?: number;
  print_duration_max_s?: number;
  limit?: number;
  offset?: number;
}

export type ModelSort =
  | "relevance"
  | "date-desc"
  | "date-asc"
  | "name-asc"
  | "name-desc"
  | "success-desc"
  | "printed-desc"
  | "duration-asc"
  | "filament-asc"
  | "cost-asc";

export interface ModelPageRead {
  items: ModelListItem[];
  next_cursor: string | null;
  total: number;
}

export interface OutlinerModelRead {
  id: number;
  name: string;
  collection: string | null;
  collection_id: number | null;
  /** Names of the collection's visible ancestors, e.g. `Parts/Brackets`; null outside one. */
  collection_label: string | null;
}

export interface ListModelPageParams extends Omit<ListModelsParams, "offset"> {
  sort?: ModelSort;
  cursor?: string;
}

export interface SavedViewFilters {
  sort?: ModelSort | null;
  collection?: string | null;
  direct: boolean;
  tag: string[];
  q?: string | null;
  printer_id?: number | null;
  printer_presence?: "any" | "none" | null;
  favorites: boolean;
  file_type?: ArtifactFileType[];
  material_type?: string[];
  slicer_name?: string[];
  printer_model?: string[];
  revision_status?: FileRevisionStatus[];
  has_similar_candidates?: boolean | null;
  printed?: boolean | null;
  print_outcome?: PrintJobState[];
  storage?: ("vault" | "external")[];
  uploaded_after?: string | null;
  uploaded_before?: string | null;
  printed_after?: string | null;
  printed_before?: string | null;
  print_duration_min_s?: number | null;
  print_duration_max_s?: number | null;
}

export type ArtifactFileType = "stl" | "3mf" | "gcode" | "obj" | "step" | "dxf";

export interface FacetValueRead {
  value: string;
  count: number;
}

export interface ModelFacetsRead {
  file_type: FacetValueRead[];
  material_type: FacetValueRead[];
  slicer_name: FacetValueRead[];
  printer_model: FacetValueRead[];
  revision_status: FacetValueRead[];
  print_outcome: FacetValueRead[];
  storage: FacetValueRead[];
  printed: FacetValueRead[];
}

export interface SavedViewRead {
  id: number;
  name: string;
  filters: SavedViewFilters;
  created_at: string;
  updated_at: string;
}

export interface ModelStarRead {
  model_id: number;
  starred: boolean;
}

export interface CollectionCreate {
  name: string;
  parent_id?: number | null;
}

export interface TagCreate {
  name: string;
}

export interface CollectionRead {
  id: number;
  name: string;
  slug: string;
  path: string;
  parent_id: number | null;
  model_count: number;
  effective_role: CollectionRole | null;
  tags: string[];
  /** False lets a folder view skip the readme request entirely. */
  has_readme: boolean;
}

/** One collection of the lazily loaded tree (`/collections/children`, `/lookup`, `/search`). */
export interface CollectionNodeRead extends CollectionRead {
  /** Direct children; zero means the row has nothing to expand into. */
  child_count: number;
  /** Every collection below this one, at any depth. */
  descendant_count: number;
  /** Names of the visible ancestors and this collection, e.g. `Parts/Brackets`. */
  display_path: string;
}

export interface CollectionPage {
  items: CollectionNodeRead[];
  /** Null on the last page. */
  next_cursor: string | null;
}

export interface CollectionLookupRead {
  collection: CollectionNodeRead;
  /** Visible ancestors, root first. */
  ancestors: CollectionNodeRead[];
}

export interface CollectionPermissionRead {
  user_id: number;
  username: string;
  collection_id: number;
  role: CollectionRole;
  inherited: boolean;
}

export interface CollectionPermissionUpdate {
  role: CollectionRole;
}

export interface FilamentProfileRead {
  id: number;
  name: string;
  material_type: string | null;
  material_brand: string | null;
  cost_per_kg: number | null;
  notes: string | null;
  usage_count: number;
  spoolman_filament_id: number | null;
  density_g_cm3: number | null;
  diameter_mm: number | null;
  created_at: string;
  updated_at: string;
}

export interface FilamentProfileCreate {
  name: string;
  material_type?: string | null;
  material_brand?: string | null;
  cost_per_kg?: number | null;
  notes?: string | null;
}

export interface FilamentProfileUpdate {
  name?: string;
  material_type?: string | null;
  material_brand?: string | null;
  cost_per_kg?: number | null;
  notes?: string | null;
}

export interface PrinterProfileRead {
  id: number;
  name: string;
  printer_model: string | null;
  slicer_name: string | null;
  nozzle_diameter_mm: number | null;
  notes: string | null;
  usage_count: number;
  created_at: string;
  updated_at: string;
}

export interface PrinterProfileCreate {
  name: string;
  printer_model?: string | null;
  slicer_name?: string | null;
  nozzle_diameter_mm?: number | null;
  notes?: string | null;
}

export interface PrinterProfileUpdate {
  name?: string;
  printer_model?: string | null;
  slicer_name?: string | null;
  nozzle_diameter_mm?: number | null;
  notes?: string | null;
}

export interface TagRead {
  id: number;
  name: string;
  slug: string;
  model_count: number;
  multipart_model_count?: number;
}
