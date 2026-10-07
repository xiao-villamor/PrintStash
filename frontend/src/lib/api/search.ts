import { requireEditingBase, requireEditingReceipt } from "./editing";
import type { EditingBase } from "@/types/editing";
import {
  authHeaders,
  getJson,
  jsonHeaders,
  requestApi,
  type GetJsonOptions,
} from "@/lib/api/request";
import { ApiError } from "@/lib/errors";
import type { SavedViewFilters, ModelSort } from "@/types";
import type {
  ParsedSearch,
  SearchPreferences,
  EndpointProposal,
  GenerationEstimate,
  GenerationProposal,
  InferenceEndpoint,
  InferenceModel,
  SearchGeneration,
  SearchResponse,
  SearchSettings,
  SearchSettingsRead,
  SearchStatus,
  SearchSubjectType,
} from "@/types/search";

export interface SearchQuery {
  q: string;
  mode?: "lexical" | "hybrid";
  instant?: boolean;
  cursor?: string;
  limit?: number;
  types?: SearchSubjectType[];
  filters?: SavedViewFilters;
  sort?: ModelSort;
}

/** Bound interactive searches, including parsing, while preserving route cancellation. */
async function searchRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const controller = new AbortController();
  const parent = options.signal;
  const cancel = () => controller.abort(parent?.reason);
  if (parent?.aborted) cancel();
  else parent?.addEventListener("abort", cancel, { once: true });
  const timer = setTimeout(
    () => controller.abort(new ApiError(408, "search_timeout", "Search timed out")),
    30_000,
  );
  try {
    return await requestApi<T>(path, {
      headers: authHeaders(),
      cache: "no-store",
      ...options,
      signal: controller.signal,
    });
  } catch (error) {
    if (controller.signal.aborted) throw controller.signal.reason;
    throw error;
  } finally {
    clearTimeout(timer);
    parent?.removeEventListener("abort", cancel);
  }
}
export async function searchLibrary(
  query: SearchQuery,
  signal?: AbortSignal,
): Promise<SearchResponse> {
  const params = new URLSearchParams({
    q: query.q,
    mode: query.mode ?? "hybrid",
    limit: String(query.limit ?? 30),
  });
  if (query.filters) params.set("filters", JSON.stringify(query.filters));
  if (query.sort) params.set("sort", query.sort);
  if (query.cursor) params.set("cursor", query.cursor);
  if (query.instant) params.set("instant", "true");
  query.types?.forEach((type) => params.append("types[]", type));
  return searchRequest<SearchResponse>(`/api/v1/search?${params}`, { signal });
}
export function getSearchStatus(options: GetJsonOptions = {}) {
  return getJson<SearchStatus>("/api/v1/search/status", { ...options });
}
export async function searchUsingModel(
  modelId: number,
  cursor?: string,
  signal?: AbortSignal,
): Promise<SearchResponse> {
  const params = new URLSearchParams();
  if (cursor) params.set("cursor", cursor);
  return searchRequest<SearchResponse>(`/api/v1/models/${modelId}/similar-text?${params}`, {
    signal,
  });
}
export async function searchImage(
  image: File,
  query: { cursor?: string; limit?: number } = {},
  signal?: AbortSignal,
): Promise<SearchResponse> {
  const params = new URLSearchParams({ limit: String(query.limit ?? 30) });
  if (query.cursor) params.set("cursor", query.cursor);
  return searchRequest<SearchResponse>(`/api/v1/search/image?${params}`, {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": image.type },
    body: image,
    cache: "no-store",
    signal,
  });
}
type SearchCommandBody =
  | SearchSettings
  | EndpointProposal
  | GenerationProposal
  | { version_token: SearchGeneration["version_token"] }
  | Partial<Pick<SearchPreferences, "nl_filters_enabled" | "timezone">>;
