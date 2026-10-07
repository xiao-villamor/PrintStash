import {
  authHeaders,
  expectOk,
  getJson,
  handleResponse,
  jsonHeaders,
  requestApi,
} from "@/lib/api/request";
import type { GetJsonOptions } from "@/lib/api/request";
import type {
  ModelProvenancePatch,
  ModelProvenanceRead,
  ModelSourceCoverRead,
} from "@/types/provenance";

function sourceCoverPath(modelId: number, sourceId: number): string {
  return `/api/v1/models/${modelId}/provenance/${sourceId}/cover`;
}

function editHeaders(modelId: number, version: number) {
  if (!Number.isSafeInteger(version) || version < 1)
    throw new Error("Invalid Source editing version");
  return {
    "If-Match": `"model-${modelId}-v${version}"`,
    "X-PrintStash-Edit-Contract": "conditional-v1",
  };
}

function coverVersion(response: Response, modelId: number, base: number): number {
  const match = response.headers.get("ETag")?.match(/^"model-(\d+)-v(\d+)"$/);
  const version = match ? Number(match[2]) : NaN;
  if (!match || Number(match[1]) !== modelId || !Number.isSafeInteger(version) || version <= base)
    throw new Error("Invalid Source cover acknowledgement");
  return version;
}

export interface SourceCoverReceipt {
  cover: ModelSourceCoverRead | null;
  edit_version: number;
}

export function getModelSourceCoverContentPath(modelId: number, sourceId: number): string {
  return `${sourceCoverPath(modelId, sourceId)}/content`;
}

export function getModelSourceCover(
  modelId: number,
  sourceId: number,
): Promise<ModelSourceCoverRead> {
  return getJson<ModelSourceCoverRead>(sourceCoverPath(modelId, sourceId));
}

export async function putModelSourceCover(
  modelId: number,
  sourceId: number,
  file: File,
  editVersion: number,
): Promise<SourceCoverReceipt> {
  const form = new FormData();
  form.append("file", file);
  return requestApi(
    sourceCoverPath(modelId, sourceId),
    {
      method: "PUT",
      headers: { ...authHeaders(), ...editHeaders(modelId, editVersion) },
      body: form,
    },
    async (response, session) => {
      const cover = await handleResponse<ModelSourceCoverRead>(response, session);
      if (cover.provenance_source_id !== sourceId)
        throw new Error("Invalid Source cover acknowledgement");
      return { cover, edit_version: coverVersion(response, modelId, editVersion) };
    },
  );
}

export function deleteModelSourceCover(
  modelId: number,
  sourceId: number,
  editVersion: number,
): Promise<SourceCoverReceipt> {
  return requestApi(
    sourceCoverPath(modelId, sourceId),
    {
      method: "DELETE",
      headers: { ...authHeaders(), ...editHeaders(modelId, editVersion) },
    },
    async (response, session) => {
      await expectOk(response, session);
      return { cover: null, edit_version: coverVersion(response, modelId, editVersion) };
    },
  );
}

export async function getModelProvenance(
  modelId: number,
  options?: GetJsonOptions,
): Promise<ModelProvenanceRead> {
  const value = await getJson<ModelProvenanceRead>(`/api/v1/models/${modelId}/provenance`, options);
  if (!Number.isSafeInteger(value.edit_version) || value.edit_version < 1)
    throw new Error("Invalid Source snapshot");
  return value;
}

export function patchModelProvenance(
  modelId: number,
  sourceId: number,
  payload: ModelProvenancePatch,
  editVersion: number,
): Promise<ModelProvenanceRead> {
  return requestApi(
    `/api/v1/models/${modelId}/provenance/${sourceId}`,
    {
      method: "PATCH",
      headers: { ...jsonHeaders(), ...editHeaders(modelId, editVersion) },
      body: JSON.stringify(payload),
    },
    async (response, session) => {
      const value = await handleResponse<ModelProvenanceRead>(response, session);
      if (!Number.isSafeInteger(value.edit_version) || value.edit_version <= editVersion)
        throw new Error("Invalid Source acknowledgement");
      return value;
    },
  );
}
