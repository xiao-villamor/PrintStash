import { getJson, sendAction, sendJson } from "@/lib/api/request";
import { CollectionCreate, CollectionRead, TagCreate, TagRead } from "@/types";

export function listCollections(): Promise<CollectionRead[]> {
  return getJson<CollectionRead[]>("/api/v1/collections");
}

export function createCollection(payload: CollectionCreate): Promise<CollectionRead> {
  return sendJson<CollectionRead>("/api/v1/collections", "POST", payload);
}

export function deleteCollection(id: number): Promise<void> {
  return sendAction(`/api/v1/collections/${id}`, "DELETE");
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
