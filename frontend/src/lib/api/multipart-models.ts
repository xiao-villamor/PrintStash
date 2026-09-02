import { getJson, sendAction, sendJson } from "@/lib/api/request";
import type {
  MultipartModelCandidate,
  MultipartModelCreate,
  MultipartModelListItem,
  MultipartModelRead,
  MultipartPartsWrite,
} from "@/types";

export interface ListMultipartModelsParams {
  collection?: string;
  direct?: boolean;
  q?: string;
  tag?: string[];
  limit?: number;
  offset?: number;
}

function multipartSearch(params?: ListMultipartModelsParams): string {
  const search = new URLSearchParams();
  if (params?.collection) search.set("collection", params.collection);
  if (params?.direct) search.set("direct", "true");
  if (params?.q) search.set("q", params.q);
  params?.tag?.forEach((tag) => search.append("tag", tag));
  if (params?.limit != null) search.set("limit", String(params.limit));
  if (params?.offset != null) search.set("offset", String(params.offset));
  const query = search.toString();
  return query ? `?${query}` : "";
}

export function listMultipartModels(
  params?: ListMultipartModelsParams,
): Promise<MultipartModelListItem[]> {
  return getJson<MultipartModelListItem[]>(`/api/v1/multipart-models${multipartSearch(params)}`, {
    fresh: true,
  });
}

export function createMultipartModel(payload: MultipartModelCreate): Promise<MultipartModelRead> {
  return sendJson<MultipartModelRead>("/api/v1/multipart-models", "POST", payload);
}

export function getMultipartModel(id: number): Promise<MultipartModelRead> {
  return getJson<MultipartModelRead>(`/api/v1/multipart-models/${id}`, { fresh: true });
}

/** Save metadata and the complete composition in one transaction. */
export function saveMultipartModel(
  id: number,
  payload: MultipartPartsWrite,
): Promise<MultipartModelRead> {
  return sendJson<MultipartModelRead>(`/api/v1/multipart-models/${id}`, "PUT", payload);
}

export function deleteMultipartModel(id: number): Promise<void> {
  return sendAction(`/api/v1/multipart-models/${id}`, "DELETE");
}

export function replaceMultipartModelTags(id: number, tags: string[]): Promise<MultipartModelRead> {
  return sendJson<MultipartModelRead>(`/api/v1/multipart-models/${id}/tags`, "PUT", { tags });
}

export function listMultipartModelCandidates(
  id: number,
  params?: { q?: string; limit?: number },
): Promise<MultipartModelCandidate[]> {
  const search = new URLSearchParams();
  if (params?.q) search.set("q", params.q);
  if (params?.limit != null) search.set("limit", String(params.limit));
  const query = search.toString();
  return getJson<MultipartModelCandidate[]>(
    `/api/v1/multipart-models/${id}/candidates${query ? `?${query}` : ""}`,
    { fresh: true },
  );
}
