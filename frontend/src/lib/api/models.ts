import {
  authHeaders,
  expectOk,
  getJson,
  getUrl,
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
  ModelPrinterFileRead,
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
  if (params?.printer_id) search.set("printer_id", String(params.printer_id));
  if (params?.printer_presence) search.set("printer_presence", params.printer_presence);
  for (const tag of params?.tag ?? []) {
    search.append("tag", tag);
  }

  const query = search.toString();
  return getJson<ModelListItem[]>(`/api/v1/models${query ? `?${query}` : ""}`);
}

export function getModel(id: number): Promise<ModelRead> {
  return getJson<ModelRead>(`/api/v1/models/${id}`);
}

export async function downloadModelExport(format: "json" | "csv"): Promise<void> {
  const res = await fetch(getUrl(`/api/v1/models/export?format=${format}`), {
    headers: authHeaders(),
    cache: "no-store",
  });
  await expectOk(res);
  const blob = await res.blob();
  const fallback = `printstash-model-export.${format}`;
  const disposition = res.headers.get("content-disposition") ?? "";
  const filename = disposition.match(/filename="([^"]+)"/)?.[1] ?? fallback;
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export function getModelPrinterFiles(id: number): Promise<ModelPrinterFileRead[]> {
  return getJson<ModelPrinterFileRead[]>(`/api/v1/models/${id}/printer-files`);
}

export function updateModel(
  id: number,
  payload: ModelUpdate,
): Promise<ModelRead> {
  return sendJson<ModelRead>(`/api/v1/models/${id}`, "PATCH", payload);
}

export function deleteModel(id: number): Promise<void> {
  return sendAction(`/api/v1/models/${id}`, "DELETE");
}

export function updateFileRevision(
  modelId: number,
  fileId: number,
  payload: FileRevisionUpdate,
): Promise<ModelRead> {
  return sendJson<ModelRead>(
    `/api/v1/models/${modelId}/files/${fileId}/revision`,
    "PATCH",
    payload,
  );
}

export function addGcodeRevision(
  modelId: number,
  formData: FormData,
): Promise<ModelRead> {
  return sendForm<ModelRead>(
    `/api/v1/models/${modelId}/gcode-revisions`,
    formData,
  );
}

export function ingestOrca(formData: FormData): Promise<IngestResponse> {
  return sendForm<IngestResponse>("/api/v1/ingest/orca", formData);
}

export function ingestModel(formData: FormData): Promise<IngestResponse> {
  return sendForm<IngestResponse>("/api/v1/ingest/model", formData);
}

export function getJobStatus(jobId: string): Promise<IngestJobStatus> {
  return getJson<IngestJobStatus>(`/api/v1/ingest/jobs/${jobId}`);
}
