import { getJson, sendAction, sendForm, sendJson, type GetJsonOptions } from "@/lib/api/request";
import {
  CollectionCreate,
  CollectionPermissionRead,
  CollectionPermissionUpdate,
  CollectionRead,
  TagCreate,
  TagRead,
} from "@/types";

export function listCollections(options?: GetJsonOptions): Promise<CollectionRead[]> {
  return getJson<CollectionRead[]>("/api/v1/collections", options);
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
