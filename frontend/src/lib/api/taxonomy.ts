import { getJson, sendAction, sendJson } from "@/lib/api/request";
import { CategoryCreate, CategoryRead, TagCreate, TagRead } from "@/types";

export function listCategories(): Promise<CategoryRead[]> {
  return getJson<CategoryRead[]>("/api/v1/categories");
}

export function createCategory(payload: CategoryCreate): Promise<CategoryRead> {
  return sendJson<CategoryRead>("/api/v1/categories", "POST", payload);
}

export function deleteCategory(id: number): Promise<void> {
  return sendAction(`/api/v1/categories/${id}`, "DELETE");
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
