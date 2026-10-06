import { getJson, type GetJsonOptions } from "./request";
import { modelListSearch } from "./models";
import type {
  LibraryAuthority,
  LibraryBrowsePage,
  LibraryBrowseParams,
} from "@/types/library-browse";

export function listLibraryPage(
  params: LibraryBrowseParams & { cursor?: string },
  options?: GetJsonOptions,
): Promise<LibraryBrowsePage> {
  const search = modelListSearch(params);
  search.set("view", params.view);
  search.set("sort", params.sort);
  if (params.cursor) search.set("cursor", params.cursor);
  return getJson<LibraryBrowsePage>(`/api/v1/models/browse?${search}`, options);
}

export function getLibraryRevision(options?: GetJsonOptions): Promise<LibraryAuthority> {
  return getJson<LibraryAuthority>("/api/v1/models/browse/revision", options);
}
