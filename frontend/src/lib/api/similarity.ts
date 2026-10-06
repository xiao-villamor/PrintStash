import { type GetJsonOptions, getJson, jsonHeaders, requestApi } from "@/lib/api/request";
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

function writeSimilarity<T>(
  path: string,
  method: "POST" | "PATCH",
  body:
    | Partial<SimilaritySettings>
    | SimilarityDecision
    | { scope: SimilarityRun["scope"]; ids: number[] }
    | Record<string, never>,
) {
  return requestApi<T>(path, { method, headers: jsonHeaders(), body: JSON.stringify(body) });
}
export function getSimilarityStatus(options?: GetJsonOptions) {
  return getJson<SimilarityStatus>("/api/v1/similarity/status", { fresh: true, ...options });
}
export function saveSimilaritySettings(patch: Partial<SimilaritySettings>) {
  return writeSimilarity<SimilaritySettings>("/api/v1/similarity/settings", "PATCH", patch);
}
export function startSimilarityRun(scope: SimilarityRun["scope"], ids: number[] = []) {
  return writeSimilarity<SimilarityRun>("/api/v1/similarity/runs", "POST", { scope, ids });
}
export function listSimilarityRuns(beforeId?: number, options?: GetJsonOptions) {
  return getJson<{ items: SimilarityRun[]; next_cursor: number | null }>(
    `/api/v1/similarity/runs${beforeId ? `?before_id=${beforeId}` : ""}`,
    { fresh: true, ...options },
  );
}
export function getSimilarityRun(id: number, options?: GetJsonOptions) {
  return getJson<SimilarityRun>(`/api/v1/similarity/runs/${id}`, { fresh: true, ...options });
}
export function cancelSimilarityRun(id: number) {
  return writeSimilarity<SimilarityRun>(`/api/v1/similarity/runs/${id}/cancel`, "POST", {});
}
export function listSimilarityCandidates(
  filters: SimilarityFilters = {},
  options?: GetJsonOptions,
) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined) query.set(key, String(value));
  }
  return getJson<SimilarityPage>(`/api/v1/similarity/candidates?${query}`, {
    fresh: true,
    ...options,
  });
}
export function getSimilarityCandidate(id: number, options?: GetJsonOptions) {
  return getJson<SimilarityCandidate>(`/api/v1/similarity/candidates/${id}`, {
    fresh: true,
    ...options,
  });
}
export function decideSimilarity(id: number, decision: SimilarityDecision) {
  return writeSimilarity<{
    decision_id: number;
    resolution_kind: SimilarityCandidate["resolution_kind"];
    target_id: number | null;
    candidate: SimilarityCandidate;
  }>(`/api/v1/similarity/candidates/${id}/decision`, "POST", decision);
}
export function findModelSimilar(id: number) {
  return writeSimilarity<SimilarityPage & { run: SimilarityRun }>(
    `/api/v1/models/${id}/similar/query`,
    "POST",
    {},
  );
}

export function searchSimilarModels(
  query: { text: string; model_id?: never } | { model_id: number; text?: never },
  options?: GetJsonOptions,
) {
  return requestApi<SemanticNeighbors>("/api/v1/similarity/search", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(query),
    signal: options?.signal,
  });
}

export function previewSimilaritySelection(
  selection: Pick<SimilaritySettings, "minimum_confidence" | "class_overrides">,
  options?: GetJsonOptions,
) {
  return requestApi<{
    total: number;
    by_class: Partial<Record<import("@/types/similarity").EvidenceClass, number>>;
  }>("/api/v1/similarity/selection-preview", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(selection),
    signal: options?.signal,
  });
}
