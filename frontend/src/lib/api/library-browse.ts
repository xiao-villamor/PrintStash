import { getJson, type GetJsonOptions } from "./request";
import { modelListSearch } from "./models";
import type {
  LibraryAuthority,
  LibraryBrowsePage,
  LibraryBrowseParams,
  LibraryThumbnailProjection,
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

export function getLibraryThumbnails(
  ids: number[],
  options?: GetJsonOptions,
): Promise<LibraryThumbnailProjection> {
  const search = new URLSearchParams();
  for (const id of ids) search.append("model_id", String(id));
  return getJson<LibraryThumbnailProjection>(`/api/v1/models/browse/thumbnails?${search}`, options);
}
