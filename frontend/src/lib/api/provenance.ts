import type { EditingBase } from "@/types/editing";
import { editHeaders, requireEditingBase, requireEditingReceipt } from "./editing";
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

function coverVersion(response: Response, modelId: number, base: EditingBase): number {
  const match = response.headers.get("ETag")?.match(/^"model-(\d+)-e([0-9a-f]{32})-v(\d+)"$/);
  const version = match ? Number(match[3]) : NaN;
  if (
    !match ||
    Number(match[1]) !== modelId ||
    !Number.isSafeInteger(version) ||
    version <= base.edit_version ||
    match[2] !== base.edit_epoch
  )
    throw new Error("Invalid Source cover acknowledgement");
  return version;
}

export interface SourceCoverReceipt extends EditingBase {
  cover: ModelSourceCoverRead | null;
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
  base: EditingBase,
): Promise<SourceCoverReceipt> {
  const form = new FormData();
  form.append("file", file);
  return requestApi(
    sourceCoverPath(modelId, sourceId),
    {
      method: "PUT",
      headers: { ...authHeaders(), ...editHeaders("model", modelId, base) },
      body: form,
    },
    async (response, session) => {
      const cover = await handleResponse<ModelSourceCoverRead>(response, session);
      if (cover.provenance_source_id !== sourceId)
        throw new Error("Invalid Source cover acknowledgement");
      return {
        cover,
        edit_version: coverVersion(response, modelId, base),
        edit_epoch: base.edit_epoch,
      };
    },
  );
}

export function deleteModelSourceCover(
  modelId: number,
  sourceId: number,
  base: EditingBase,
): Promise<SourceCoverReceipt> {
  return requestApi(
    sourceCoverPath(modelId, sourceId),
    {
      method: "DELETE",
      headers: { ...authHeaders(), ...editHeaders("model", modelId, base) },
    },
    async (response, session) => {
      await expectOk(response, session);
      return {
        cover: null,
        edit_version: coverVersion(response, modelId, base),
        edit_epoch: base.edit_epoch,
      };
    },
  );
}

export async function getModelProvenance(
  modelId: number,
  options?: GetJsonOptions,
): Promise<ModelProvenanceRead> {
  const value = await getJson<ModelProvenanceRead>(`/api/v1/models/${modelId}/provenance`, options);
  requireEditingBase(value);
  return value;
}

export function patchModelProvenance(
  modelId: number,
  sourceId: number,
  payload: ModelProvenancePatch,
  base: EditingBase,
): Promise<ModelProvenanceRead> {
  return requestApi(
    `/api/v1/models/${modelId}/provenance/${sourceId}`,
    {
      method: "PATCH",
      headers: { ...jsonHeaders(), ...editHeaders("model", modelId, base) },
      body: JSON.stringify(payload),
    },
    async (response, session) => {
      const value = await handleResponse<ModelProvenanceRead>(response, session);
      requireEditingReceipt(value, base);
      return value;
    },
  );
}
