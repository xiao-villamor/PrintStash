import type { EditingBase } from "@/types/editing";
import { editHeaders, requireEditingReceipt } from "./editing";
import {
  type GetJsonOptions,
  getJson,
  handleResponse,
  requestApi,
  authHeaders,
  jsonHeaders,
  requestMutation,
  sendAction,
  sendJson,
} from "@/lib/api/request";
import type { SessionRequest } from "@/lib/session-transport";
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
  favorites?: boolean;
  limit?: number;
  offset?: number;
}

function multipartSearch(params?: ListMultipartModelsParams): string {
  const search = new URLSearchParams();
  if (params?.collection) search.set("collection", params.collection);
  if (params?.direct) search.set("direct", "true");
  if (params?.q) search.set("q", params.q);
  params?.tag?.forEach((tag) => search.append("tag", tag));
  if (params?.favorites) search.set("favorites", "true");
  if (params?.limit != null) search.set("limit", String(params.limit));
  if (params?.offset != null) search.set("offset", String(params.offset));
  const query = search.toString();
  return query ? `?${query}` : "";
}

export function listMultipartModels(
  params?: ListMultipartModelsParams,
  options?: GetJsonOptions,
): Promise<MultipartModelListItem[]> {
  return getJson<MultipartModelListItem[]>(`/api/v1/multipart-models${multipartSearch(params)}`, {
    fresh: true,
    ...options,
  });
}

export function createMultipartModel(payload: MultipartModelCreate): Promise<MultipartModelRead> {
  return sendJson<MultipartModelRead>("/api/v1/multipart-models", "POST", payload);
}

export function getMultipartModel(
  id: number,
  options?: GetJsonOptions,
): Promise<MultipartModelRead> {
  return getJson<MultipartModelRead>(`/api/v1/multipart-models/${id}`, options);
}

/** A successful status alone cannot confirm which aggregate/version was saved. */
function editingReceipt(id: number, base: EditingBase) {
  return async (response: Response, session: SessionRequest): Promise<MultipartModelRead> => {
    const saved = await handleResponse<MultipartModelRead>(response, session);
    if (
      saved?.id !== id ||
      !Number.isSafeInteger(saved.edit_version) ||
      saved.edit_version <= base.edit_version ||
      saved.edit_epoch !== base.edit_epoch
    )
      throw new Error("Invalid Multipart acknowledgement");
    requireEditingReceipt(saved, base);
    return saved;
  };
}

/** Save metadata and the complete composition in one transaction. */
export function saveMultipartModel(
  id: number,
  payload: MultipartPartsWrite,
  base: EditingBase,
): Promise<MultipartModelRead> {
  return requestApi<MultipartModelRead>(
    `/api/v1/multipart-models/${id}`,
    {
      method: "PUT",
      headers: { ...jsonHeaders(), ...editHeaders("multipart", id, base) },
      body: JSON.stringify(payload),
    },
    editingReceipt(id, base),
  );
}

export function deleteMultipartModel(id: number): Promise<void> {
  return sendAction(`/api/v1/multipart-models/${id}`, "DELETE");
}

export async function uploadMultipartModelCover(
  id: number,
  file: File,
  base: EditingBase,
): Promise<MultipartModelRead> {
  const path = `/api/v1/multipart-models/${id}/cover`;
  const body = new FormData();
  body.append("file", file);
  return requestApi<MultipartModelRead>(
    path,
    {
      method: "PUT",
      headers: { ...authHeaders(), ...editHeaders("multipart", id, base) },
      body,
    },
    editingReceipt(id, base),
  );
}

export async function deleteMultipartModelCover(
  id: number,
  base: EditingBase,
): Promise<MultipartModelRead> {
  const path = `/api/v1/multipart-models/${id}/cover`;
  return requestApi<MultipartModelRead>(
    path,
    {
      method: "DELETE",
      headers: { ...authHeaders(), ...editHeaders("multipart", id, base) },
    },
    editingReceipt(id, base),
  );
}

export function replaceMultipartModelTags(
  id: number,
  tags: string[],
  base: EditingBase,
): Promise<MultipartModelRead> {
  return requestApi<MultipartModelRead>(
    `/api/v1/multipart-models/${id}/tags`,
    {
      method: "PUT",
      headers: { ...jsonHeaders(), ...editHeaders("multipart", id, base) },
      body: JSON.stringify({ tags }),
    },
    editingReceipt(id, base),
  );
}

export interface MultipartModelStarRead {
  multipart_model_id: number;
  starred: boolean;
}

export function starMultipartModel(id: number): Promise<MultipartModelStarRead> {
  return sendJson<MultipartModelStarRead>(`/api/v1/multipart-models/${id}/star`, "PUT", {});
}

export async function unstarMultipartModel(id: number): Promise<MultipartModelStarRead> {
  const path = `/api/v1/multipart-models/${id}/star`;
  return requestMutation<MultipartModelStarRead>(path, { method: "DELETE" });
}

export function listMultipartModelCandidates(
  id: number,
  params?: { q?: string; limit?: number; offset?: number; collection?: string; direct?: boolean },
): Promise<MultipartModelCandidate[]> {
  const search = new URLSearchParams();
  if (params?.q) search.set("q", params.q);
  if (params?.limit != null) search.set("limit", String(params.limit));
  if (params?.offset != null) search.set("offset", String(params.offset));
  if (params?.collection) search.set("collection", params.collection);
  if (params?.direct) search.set("direct", "true");
  const query = search.toString();
  return getJson<MultipartModelCandidate[]>(
    `/api/v1/multipart-models/${id}/candidates${query ? `?${query}` : ""}`,
    { fresh: true },
  );
}
