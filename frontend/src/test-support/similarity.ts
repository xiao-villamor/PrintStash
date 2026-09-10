/** Complete similarity wire fixtures; individual tests name only the state they need. */
import type {
  SimilarityCandidate,
  SimilarityRun,
  SimilaritySettings,
  SimilarityStatus,
} from "@/types/similarity";
import { FROZEN_NOW } from "@/test-support/factories";

export function similaritySettings(
  overrides: Partial<SimilaritySettings> = {},
): SimilaritySettings {
  return {
    enabled: true,
    fingerprint_on_ingest: true,
    minimum_confidence: 0.9,
    class_overrides: {},
    triangle_cap: 200000,
    sample_points: 5000,
    voxel_resolution: 64,
    max_candidates: 20,
    page_size: 100,
    schedule_hours: 0,
    embeddings_enabled: false,
    ...overrides,
  };
}
export function similarityStatus(overrides: Partial<SimilarityStatus> = {}): SimilarityStatus {
  return {
    enabled: true,
    algorithm_version: "geometry-v1-sh5f4577c4",
    pending_fingerprints: 0,
    capabilities: {
      family_resolution: false,
      multipart_resolution: true,
      step: false,
      local_embeddings: false,
    },
    settings: similaritySettings(),
    ...overrides,
  };
}
export function aSimilarityRun(overrides: Partial<SimilarityRun> = {}): SimilarityRun {
  return {
    id: 1,
    scope: "library",
    scope_ids: [],
    state: "queued",
    phase: "fingerprint",
    counters: {},
    failure_code: null,
    created_at: FROZEN_NOW,
    finished_at: null,
    last_activity_at: FROZEN_NOW,
    ...overrides,
  };
}
export function aSimilarityCandidate(
  overrides: Partial<SimilarityCandidate> = {},
): SimilarityCandidate {
  return {
    id: 1,
    model_a_id: 1,
    model_b_id: 2,
    model_a: { id: 1, name: "Bracket", slug: "bracket", thumbnail_file_id: null },
    model_b: { id: 2, name: "Bracket copy", slug: "bracket-copy", thumbnail_file_id: null },
    evidence_class: "identical_geometry",
    confidence: 1,
    exact_equivalence: true,
    review_state: "open",
    freshness: "current",
    resolution_kind: null,
    version: 1,
    summary: {
      sample_points: 5000,
      sampled_hausdorff_mm: 0.03,
      sampled_chamfer_mm: 0.01,
      voxel_iou: 1,
    },
    primary_lineage_key: null,
    observations: [],
    allowed_actions: ["confirm_evidence", "reject", "later", "reopen"],
    algorithm_version: "geometry-v1-sh5f4577c4",
    reconsidered_candidate_id: null,
    ...overrides,
  };
}
