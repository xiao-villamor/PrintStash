import type {
  InferenceEndpoint,
  InferenceModel,
  SearchGeneration,
  SearchResponse,
  SearchResult,
  SearchSettings,
  SearchSettingsRead,
  SearchStatus,
} from "@/types/search";

export function searchStatus(overrides: Partial<SearchStatus> = {}): SearchStatus {
  return {
    enabled: false,
    semantic_ready: false,
    legs: ["lexical"],
    generations: [],
    degraded: [],
    backlog: false,
    remote_hosts: [],
    ...overrides,
  };
}
export function aSearchResult(overrides: Partial<SearchResult> = {}): SearchResult {
  return {
    subject_type: "model",
    subject_id: 12,
    name: "Desk bracket",
    href: "/models/12",
    model: null,
    evidence: [{ leg: "lexical", field: "title", text: "Desk bracket", ranges: [[5, 12]] }],
    ...overrides,
  };
}
export function searchResponse(overrides: Partial<SearchResponse> = {}): SearchResponse {
  return {
    items: [],
    next_cursor: null,
    legs: ["lexical"],
    lexical_backend: "fts5",
    degraded: [],
    semantic_ready: false,
    generations: [],
    truncated: false,
    outcome: "no_results",
    leg_errors: {},
    ...overrides,
  };
}
export function searchSettings(overrides: Partial<SearchSettings> = {}): SearchSettings {
  return {
    enabled: false,
    lexical_backend: "auto",
    captions_enabled: false,
    nl_filters_enabled: false,
    sparse_expansion_enabled: false,
    sparse_model_id: null,
    local_models_enabled: false,
    download_enabled: false,
    send_rendered_images: false,
    send_query_images: false,
    timezone: "UTC",
    chat_endpoint_id: null,
    rollback_retention_hours: 24,
    max_index_bytes: 2147483648,
    query_timeout_seconds: 3,
    semantic_floor: 0.35,
    lexical_weight: 1,
    semantic_weight: 1,
    rrf_k: 60,
    semantic_floors: {},
    ...overrides,
  };
}
export function searchConfiguration(
  overrides: Partial<SearchSettingsRead> = {},
): SearchSettingsRead {
  return { settings: searchSettings(), endpoints: [], environment_endpoints: [], ...overrides };
}
export function anInferenceEndpoint(overrides: Partial<InferenceEndpoint> = {}): InferenceEndpoint {
  return {
    id: 2,
    kind: "embedding",
    host: "inference.local",
    base_url: "http://inference.local/v1",
    model: "server-encoder",
    revision: "pinned-v1",
    model_repo: null,
    mrl_dimensions: [],
    config_hash: "b".repeat(64),
    native_dimension: 384,
    supports_images: false,
    dialect: null,
    guarantee: null,
    has_credentials: true,
    header_names: ["X-Test-Key"],
    timeout_seconds: 15,
    max_input_characters: 16384,
    prefer_responses: false,
    ...overrides,
  };
}
export function anInferenceModel(overrides: Partial<InferenceModel> = {}): InferenceModel {
  return {
    id: "a".repeat(64),
    key: "bge-small-en-v1.5",
    revision: "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
    repository: "BAAI/bge-small-en-v1.5",
    languages: ["en"],
    license: "MIT",
    modality: "text",
    native_dimension: 384,
    size_bytes: 133805086,
    installed: true,
    curated: true,
    referenced: false,
    runtime_available: true,
    mrl_dimensions: [],
    ...overrides,
  };
}
export function aSearchGeneration(overrides: Partial<SearchGeneration> = {}): SearchGeneration {
  return {
    id: 1,
    state: "active",
    phase: "ready",
    version_token: "a".repeat(32),
    modality: "text",
    profile: "semantic_text",
    model: "old-encoder",
    config_hash: "c".repeat(64),
    native_dimension: 384,
    index_dimension: 384,
    quantization: "float32",
    index_backend: "auto",
    effective_backend: "numpy",
    index_state: "ready",
    index_error: null,
    error_code: null,
    processed: 20,
    copied: 0,
    truncated_count: 0,
    eligible: 20,
    indexed: 20,
    quarantined: 0,
    estimated_bytes: 2097152,
    job_id: "index-1",
    verified_at: "2026-09-11T12:01:00Z",
    retain_until: null,
    created_at: "2026-09-11T12:00:00Z",
    last_activity_at: "2026-09-11T12:01:00Z",
    eta_seconds: null,
    ...overrides,
  };
}
