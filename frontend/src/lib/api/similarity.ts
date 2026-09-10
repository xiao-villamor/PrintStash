import { getJson, sendJson } from "@/lib/api/request";
import type {
  SemanticNeighbors,
  SimilarityCandidate,
  SimilarityDecision,
  SimilarityFilters,
  SimilarityPage,
  SimilarityRun,
  SimilaritySettings,
  SimilarityStatus,
} from "@/types/similarity";

export function getSimilarityStatus() {
  return getJson<SimilarityStatus>("/api/v1/similarity/status", { fresh: true });
}
export function saveSimilaritySettings(patch: Partial<SimilaritySettings>) {
  return sendJson<SimilaritySettings>("/api/v1/similarity/settings", "PATCH", patch);
}
export function startSimilarityRun(scope: SimilarityRun["scope"], ids: number[] = []) {
  return sendJson<SimilarityRun>("/api/v1/similarity/runs", "POST", { scope, ids });
}
export function listSimilarityRuns(beforeId?: number) {
  return getJson<{ items: SimilarityRun[]; next_cursor: number | null }>(
    `/api/v1/similarity/runs${beforeId ? `?before_id=${beforeId}` : ""}`,
    { fresh: true },
  );
}
export function getSimilarityRun(id: number) {
  return getJson<SimilarityRun>(`/api/v1/similarity/runs/${id}`, { fresh: true });
}
export function cancelSimilarityRun(id: number) {
  return sendJson<SimilarityRun>(`/api/v1/similarity/runs/${id}/cancel`, "POST", {});
}
export function listSimilarityCandidates(filters: SimilarityFilters = {}) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined) query.set(key, String(value));
  }
  return getJson<SimilarityPage>(`/api/v1/similarity/candidates?${query}`, { fresh: true });
}
export function getSimilarityCandidate(id: number) {
  return getJson<SimilarityCandidate>(`/api/v1/similarity/candidates/${id}`, { fresh: true });
}
export function decideSimilarity(id: number, decision: SimilarityDecision) {
  return sendJson<{
    decision_id: number;
    resolution_kind: string;
    target_id: number | null;
    candidate: SimilarityCandidate;
  }>(`/api/v1/similarity/candidates/${id}/decision`, "POST", decision);
}
export function findModelSimilar(id: number) {
  return sendJson<SimilarityPage & { run: SimilarityRun }>(
    `/api/v1/models/${id}/similar/query`,
    "POST",
    {},
  );
}

export function searchSimilarModels(
  query: { text: string; model_id?: never } | { model_id: number; text?: never },
) {
  return sendJson<SemanticNeighbors>("/api/v1/similarity/search", "POST", query);
}

export function previewSimilaritySelection(
  selection: Pick<SimilaritySettings, "minimum_confidence" | "class_overrides">,
) {
  return sendJson<{
    total: number;
    by_class: Partial<Record<import("@/types/similarity").EvidenceClass, number>>;
  }>("/api/v1/similarity/selection-preview", "POST", selection);
}
