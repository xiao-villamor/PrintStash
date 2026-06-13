import { getJson, sendAction, sendJson } from "@/lib/api/request";
import {
  CollectionCreate,
  CollectionPermissionRead,
  CollectionPermissionUpdate,
  CollectionRead,
  TagCreate,
  TagRead,
} from "@/types";

export function listCollections(): Promise<CollectionRead[]> {
  return getJson<CollectionRead[]>("/api/v1/collections");
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

export function listCollectionPermissions(id: number): Promise<CollectionPermissionRead[]> {
  return getJson<CollectionPermissionRead[]>(`/api/v1/collections/${id}/permissions`, { fresh: true });
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

export function listTags(): Promise<TagRead[]> {
  return getJson<TagRead[]>("/api/v1/tags");
}

export function createTag(payload: TagCreate): Promise<TagRead> {
  return sendJson<TagRead>("/api/v1/tags", "POST", payload);
}

export function deleteTag(id: number): Promise<void> {
  return sendAction(`/api/v1/tags/${id}`, "DELETE");
}
