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
  ArtifactOutcomeRead,
  FileRevisionUpdate,
  ImportedPrintJobRead,
  IngestJobStatus,
  IngestResponse,
  ListModelPageParams,
  ListModelsParams,
  ManualPrintJobCreate,
  ModelBatchResult,
  ModelListItem,
  ModelFacetsRead,
  ModelPageRead,
  ModelPrinterFileRead,
  ModelPrintJobRead,
  ModelRead,
  ModelStarRead,
  ModelUpdate,
  OutlinerModelRead,
  RevisionBatchResult,
  TrashPurgeRead,
  TrashedModelRead,
  VaultStatsRead,
} from "@/types";

function modelListSearch(params?: ListModelsParams): URLSearchParams {
  const search = new URLSearchParams();
  if (params?.collection) search.set("collection", params.collection);
  if (params?.direct) search.set("direct", "true");
  if (params?.q) search.set("q", params.q);
  if (params?.limit) search.set("limit", String(params.limit));
  if (params?.offset) search.set("offset", String(params.offset));
  if (params?.printer_id) search.set("printer_id", String(params.printer_id));
  if (params?.printer_presence) search.set("printer_presence", params.printer_presence);
  if (params?.favorites) search.set("favorites", "true");
  for (const tag of params?.tag ?? []) {
    search.append("tag", tag);
  }
  for (const key of ["file_type", "material_type", "slicer_name", "printer_model", "revision_status", "print_outcome", "storage"] as const) {
    for (const value of params?.[key] ?? []) search.append(key, String(value));
  }
  if (params?.printed !== undefined) search.set("printed", String(params.printed));
  if (params?.uploaded_after) search.set("uploaded_after", params.uploaded_after);
  if (params?.uploaded_before) search.set("uploaded_before", params.uploaded_before);

  return search;
}

export async function listModels(
  params?: ListModelsParams,
): Promise<ModelListItem[]> {
  const query = modelListSearch(params).toString();
  return getJson<ModelListItem[]>(`/api/v1/models${query ? `?${query}` : ""}`);
}

export async function listModelPage(
  params?: ListModelPageParams,
): Promise<ModelPageRead> {
  const search = modelListSearch(params);
  if (params?.sort) search.set("sort", params.sort);
  if (params?.cursor) search.set("cursor", params.cursor);
  const query = search.toString();
  return getJson<ModelPageRead>(`/api/v1/models/page${query ? `?${query}` : ""}`);
}

export async function listOutlinerModels(
  params?: Omit<ListModelsParams, "collection" | "direct" | "q" | "offset">,
): Promise<OutlinerModelRead[]> {
  const search = modelListSearch(params);
  const query = search.toString();
  return getJson<OutlinerModelRead[]>(
    `/api/v1/models/outliner${query ? `?${query}` : ""}`,
  );
}

export async function getModelFacets(
  params?: Omit<ListModelsParams, "limit" | "offset">,
): Promise<ModelFacetsRead> {
  const search = new URLSearchParams();
  if (params?.collection) search.set("collection", params.collection);
  if (params?.direct) search.set("direct", "true");
  if (params?.q) search.set("q", params.q);
  if (params?.printer_id) search.set("printer_id", String(params.printer_id));
  if (params?.printer_presence) search.set("printer_presence", params.printer_presence);
  if (params?.favorites) search.set("favorites", "true");
  for (const tag of params?.tag ?? []) search.append("tag", tag);
  for (const key of ["file_type", "material_type", "slicer_name", "printer_model", "revision_status", "print_outcome", "storage"] as const) {
    for (const value of params?.[key] ?? []) search.append(key, String(value));
  }
  if (params?.printed !== undefined) search.set("printed", String(params.printed));
  if (params?.uploaded_after) search.set("uploaded_after", params.uploaded_after);
  if (params?.uploaded_before) search.set("uploaded_before", params.uploaded_before);
  const query = search.toString();
  return getJson<ModelFacetsRead>(`/api/v1/models/facets${query ? `?${query}` : ""}`);
}

export function starModel(id: number): Promise<ModelStarRead> {
  return sendJson<ModelStarRead>(`/api/v1/models/${id}/star`, "PUT", {});
}

