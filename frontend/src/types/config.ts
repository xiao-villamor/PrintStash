export interface SetupStatus {
  configured: boolean;
  setup_token_required?: boolean;
  user_count: number;
  default_data_dir?: string;
  default_thumb_dir?: string;
  current_data_dir?: string;
  current_thumb_dir?: string;
  current_storage_backend?: string;
  current_storage_provider?: string;
  current_storage_provider_config?: StorageProviderConfigValues;
  current_s3_bucket?: string;
  current_s3_endpoint_url?: string;
  current_s3_region?: string;
  current_backup_retention_days?: number;
  current_backup_s3_bucket?: string;
  current_backup_s3_endpoint_url?: string;
  current_backup_s3_region?: string;
  configured_at?: string | null;
}

export interface SetupRequest {
  setup_token: string;
  username: string;
  password: string;
  email?: string;
  storage_backend?: string;
  storage_provider?: string;
  storage_provider_config?: StorageProviderConfigValues;
  data_dir?: string;
  thumb_dir?: string;
  s3_bucket?: string;
  s3_endpoint_url?: string;
  s3_region?: string;
  s3_access_key?: string;
  s3_secret_key?: string;
  backup_retention_days?: number;
  backup_s3_bucket?: string;
  backup_s3_endpoint_url?: string;
  backup_s3_region?: string;
  backup_s3_access_key?: string;
  backup_s3_secret_key?: string;
}

export interface SetupResponse {
  configured: boolean;
  user_id: number;
  username: string;
  storage_backend: string;
  storage_provider: string;
  data_dir: string;
  thumb_dir: string;
  access_token: string;
  token_type: string;
}

export interface VaultConfigRead {
  storage_backend: string;
  storage_provider: string;
  storage_provider_config: StorageProviderConfigValues;
  storage_tier: StorageTier;
  storage_warnings: string[];
  storage_unverified_acknowledged: boolean;
  data_dir: string;
  thumb_dir: string;
  s3_bucket: string;
  s3_endpoint_url: string;
  s3_region: string;
  s3_access_key: string;
  s3_secret_key: string;
  has_s3_access_key: boolean;
  has_s3_secret_key: boolean;
  backup_retention_days: number;
  trash_retention_days: number;
  backup_s3_bucket: string;
  backup_s3_endpoint_url: string;
  backup_s3_region: string;
  backup_s3_access_key: string;
  backup_s3_secret_key: string;
  has_backup_s3_access_key: boolean;
  has_backup_s3_secret_key: boolean;
  has_backup_s3: boolean;
  auto_mark_known_good: boolean;
  external_libraries_enabled: boolean;
  currency: string;
  model_thumbnail_width: number;
  oidc_enabled: boolean;
  oidc_issuer_url: string;
  oidc_client_id: string;
  has_oidc_client_secret: boolean;
  oidc_scopes: string;
  oidc_username_claim: string;
  oidc_groups_claim: string;
  oidc_admin_groups: string;
  oidc_display_name: string;
  oidc_redirect_uri: string;
  oidc_allow_insecure_http: boolean;
}

export interface VaultConfigUpdate {
  storage_backend?: string;
  storage_provider?: string;
  storage_provider_config?: StorageProviderConfigValues;
  data_dir?: string;
  thumb_dir?: string;
  s3_bucket?: string;
  s3_endpoint_url?: string;
  s3_region?: string;
  s3_access_key?: string;
  s3_secret_key?: string;
  backup_retention_days?: number;
  trash_retention_days?: number;
  backup_s3_bucket?: string;
  backup_s3_endpoint_url?: string;
  backup_s3_region?: string;
  backup_s3_access_key?: string;
  backup_s3_secret_key?: string;
  auto_mark_known_good?: boolean;
  external_libraries_enabled?: boolean;
  currency?: string;
  model_thumbnail_width?: 320 | 640 | 1280;
  oidc_enabled?: boolean;
  oidc_issuer_url?: string;
  oidc_client_id?: string;
  oidc_client_secret?: string;
  oidc_scopes?: string;
  oidc_username_claim?: string;
  oidc_groups_claim?: string;
  oidc_admin_groups?: string;
  oidc_display_name?: string;
  oidc_redirect_uri?: string;
  oidc_allow_insecure_http?: boolean;
}

export type StorageTier = "verified" | "guarded" | "unguarded";

