import { getJson, requestApi } from "./request";
import { modelListSearch } from "./models";
import type {
  OutlinerParams,
  OutlinerRestoreParams,
  OutlinerRestoreRead,
  OutlinerCollectionPage,
  OutlinerEntryPage,
  OutlinerSearchPage,
} from "@/types/outliner";

function params(query: OutlinerParams): string {
  const result = modelListSearch({ ...query, limit: 50 });
  result.set("view", query.view);
  for (const key of ["cursor", "parent_id", "collection_id", "reveal_id"] as const) {
    if (query[key] !== undefined) result.set(key, String(query[key]));
  }
  return result.toString();
}
export function listOutlinerCollections(
  query: OutlinerParams,
  signal?: AbortSignal,
): Promise<OutlinerCollectionPage> {
  return getJson(`/api/v1/outliner/collections?${params(query)}`, { signal });
}
export function listOutlinerEntries(
  query: OutlinerParams,
  signal?: AbortSignal,
): Promise<OutlinerEntryPage> {
  return getJson(`/api/v1/outliner/entries?${params(query)}`, { signal });
}
export function searchOutliner(
  query: OutlinerParams,
  signal?: AbortSignal,
): Promise<OutlinerSearchPage> {
  return getJson(`/api/v1/outliner/search?${params(query)}`, { signal });
}

export function restoreOutliner(
  query: OutlinerRestoreParams,
  signal?: AbortSignal,
): Promise<OutlinerRestoreRead> {
  return requestApi("/api/v1/outliner/restore", {
    method: "POST",
    body: JSON.stringify({ ...query, limit: 50 }),
    headers: { "Content-Type": "application/json" },
    signal,
  });
}
