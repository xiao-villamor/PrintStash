import { getJson, sendAction, sendForm, sendJson, type GetJsonOptions } from "@/lib/api/request";
import {
  CollectionCreate,
  CollectionLookupRead,
  CollectionPage,
  CollectionRole,
  CollectionPermissionRead,
  CollectionPermissionUpdate,
  CollectionRead,
  TagCreate,
  TagRead,
} from "@/types";

export function listCollections(options?: GetJsonOptions): Promise<CollectionRead[]> {
  return getJson<CollectionRead[]>("/api/v1/collections", options);
}

/** One page of a collection's children, or of the caller's top level when `parentId` is null. */
export function listCollectionChildren(
  parentId: number | null,
  cursor: string | null = null,
  limit = 200,
): Promise<CollectionPage> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (parentId !== null) params.set("parent_id", String(parentId));
  if (cursor !== null) params.set("cursor", cursor);
  return getJson<CollectionPage>(`/api/v1/collections/children?${params}`, { fresh: true });
}

/** The collection at `path` with its visible ancestors, root first. */
export function lookupCollection(path: string): Promise<CollectionLookupRead> {
  const params = new URLSearchParams({ path });
  return getJson<CollectionLookupRead>(`/api/v1/collections/lookup?${params}`, { fresh: true });
}

/** Collections whose name contains `query`, held at `minRole` or above. */
export function searchCollections(
  query: string,
  minRole: CollectionRole = "view",
  cursor: string | null = null,
  limit = 20,
): Promise<CollectionPage> {
  const params = new URLSearchParams({ q: query, min_role: minRole, limit: String(limit) });
  if (cursor !== null) params.set("cursor", cursor);
  return getJson<CollectionPage>(`/api/v1/collections/search?${params}`, { fresh: true });
}

export function createCollection(payload: CollectionCreate): Promise<CollectionRead> {
  return sendJson<CollectionRead>("/api/v1/collections", "POST", payload);
}

export function deleteCollection(id: number, recursive = false): Promise<void> {
  const url = `/api/v1/collections/${id}${recursive ? "?recursive=true" : ""}`;
  return sendAction(url, "DELETE");
}

export function moveCollection(id: number, parentId: number | null): Promise<CollectionRead> {
  return sendJson<CollectionRead>(`/api/v1/collections/${id}`, "PATCH", { parent_id: parentId });
}

export function renameCollection(id: number, name: string): Promise<CollectionRead> {
  return sendJson<CollectionRead>(`/api/v1/collections/${id}`, "PATCH", { name });
}

export function replaceCollectionTags(id: number, tags: string[]): Promise<CollectionRead> {
  return sendJson<CollectionRead>(`/api/v1/collections/${id}/tags`, "PUT", { tags });
}

export function getCollectionReadme(id: number): Promise<{ readme: string | null }> {
  return getJson<{ readme: string | null }>(`/api/v1/collections/${id}/readme`, { fresh: true });
}

export function setCollectionReadme(
  id: number,
  readme: string | null,
): Promise<{ readme: string | null }> {
  return sendJson<{ readme: string | null }>(`/api/v1/collections/${id}/readme`, "PUT", { readme });
}

export function uploadCollectionImage(id: number, file: File): Promise<{ url: string }> {
  const form = new FormData();
  form.append("file", file);
  return sendForm<{ url: string }>(`/api/v1/collections/${id}/images`, form);
}

export function listCollectionPermissions(id: number): Promise<CollectionPermissionRead[]> {
  return getJson<CollectionPermissionRead[]>(`/api/v1/collections/${id}/permissions`, {
    fresh: true,
  });
}

export function updateCollectionPermission(
  collectionId: number,
  userId: number,
  payload: CollectionPermissionUpdate,
): Promise<CollectionPermissionRead> {
  return sendJson<CollectionPermissionRead>(
    `/api/v1/collections/${collectionId}/permissions/${userId}`,
    "PUT",
    payload,
  );
}

export function deleteCollectionPermission(collectionId: number, userId: number): Promise<void> {
  return sendAction(`/api/v1/collections/${collectionId}/permissions/${userId}`, "DELETE");
}

export function listTags(options?: GetJsonOptions): Promise<TagRead[]> {
  return getJson<TagRead[]>("/api/v1/tags", options);
}

export function createTag(payload: TagCreate): Promise<TagRead> {
  return sendJson<TagRead>("/api/v1/tags", "POST", payload);
}

export function deleteTag(id: number): Promise<void> {
  return sendAction(`/api/v1/tags/${id}`, "DELETE");
}