export interface StorageHealthRead {
  ok: boolean;
  backend?: string | null;
  provider?: string | null;
  data_dir?: string | null;
  thumb_dir?: string | null;
  tier?: StorageTier | string | null;
  warnings?: string[];
  diagnostics?: {
    root_bindings?: Record<string, string>;
    roots_ready?: boolean;
    probed?: boolean;
  };
}

export type StorageRootRole = "data" | "thumb";

export interface StorageRootEnrollmentRead {
  enrolled: boolean;
  role: StorageRootRole;
  restart_required: boolean;
}

export interface HealthResponse {
  status: string;
  name: string;
  version: string;
  capabilities?: {
    restart?: boolean;
  };
  storage?: StorageHealthRead;
  components?: {
    database?: { ok: boolean };
    storage?: StorageHealthRead;
  };
}

export type StorageProviderConfigValue = string | number | string[];
export type StorageProviderConfigValues = Record<string, StorageProviderConfigValue>;

export type ProviderCategory = "this_machine" | "s3_compatible" | "nextcloud_webdav" | "nas_sftp";

export interface StorageProviderField {
  name: string;
  label: string;
  help: string;
  input_type: "text" | "password" | "url" | "number" | "path";
  required: boolean;
  secret: boolean;
  default?: string | number | null;
}

export interface StorageProvider {
  id: string;
  label: string;
  category: ProviderCategory;
  description: string;
  expected_tier: StorageTier;
  expected_tier_note: string;
  consequences: string[];
  documentation_url: string;
  available: boolean;
  selectable: boolean;
  /** Product maturity, independent from the measured storage safety tier. */
  support_level?: "stable" | "beta" | string;
  disabled_reason?: string | null;
  fields: StorageProviderField[];
}

export type ExternalLibraryCollectionMode = "mirror" | "single";

// auto: watch only on local filesystems; events: always watch; off: never watch.
export type ExternalLibraryWatchMode = "auto" | "events" | "off";
export type LibrarySourceKind = "mounted" | "s3" | "webdav" | "sftp";
export type StorageConnectionConfigurationValue = string | number | boolean | null;
export type StorageConnectionConfiguration = Record<string, StorageConnectionConfigurationValue>;

export interface StorageConnection {
  id: number;
  name: string;
  kind: Exclude<LibrarySourceKind, "mounted">;
  configuration: StorageConnectionConfiguration;
  secret_fields_set: string[];
  enabled: boolean;
}

// Detected filesystem class backing the root path.
export type ExternalLibraryFsKind = "local" | "network" | "unknown";

export type ExternalLibraryBindingState =
  | "bound"
  | "unbound"
  | "missing"
  | "unreadable"
  | "invalid"
  | "mismatch";

export interface ExternalLibraryScanSummary {
  added: number;
  updated: number;
  removed: number;
  skipped: number;
  errors: string[];
  error: string | null;
  aborted: boolean;
}

export interface ExternalLibrary {
  id: number;
  name: string;
  root_path: string;
  source_kind?: LibrarySourceKind;
  connection_id?: number | null;
  source_prefix?: string;
  writeback_enabled?: boolean;
  enabled: boolean;
  scan_interval_minutes: number;
  scan_schedule: string;
  watch_mode: ExternalLibraryWatchMode;
  fs_kind: ExternalLibraryFsKind | null;
  watch_active: boolean;
  binding_state: ExternalLibraryBindingState;
  binding_reason: string | null;
  root_enrollable: boolean;
  collection_mode: ExternalLibraryCollectionMode;
  target_collection_id: number | null;
  last_scanned_at: string | null;
  // "partial" = scan completed but one or more files failed to index (terminal,
  // like "ok"); the backend uses it so a green status never hides a per-file error.
  last_scan_status: "ok" | "error" | "running" | "partial" | null;
  last_scan_summary: ExternalLibraryScanSummary | null;
}

export interface ExternalLibraryRootEnrollment {
  confirm_root_path: string;
}

export interface ExternalLibraryCreate {
  name: string;
  root_path?: string;
  source_kind?: LibrarySourceKind;
  connection_id?: number | null;
  source_prefix?: string;
  enabled?: boolean;
  scan_schedule?: string;
  watch_mode?: ExternalLibraryWatchMode;
  collection_mode?: ExternalLibraryCollectionMode;
  target_collection_id?: number | null;
}

export interface ExternalLibraryUpdate {
  name?: string;
  root_path?: string;
  enabled?: boolean;
  scan_schedule?: string;
  watch_mode?: ExternalLibraryWatchMode;
  collection_mode?: ExternalLibraryCollectionMode;
  target_collection_id?: number | null;
}