/** First-party Search commands reconcile in the feature owner, without compatibility invalidations. */
function writeSearch<T>(
  path: string,
  method: "POST" | "PUT" | "PATCH" | "DELETE",
  payload?: SearchCommandBody,
) {
  const options: RequestInit = { method, headers: jsonHeaders() };
  if (payload !== undefined) options.body = JSON.stringify(payload);
  return requestApi<T>(path, options);
}
const configuration = "/api/v1/config/ai-search";
export function getSearchSettings(options: GetJsonOptions = {}) {
  return getJson<SearchSettingsRead>(configuration, { ...options });
}
export async function saveSearchSettings(settings: SearchSettings, base: EditingBase) {
  requireEditingBase(base);
  const result = await requestApi<SearchSettingsRead>(configuration, {
    method: "PUT",
    headers: {
      ...jsonHeaders(),
      "If-Match": `"search-settings-e${base.edit_epoch}-v${base.edit_version}"`,
      "X-PrintStash-Edit-Contract": "conditional-v1",
    },
    body: JSON.stringify(settings),
  });
  requireEditingReceipt(result, base);
  return result;
}
export function createInferenceEndpoint(proposal: EndpointProposal) {
  return writeSearch<InferenceEndpoint>(`${configuration}/endpoints`, "POST", proposal);
}
export function importEnvironmentEndpoint(kind: "embedding" | "chat") {
  return writeSearch<InferenceEndpoint>(
    `${configuration}/endpoints/from-environment/${kind}`,
    "POST",
    {},
  );
}
export function listSearchGenerations(options: GetJsonOptions = {}) {
  return getJson<SearchGeneration[]>(`${configuration}/generations`, { ...options });
}
export function prepareSearchGeneration(proposal: GenerationProposal) {
  return writeSearch<SearchGeneration>(`${configuration}/generations`, "POST", proposal);
}
export function estimateSearchGeneration(
  proposal: GenerationProposal,
  options: Pick<GetJsonOptions, "signal"> = {},
) {
  return requestApi<GenerationEstimate>(`${configuration}/generations/estimate`, {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(proposal),
    ...options,
  });
}
export function actOnSearchGeneration(
  generation: SearchGeneration,
  action: "activate" | "cancel" | "retry",
) {
  return writeSearch<SearchGeneration>(
    `${configuration}/generations/${generation.id}/${action}`,
    "POST",
    { version_token: generation.version_token },
  );
}
export function listInferenceModels(options: GetJsonOptions = {}) {
  return getJson<InferenceModel[]>("/api/v1/inference/models", { ...options });
}
/** The Job kind of a model download; follow it through the Jobs API. */
export const MODEL_DOWNLOAD_KIND = "inference.model_download";

export function downloadInferenceModel(key: string) {
  return writeSearch<{ job_id: string }>(
    `/api/v1/inference/models/${encodeURIComponent(key)}/download`,
    "POST",
    {},
  );
}
export function cancelInferenceDownload(id: string) {
  return writeSearch<void>(
    `/api/v1/inference/models/downloads/${encodeURIComponent(id)}/cancel`,
    "POST",
  );
}
export function validateInferenceModel(id: string) {
  return writeSearch<{ id: string; ready: boolean }>(
    `/api/v1/inference/models/${id}/validate`,
    "POST",
    {},
  );
}
export function deleteInferenceModel(id: string) {
  return writeSearch<void>(`/api/v1/inference/models/${id}`, "DELETE");
}

export function getSearchPreferences(options: Pick<GetJsonOptions, "signal"> = {}) {
  return searchRequest<SearchPreferences>("/api/v1/search/preferences", options);
}
export function saveSearchPreferences(
  value: Partial<Pick<SearchPreferences, "nl_filters_enabled" | "timezone">>,
) {
  return writeSearch<SearchPreferences>("/api/v1/search/preferences", "PATCH", value);
}
export async function parseSearch(query: string, signal?: AbortSignal): Promise<ParsedSearch> {
  return searchRequest<ParsedSearch>("/api/v1/search/parse", {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ query }),
    cache: "no-store",
    signal,
  });
}
