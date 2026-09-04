export type InboxItemState =
  | "captured"
  | "resolving"
  | "review"
  | "importing"
  | "completed"
  | "failed"
  | "dismissed";

export type InboxItemCompletion = "complete" | "partial";
export type InboxItemResultState = "imported" | "deduplicated" | "failed";

export interface InboxManifestFile {
  id: string;
  name: string;
  size: number | null;
  file_type: string;
}

export interface InboxManifestV1 {
  schema_version?: 1;
  kind: "direct" | "archive" | "model_files" | "collection" | "browser_file";
  title?: string;
  selected_ids?: string[];
  entries?: InboxManifestFile[];
  files?: InboxManifestFile[];
  members?: Array<{ id: string; title: string; page_url: string }>;
}

export interface InboxManifestV2 {
  schema_version: 2;
  kind: "model_files";
  source: {
    provider: string;
    canonical_url: string;
    source_item_id: string | null;
    source_revision: string | null;
    adapter_version: string;
    tags: string[];
    fields: Partial<Record<InboxCapturedFieldName, InboxCapturedField>>;
  };
  files: InboxManifestFile[];
  selected_ids: string[];
}

export type InboxCapturedFieldName =
  | "title"
  | "description"
  | "instructions"
  | "creator_name"
  | "creator_id"
  | "creator_url"
  | "license_code"
  | "license_url"
  | "license_text"
  | "attribution_text"
  | "published_at"
  | "updated_at";

export interface InboxCapturedField {
  value: string;
  origin: "confirmed" | "inferred";
}

export type InboxManifest = InboxManifestV1 | InboxManifestV2;

export interface InboxItemResult {
  id: number;
  source_selection_id: string;
  result_key: string;
  original_filename: string;
  state: InboxItemResultState;
  model_id: number | null;
  file_id: number | null;
  provenance_source_id: number | null;
  error_code: string | null;
  retryable: boolean;
  created_at: string;
  updated_at: string;
}

export interface InboxItem {
  id: number;
  owner_user_id: number;
  source_kind: "url" | "browser" | "upload" | "external";
  source_url: string | null;
  display_title: string | null;
  source_hostname: string | null;
  state: InboxItemState;
  manifest: InboxManifest;
  target_collection_id: number | null;
  requested_tags: string[];
  background_job_id: string | null;
  resulting_model_id: number | null;
  results: InboxItemResult[];
  error_code: string | null;
  retryable: boolean;
  attempt_count: number;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  completion: InboxItemCompletion | null;
}