export async function unstarModel(id: number): Promise<ModelStarRead> {
  const path = `/api/v1/models/${id}/star`;
  const res = await fetch(getUrl(path), { method: "DELETE", headers: authHeaders() });
  invalidateApiCache(path);
  return handleResponse<ModelStarRead>(res);
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

export async function downloadLibraryArchive(): Promise<void> {
  const res = await fetch(getUrl("/api/v1/models/library-archive"), { headers: authHeaders(), cache: "no-store" });
  await expectOk(res);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url; link.download = "printstash-library-v1.zip";
  document.body.appendChild(link); link.click(); link.remove(); URL.revokeObjectURL(url);
}

export function importLibraryArchive(file: File): Promise<{ created_models: number; created_files: number; skipped_files: number; imported_jobs: number }> {
  const form = new FormData(); form.append("file", file);
  return sendForm("/api/v1/models/library-import", form);
}

export function getModelPrinterFiles(id: number): Promise<ModelPrinterFileRead[]> {
  return getJson<ModelPrinterFileRead[]>(`/api/v1/models/${id}/printer-files`);
}

export function getModelPrintJobs(id: number): Promise<ModelPrintJobRead[]> {
  return getJson<ModelPrintJobRead[]>(`/api/v1/models/${id}/print-jobs`);
}

export function getArtifactOutcomes(modelId: number, fileIds: number[]): Promise<ArtifactOutcomeRead[]> {
  const search = new URLSearchParams();
  fileIds.forEach((id) => search.append("file_id", String(id)));
  return getJson<ArtifactOutcomeRead[]>(`/api/v1/models/${modelId}/artifact-outcomes?${search}`);
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

export function batchMoveModels(
  modelIds: number[],
  collection: string,
): Promise<ModelBatchResult> {
  return sendJson<ModelBatchResult>("/api/v1/models/batch/move", "POST", {
    model_ids: modelIds,
    collection,
  });
}

export function batchTagModels(
  modelIds: number[],
  add: string[],
  remove: string[],
): Promise<ModelBatchResult> {
  return sendJson<ModelBatchResult>("/api/v1/models/batch/tags", "POST", {
    model_ids: modelIds,
    add,
    remove,
  });
}

export function batchSetRevisionLabels(
  fileIds: number[],
  revisionLabel: string | null,
): Promise<RevisionBatchResult> {
  return sendJson<RevisionBatchResult>(
    "/api/v1/models/batch/revision-labels",
    "PATCH",
    { file_ids: fileIds, revision_label: revisionLabel },
  );
}

export function batchDeleteModels(
  modelIds: number[],
): Promise<ModelBatchResult> {
  return sendJson<ModelBatchResult>("/api/v1/models/batch/delete", "POST", {
    model_ids: modelIds,
  });
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

export function listIngestJobs(): Promise<IngestJobStatus[]> {
  return getJson<IngestJobStatus[]>("/api/v1/ingest/jobs", { fresh: true });
}

export function ingestUrl(payload: {
  url: string;
  collection?: string;
  tags?: string;
  review?: boolean;
}): Promise<IngestResponse> {
  return sendJson<IngestResponse>("/api/v1/ingest/url", "POST", payload);
}

export function selectModelFiles(
  filesToken: string,
  payload: { file_ids: string[]; collection?: string; tags?: string },
): Promise<IngestResponse> {
  return sendJson<IngestResponse>(
    `/api/v1/ingest/url/files/${filesToken}/select`,
    "POST",
    payload,
  );
}

export function selectCollectionMembers(
  collectionToken: string,
  payload: { member_ids: string[]; collection?: string; tags?: string },
): Promise<IngestResponse> {
  return sendJson<IngestResponse>(
    `/api/v1/ingest/collection/${collectionToken}/select`,
    "POST",
    payload,
  );
}

export function ingestArchive(formData: FormData): Promise<ArchiveManifest> {
  return sendForm<ArchiveManifest>("/api/v1/ingest/archive", formData);
}

export function inspectArchive(formData: FormData): Promise<IngestResponse> {
  return sendForm<IngestResponse>("/api/v1/ingest/archive/inspect", formData);
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
