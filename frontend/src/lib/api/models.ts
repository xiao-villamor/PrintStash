import {
  authHeaders,
  expectOk,
  getJson,
  GetJsonOptions,
  getUrl,
  handleResponse,
  invalidateApiCache,
  sendAction,
  sendForm,
  sendJson,
} from "@/lib/api/request";
import {
  ArchiveManifest,
  FileRevisionUpdate,
  ImportedPrintJobRead,
  IngestJobStatus,
  IngestResponse,
  ListModelsParams,
  ManualPrintJobCreate,
  ModelListItem,
  ModelPrinterFileRead,
  ModelPrintJobRead,
  ModelRead,
  ModelUpdate,
  TrashPurgeRead,
  TrashedModelRead,
  VaultStatsRead,
} from "@/types";

export async function listModels(
  params?: ListModelsParams,
): Promise<ModelListItem[]> {
  const search = new URLSearchParams();
  if (params?.collection) search.set("collection", params.collection);
  if (params?.direct) search.set("direct", "true");
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

export function getVaultStats(
  options?: GetJsonOptions,
): Promise<VaultStatsRead> {
  return getJson<VaultStatsRead>("/api/v1/models/stats", options);
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

export function getModelPrintJobs(id: number): Promise<ModelPrintJobRead[]> {
  return getJson<ModelPrintJobRead[]>(`/api/v1/models/${id}/print-jobs`);
}

export function createManualPrintJob(
  modelId: number,
  payload: ManualPrintJobCreate,
): Promise<ModelPrintJobRead> {
  return sendJson<ModelPrintJobRead>(`/api/v1/models/${modelId}/print-jobs`, "POST", payload);
}

export function importPrintJobsFromPrinter(
  modelId: number,
  printerId: number,
): Promise<ImportedPrintJobRead[]> {
  return sendJson<ImportedPrintJobRead[]>(
    `/api/v1/models/${modelId}/print-jobs/import-printer/${printerId}`,
    "POST",
    {},
  );
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

export function listTrash(): Promise<TrashedModelRead[]> {
  return getJson<TrashedModelRead[]>("/api/v1/models/trash");
}

export function restoreModel(id: number): Promise<ModelRead> {
  return sendJson<ModelRead>(`/api/v1/models/${id}/restore`, "POST", {});
}

export async function purgeModel(id: number): Promise<TrashPurgeRead> {
  const res = await fetch(getUrl(`/api/v1/models/${id}/purge`), {
    method: "DELETE",
    headers: authHeaders(),
  });
  invalidateApiCache(`/api/v1/models/${id}/purge`);
  return handleResponse<TrashPurgeRead>(res);
}

export async function purgeExpiredTrash(): Promise<TrashPurgeRead> {
  const res = await fetch(getUrl("/api/v1/models/trash/expired"), {
    method: "DELETE",
    headers: authHeaders(),
  });
  invalidateApiCache("/api/v1/models/trash/expired");
  return handleResponse<TrashPurgeRead>(res);
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

export async function deleteFileRevision(
  modelId: number,
  fileId: number,
): Promise<ModelRead> {
  const path = `/api/v1/models/${modelId}/files/${fileId}/revision`;
  const res = await fetch(getUrl(path), {
    method: "DELETE",
    headers: authHeaders(),
  });
  invalidateApiCache(path);
  return handleResponse<ModelRead>(res);
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
  return getJson<IngestJobStatus>(`/api/v1/ingest/jobs/${jobId}`, { fresh: true });
}

export function ingestUrl(payload: {
  url: string;
  collection?: string;
  model_name?: string;
  tags?: string;
}): Promise<IngestResponse> {
  return sendJson<IngestResponse>("/api/v1/ingest/url", "POST", payload);
}

export function ingestArchive(formData: FormData): Promise<ArchiveManifest> {
  return sendForm<ArchiveManifest>("/api/v1/ingest/archive", formData);
}

export function selectArchiveEntries(
  archiveId: string,
  payload: { names: string[]; collection?: string; tags?: string },
): Promise<IngestResponse> {
  return sendJson<IngestResponse>(
    `/api/v1/ingest/archive/${archiveId}/select`,
    "POST",
    payload,
  );
}
