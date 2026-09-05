import { getJson, sendJson } from "./request";
import type { MultipartBuild } from "@/types/multipart-builds";
import type { BatchCreate } from "@/types";

const base = "/api/v1/multipart-builds";
export function listMultipartBuilds(archived = false, offset = 0): Promise<MultipartBuild[]> {
  return getJson(`${base}?archived=${archived}&offset=${offset}&limit=50`, { fresh: true });
}
export function getMultipartBuild(id: number): Promise<MultipartBuild> {
  return getJson(`${base}/${id}`, { fresh: true });
}
export function createMultipartBuild(body: {
  name: string;
  multipart_model_id: number;
  object_quantity: number;
}): Promise<MultipartBuild> {
  return sendJson(base, "POST", body);
}
export function selectBuildRevision(
  id: number,
  partId: number,
  body: { version: number; choice_id?: number; revision_id: number | null },
): Promise<MultipartBuild> {
  return sendJson(`${base}/${id}/parts/${partId}`, "PATCH", body);
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
  return sendJson(`${base}/${id}/parts/${partId}/queue`, "POST", body);
}
export function confirmBuildResult(
  id: number,
  attemptId: number,
  body: { version: number; valid_units: number; idempotency_key: string },
): Promise<MultipartBuild> {
  return sendJson(`${base}/${id}/attempts/${attemptId}/confirm`, "POST", body);
}
export function duplicateMultipartBuild(id: number, name: string): Promise<MultipartBuild> {
  return sendJson(`${base}/${id}/duplicate`, "POST", { name });
}
export function archiveMultipartBuild(
  id: number,
  version: number,
  archived: boolean,
): Promise<MultipartBuild> {
  return sendJson(`${base}/${id}/archive`, "PATCH", { version, archived });
}
