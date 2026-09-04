import {
  authHeaders,
  getJson,
  getUrl,
  handleResponse,
  invalidateApiCache,
  sendAction,
  sendJson,
} from "@/lib/api/request";
import type {
  ModelProvenancePatch,
  ModelProvenanceRead,
  ModelSourceCoverRead,
} from "@/types/provenance";

function sourceCoverPath(modelId: number, sourceId: number): string {
  return `/api/v1/models/${modelId}/provenance/${sourceId}/cover`;
}

export function getModelSourceCoverContentPath(modelId: number, sourceId: number): string {
  return `${sourceCoverPath(modelId, sourceId)}/content`;
}

export function getModelSourceCover(
  modelId: number,
  sourceId: number,
): Promise<ModelSourceCoverRead> {
  return getJson<ModelSourceCoverRead>(sourceCoverPath(modelId, sourceId), { fresh: true });
}

export async function putModelSourceCover(
  modelId: number,
  sourceId: number,
  file: File,
): Promise<ModelSourceCoverRead> {
  const form = new FormData();
  form.append("file", file);
  const path = sourceCoverPath(modelId, sourceId);
  const res = await fetch(getUrl(path), {
    method: "PUT",
    headers: authHeaders(),
    body: form,
  });
  invalidateApiCache(path);
  return handleResponse<ModelSourceCoverRead>(res);
}

export function deleteModelSourceCover(modelId: number, sourceId: number): Promise<void> {
  return sendAction(sourceCoverPath(modelId, sourceId), "DELETE");
}

export function getModelProvenance(modelId: number): Promise<ModelProvenanceRead> {
  return getJson<ModelProvenanceRead>(`/api/v1/models/${modelId}/provenance`);
}

export function patchModelProvenance(
  modelId: number,
  sourceId: number,
  payload: ModelProvenancePatch,
): Promise<ModelProvenanceRead> {
  return sendJson<ModelProvenanceRead>(
    `/api/v1/models/${modelId}/provenance/${sourceId}`,
    "PATCH",
    payload,
  );
}
