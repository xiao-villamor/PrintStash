import { getJson, requestApi, jsonHeaders, type GetJsonOptions } from "./request";
import type { MultipartBuild } from "@/types/multipart-builds";
import type { BatchCreate } from "@/types";

const base = "/api/v1/multipart-builds";
export function listMultipartBuilds(
  archived = false,
  offset = 0,
  options: GetJsonOptions = {},
): Promise<MultipartBuild[]> {
  return getJson(`${base}?archived=${archived}&offset=${offset}&limit=50`, {
    ...options,
    fresh: true,
  });
}
export function getMultipartBuild(
  id: number,
  options: GetJsonOptions = {},
): Promise<MultipartBuild> {
  return getJson(`${base}/${id}`, { ...options, fresh: true });
}
export function createMultipartBuild(body: {
  name: string;
  multipart_model_id: number;
  object_quantity: number;
}): Promise<MultipartBuild> {
  return requestApi(base, { method: "POST", headers: jsonHeaders(), body: JSON.stringify(body) });
}
export function selectBuildRevision(
  id: number,
  partId: number,
  body: { version: number; choice_id?: number; revision_id: number | null },
): Promise<MultipartBuild> {
  return requestApi(`${base}/${id}/parts/${partId}`, {
    method: "PATCH",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
}
export function queueBuildPart(
  id: number,
  partId: number,
  body: {
    version: number;
    units_per_job: number;
    job_count: number;
    confirm_excess: boolean;
    routing: Omit<BatchCreate, "file_id" | "quantity">;
  },
): Promise<MultipartBuild> {
  return requestApi(`${base}/${id}/parts/${partId}/queue`, {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
}
export function confirmBuildResult(
  id: number,
  attemptId: number,
  body: { version: number; valid_units: number; idempotency_key: string },
): Promise<MultipartBuild> {
  return requestApi(`${base}/${id}/attempts/${attemptId}/confirm`, {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
}
export function duplicateMultipartBuild(id: number, name: string): Promise<MultipartBuild> {
  return requestApi(`${base}/${id}/duplicate`, {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify({ name }),
  });
}
export function archiveMultipartBuild(
  id: number,
  version: number,
  archived: boolean,
): Promise<MultipartBuild> {
  return requestApi(`${base}/${id}/archive`, {
    method: "PATCH",
    headers: jsonHeaders(),
    body: JSON.stringify({ version, archived }),
  });
}
