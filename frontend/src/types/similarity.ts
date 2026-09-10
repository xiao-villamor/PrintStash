export const EVIDENCE_CLASSES = [
  "identical_geometry",
  "rescaled",
  "mirrored",
  "rescaled_mirrored",
  "remeshed",
  "repaired",
  "similar_shape",
  "component_of",
  "plate_of",
] as const;
export type EvidenceClass = (typeof EVIDENCE_CLASSES)[number];
export type ReviewState = "open" | "confirmed" | "rejected" | "later";
export type SimilarityAction =
  | "confirm_evidence"
  | "confirm_family"
  | "create_multipart"
  | "reject"
  | "later"
  | "reopen";

export interface SimilaritySettings {
  enabled: boolean;
  fingerprint_on_ingest: boolean;
  minimum_confidence: number;
  class_overrides: Partial<Record<EvidenceClass, number>>;
  triangle_cap: number;
  sample_points: number;
  voxel_resolution: 64;
  max_candidates: number;
  page_size: number;
  schedule_hours: number;
  embeddings_enabled: boolean;
}
export interface SimilarityStatus {
  embeddings_enabled?: boolean;
  enabled: boolean;
  algorithm_version: string;
  pending_fingerprints: number;
  selection?: Pick<SimilaritySettings, "minimum_confidence" | "class_overrides">;
  capabilities: {
    family_resolution: boolean;
    multipart_resolution: boolean;
    step: boolean;
    local_embeddings: boolean;
    ["text_to_shape"]?: boolean;
    embedding_reason?: string | null;
  };
  settings: SimilaritySettings | null;
}
export interface SimilarityCounters {
  ready?: number;
  partial?: number;
  unsupported?: number;
  failed?: number;
  cached?: number;
  stale?: number;
  artifacts_processed?: number;
  shortlisted?: number;
  skipped_by_budget?: number;
  verified?: number;
  verification_failed?: number;
  candidates?: number;
  embedded?: number;
  embedding_cached?: number;
}
export interface SimilarityRun {
  id: number;
  scope: "library" | "collections" | "models" | "sources";
  scope_ids: number[];
  state: "queued" | "running" | "cancelling" | "cancelled" | "completed" | "failed";
  phase: "fingerprint" | "candidates" | "embeddings";
  checkpoint?: { embedding_failure_code?: string };
  counters: SimilarityCounters;
  failure_code: string | null;
  created_at: string;
  finished_at: string | null;
  last_activity_at: string;
}
export interface SimilarityModel {
  id: number;
  name: string;
  slug: string;
  thumbnail_file_id: number | null;
}
export interface SimilarityEvidence {
  scale_factor?: number;
  mirrored?: boolean;
  mirror_ambiguous?: boolean;
  transform?: number[][];
  sampled_hausdorff_mm?: number;
  sampled_chamfer_mm?: number;
  voxel_iou?: number | null;
  sample_points?: number;
  evaluation_seeds?: number[];
  tolerance_relative?: number;
  convergence_error?: number;
  unavailable?: [string, string][];
  contained_side?: "a" | "b";
  copies?: number;
  composition?: { model_id: number; quantity: number }[];
  unmatched_components?: number;
}
export interface SimilaritySource {
  file_id: number;
  component_index: number;
  surface_area: number | null;
  volume: number | null;
  face_count: number | null;
  watertight: boolean | null;
}
export interface SimilarityObservation {
  lineage_key: string;
  source_a?: SimilaritySource;
  source_b?: SimilaritySource;
  id: number;
  fingerprint_a_id: number | null;
  fingerprint_b_id: number | null;
  input_hash_a: string;
  input_hash_b: string;
  kind: string;
  multiplicity_a: number;
  multiplicity_b: number;
  evidence: SimilarityEvidence;
}
export interface SimilarityCandidate {
  id: number;
  model_a_id: number;
  model_b_id: number;
  model_a: SimilarityModel;
  model_b: SimilarityModel;
  evidence_class: EvidenceClass;
  confidence: number;
  exact_equivalence: boolean;
  review_state: ReviewState;
  freshness: "current" | "stale";
  resolution_kind: "evidence_only" | "multipart" | "family" | null;
  version: number;
  summary: SimilarityEvidence;
  allowed_actions: SimilarityAction[];
  primary_lineage_key: string | null;
  observations?: SimilarityObservation[];
  observations_truncated?: boolean;
  algorithm_version: string;
  reconsidered_candidate_id: number | null;
}
export interface SimilarityPage {
  items: SimilarityCandidate[];
  next_cursor: string | null;
}
export interface SimilarityFilters {
  evidence_class?: EvidenceClass;
  review_state?: ReviewState;
  freshness?: "current" | "stale";
  minimum_confidence?: number;
  collection_id?: number;
  file_type?: string;
  source?: "vault" | "external";
  known_good?: boolean;
  model_id?: number;
  cursor?: string;
  limit?: number;
}
export interface SimilarityDecision {
  action: SimilarityAction;
  request_id: string;
  version: number;
  target_id?: number;
  name?: string;
  collection_id?: number;
  parts?: { name: string; model_ids: number[]; quantity: number }[];
}

export interface SemanticNeighbors {
  items: { model: SimilarityModel; score: number; evidence_kind: "semantic" }[];
  evidence_kind: "semantic";
  space_id: number;
  index_state: "ready" | "empty" | "missing_model_vectors";
  scanned: number;
  truncated: boolean;
}
