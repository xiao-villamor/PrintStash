import { getJson } from "./request";
import { modelListSearch } from "./models";
import type {
  OutlinerParams,
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
  return getJson(`/api/v1/outliner/collections?${params(query)}`, { fresh: true, signal });
}
export function listOutlinerEntries(
  query: OutlinerParams,
  signal?: AbortSignal,
): Promise<OutlinerEntryPage> {
  return getJson(`/api/v1/outliner/entries?${params(query)}`, { fresh: true, signal });
}
export function searchOutliner(
  query: OutlinerParams,
  signal?: AbortSignal,
): Promise<OutlinerSearchPage> {
  return getJson(`/api/v1/outliner/search?${params(query)}`, { fresh: true, signal });
}
