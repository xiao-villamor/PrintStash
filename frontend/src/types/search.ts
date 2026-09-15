import type { ModelListItem } from "@/types/models";

export type SearchSubjectType = "model" | "collection" | "multipart_model" | "document";
export interface SearchEvidence {
  leg: string;
  field: string;
  text: string;
  ranges: [number, number][];
}
export interface SearchResult {
  subject_type: SearchSubjectType;
  subject_id: number;
  name: string;
  href: string;
  evidence: SearchEvidence[];
  model: ModelListItem | null;
}
export interface SearchResponse {
  items: SearchResult[];
  next_cursor: string | null;
  legs: string[];
  lexical_backend: "fts5" | "postgres_bm25" | "ranked_like";
  degraded: string[];
  semantic_ready: boolean;
  generations: number[];
  truncated: boolean;
  outcome: "results" | "no_results" | "no_strong_matches";
  leg_errors: Record<string, string>;
}
export interface SearchStatus {
  enabled: boolean;
  semantic_ready: boolean;
  legs: string[];
  generations: number[];
  degraded: string[];
  backlog: boolean;
  remote_hosts: string[];
}
export interface SearchSettings {
  enabled: boolean;
  lexical_backend: "auto" | "ranked_like";
  captions_enabled: boolean;
  nl_filters_enabled: boolean;
  local_models_enabled: boolean;
  sparse_expansion_enabled: boolean;
  sparse_model_id: string | null;
  download_enabled: boolean;
  send_rendered_images: boolean;
  send_query_images: boolean;
  timezone: string;
  chat_endpoint_id: number | null;
  rollback_retention_hours: number;
  max_index_bytes: number;
  query_timeout_seconds: number;
  semantic_floor: number;
  lexical_weight: number;
  semantic_weight: number;
  rrf_k: number;
  semantic_floors: Record<string, number>;
}
export interface InferenceEndpoint {
  id: number;
  kind: "embedding" | "chat";
  host: string;
  base_url: string;
  model: string;
  revision: string;
  model_repo: string | null;
  mrl_dimensions: number[];
  config_hash: string;
  native_dimension: number | null;
  supports_images: boolean;
  dialect: string | null;
  guarantee: string | null;
  has_credentials: boolean;
  header_names: string[];
  timeout_seconds: number;
  max_input_characters: number;
  prefer_responses: boolean;
}
export interface EndpointProposal {
  kind: "embedding" | "chat";
  base_url: string;
  model: string;
  revision: string;
  model_repo?: string | null;
  native_dimension?: number;
  supports_images?: boolean;
  prefer_responses?: boolean;
  timeout_seconds?: number;
  max_input_characters?: number;
  api_key?: string;
  headers?: Record<string, string>;
  inherit_credentials_from_id?: number;
}
export interface SearchSettingsRead {
  settings: SearchSettings;
  endpoints: InferenceEndpoint[];
  environment_endpoints: ("embedding" | "chat")[];
}
export interface InferenceModel {
  id: string;
  key: string;
  revision: string;
  repository: string | null;
  languages: string[];
  license: string | null;
  modality: string;
  native_dimension: number;
  size_bytes: number;
  installed: boolean;
  curated: boolean;
  referenced: boolean;
  runtime_available: boolean;
  mrl_dimensions: number[];
}
export interface GenerationProposal {
  endpoint_id?: number;
  local_model_id?: string;
  profile?: "semantic_text" | "thumbnail" | "multiview" | "point_cloud";
  aggregation?: "mean" | "max";
  query_prefix?: string;
  document_prefix?: string;
  passage_recipe_version?: number;
  index_backend: "auto" | "numpy" | "sqlite_vec" | "pgvector";
  index_dimension?: number;
  quantization: "float32" | "int8" | "binary";
  auto_activate: boolean;
}
export interface SearchGeneration {
  id: number;
  state: string;
  phase: string;
  version_token: string | null;
  modality: string;
  profile: string;
  model: string;
  config_hash: string;
  native_dimension: number;
  index_dimension: number;
  quantization: string;
  index_backend: string;
  effective_backend: string;
  index_state: string;
  index_error: string | null;
  error_code: string | null;
  processed: number;
  copied: number;
  truncated_count: number;
  eligible: number;
  indexed: number;
  quarantined: number;
  estimated_bytes: number;
  job_id: string | null;
  verified_at: string | null;
  retain_until: string | null;
  created_at: string;
  last_activity_at: string | null;
  eta_seconds: number | null;
}
export interface GenerationEstimate {
  passages: number;
  estimated_bytes: number;
  existing_bytes: number;
  budget_bytes: number;
  fits_budget: boolean;
  estimated_seconds: number | null;
}

export interface SearchPreferences {
  nl_filters_enabled: boolean;
  timezone: string | null;
  effective_timezone: string;
  available: boolean;
  endpoint_host: string | null;
}
export interface ParsedSearch {
  residual_query: string;
  filters: import("@/types").SavedViewFilters;
  sort: import("@/types").ModelSort;
  parsed: boolean;
  reason: string | null;
  timezone: string;
}
