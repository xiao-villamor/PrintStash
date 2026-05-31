import {
  getJson,
  sendAction,
  sendForm,
  sendJson,
} from "@/lib/api/request";
import {
  FileRevisionUpdate,
  IngestJobStatus,
  IngestResponse,
  ListModelsParams,
  ModelListItem,
  ModelRead,
  ModelUpdate,
} from "@/types";

export async function listModels(
  params?: ListModelsParams,
): Promise<ModelListItem[]> {
  const search = new URLSearchParams();
  if (params?.category) search.set("category", params.category);
  if (params?.q) search.set("q", params.q);
  if (params?.limit) search.set("limit", String(params.limit));
  if (params?.offset) search.set("offset", String(params.offset));
  for (const tag of params?.tag ?? []) {
    search.append("tag", tag);
  }

  const query = search.toString();
  return getJson<ModelListItem[]>(`/api/v1/models${query ? `?${query}` : ""}`);
}

export function getModel(id: number): Promise<ModelRead> {
  return getJson<ModelRead>(`/api/v1/models/${id}`);
}

export function updateModel(
  id: number,
  payload: ModelUpdate,
  apiKey?: string,
): Promise<ModelRead> {
  return sendJson<ModelRead>(`/api/v1/models/${id}`, "PATCH", payload, apiKey);
}

export function deleteModel(id: number, apiKey?: string): Promise<void> {
  return sendAction(`/api/v1/models/${id}`, "DELETE", apiKey);
}

export function updateFileRevision(
  modelId: number,
  fileId: number,
  payload: FileRevisionUpdate,
  apiKey?: string,
): Promise<ModelRead> {
  return sendJson<ModelRead>(
    `/api/v1/models/${modelId}/files/${fileId}/revision`,
    "PATCH",
    payload,
    apiKey,
  );
}

export function ingestOrca(
  formData: FormData,
  apiKey?: string,
): Promise<IngestResponse> {
  return sendForm<IngestResponse>("/api/v1/ingest/orca", formData, apiKey);
}

export function ingestModel(
  formData: FormData,
  apiKey?: string,
): Promise<IngestResponse> {
  return sendForm<IngestResponse>("/api/v1/ingest/model", formData, apiKey);
}

export function getJobStatus(jobId: string): Promise<IngestJobStatus> {
  return getJson<IngestJobStatus>(`/api/v1/ingest/jobs/${jobId}`);
}
