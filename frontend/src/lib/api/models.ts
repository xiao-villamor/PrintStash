import type { EditingBase } from "@/types/editing";
import { editHeaders, requireEditingReceipt } from "./editing";
import {
  expectOk,
  getJson,
  jsonHeaders,
  GetJsonOptions,
  requestApi,
  requestMutation,
  sendAction,
  sendForm,
  sendFormWithProgress,
  sendJson,
} from "@/lib/api/request";
import {
  ArtifactOutcomeRead,
  FileRevisionUpdate,
  ImportedPrintJobRead,
  JobAccepted,
  ListModelPageParams,
  ListModelsParams,
  ManualPrintJobCreate,
  ModelBatchResult,
  ModelEditBatchResult,
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
  normalizeTrashPurgeRead,
  TrashedModelRead,
  VaultStatsRead,
} from "@/types";

export function modelListSearch(params?: ListModelsParams): URLSearchParams {
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
  for (const key of [
    "file_type",
    "material_type",
    "slicer_name",
    "printer_model",
    "revision_status",
    "print_outcome",
    "storage",
  ] as const) {
    for (const value of params?.[key] ?? []) search.append(key, String(value));
  }
  if (params?.has_similar_candidates !== undefined)
    search.set("has_similar_candidates", String(params.has_similar_candidates));
  if (params?.printed !== undefined) search.set("printed", String(params.printed));
  if (params?.uploaded_after) search.set("uploaded_after", params.uploaded_after);
  if (params?.uploaded_before) search.set("uploaded_before", params.uploaded_before);

  for (const key of [
    "printed_after",
    "printed_before",
    "print_duration_min_s",
    "print_duration_max_s",
  ] as const) {
    if (params?.[key] != null) search.set(key, String(params[key]));
  }
  return search;
}

export async function listModels(
  params?: ListModelsParams,
  options?: GetJsonOptions,
): Promise<ModelListItem[]> {
  const query = modelListSearch(params).toString();
  return getJson<ModelListItem[]>(`/api/v1/models${query ? `?${query}` : ""}`, options);
}

export async function listModelPage(params?: ListModelPageParams): Promise<ModelPageRead> {
  const search = modelListSearch(params);
  if (params?.sort) search.set("sort", params.sort);
  if (params?.cursor) search.set("cursor", params.cursor);
  const query = search.toString();
  return getJson<ModelPageRead>(`/api/v1/models/page${query ? `?${query}` : ""}`, {
    fresh: true,
  });
}

export async function listOutlinerModels(
  params?: Omit<ListModelsParams, "collection" | "direct" | "q" | "offset">,
): Promise<OutlinerModelRead[]> {
  const search = modelListSearch(params);
  const query = search.toString();
  return getJson<OutlinerModelRead[]>(`/api/v1/models/outliner${query ? `?${query}` : ""}`, {
    fresh: true,
  });
}

export async function getModelFacets(
  params?: Omit<ListModelsParams, "limit" | "offset">,
): Promise<ModelFacetsRead> {
  const search = modelListSearch(params);
  const query = search.toString();
  return getJson<ModelFacetsRead>(`/api/v1/models/facets${query ? `?${query}` : ""}`, {
    fresh: true,
  });
}

export function starModel(id: number): Promise<ModelStarRead> {
  return sendJson<ModelStarRead>(`/api/v1/models/${id}/star`, "PUT", {});
}

export async function unstarModel(id: number): Promise<ModelStarRead> {
  const path = `/api/v1/models/${id}/star`;
  return requestMutation<ModelStarRead>(path, { method: "DELETE" });
}

export function getModel(id: number, options?: GetJsonOptions): Promise<ModelRead> {
  return getJson<ModelRead>(`/api/v1/models/${id}`, options);
}

export function getVaultStats(options?: GetJsonOptions): Promise<VaultStatsRead> {
  return getJson<VaultStatsRead>("/api/v1/models/stats", options);
}

export async function downloadModelExport(format: "json" | "csv"): Promise<void> {
  return requestApi(
    `/api/v1/models/export?format=${format}`,
    { cache: "no-store" },
    async (res, session) => {
      await expectOk(res, session);
      const blob = await res.blob();
      session.assertCurrent();
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
    },
  );
}

export async function downloadLibraryArchive(version: 1 | 2 = 2): Promise<void> {
  return requestApi(
    `/api/v1/models/library-archive?version=${version}`,
    { cache: "no-store" },
    async (res, session) => {
      await expectOk(res, session);
      const blob = await res.blob();
      session.assertCurrent();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `printstash-library-v${version}.zip`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    },
  );
}

export function importLibraryArchive(file: File): Promise<JobAccepted> {
  const form = new FormData();
  form.append("file", file);
  return sendForm("/api/v1/models/library-import", form);
}

export function getModelPrinterFiles(
  id: number,
  options?: GetJsonOptions,
): Promise<ModelPrinterFileRead[]> {
  return getJson<ModelPrinterFileRead[]>(`/api/v1/models/${id}/printer-files`, options);
}

export function getModelPrintJobs(
  id: number,
  options?: GetJsonOptions,
): Promise<ModelPrintJobRead[]> {
  return getJson<ModelPrintJobRead[]>(`/api/v1/models/${id}/print-jobs`, options);
}

export function getArtifactOutcomes(
  modelId: number,
  fileIds: number[],
): Promise<ArtifactOutcomeRead[]> {
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

/** Every editor sends the version of the snapshot its intent was based on. */
export function updateModel(
  id: number,
  payload: ModelUpdate,
  base: EditingBase,
): Promise<ModelRead> {
  return sendJson<ModelRead>(
    `/api/v1/models/${id}`,
    "PATCH",
    payload,
    editHeaders("model", id, base),
  ).then((saved) => {
    if (saved.id !== id) throw new Error("Invalid Model acknowledgement");
    requireEditingReceipt(saved, base);
    return saved;
  });
}

export function deleteModel(id: number): Promise<void> {
  return sendAction(`/api/v1/models/${id}`, "DELETE");
}

export function batchMoveModels(
  modelIds: number[],
  collection: string,
  expectedVersions: Record<number, EditingBase>,
): Promise<ModelEditBatchResult> {
  return sendJson<ModelEditBatchResult>(
    "/api/v1/models/batch/move",
    "POST",
    {
      model_ids: modelIds,
      collection,
      expected_versions: expectedVersions,
    },
    { "X-PrintStash-Edit-Contract": "conditional-v1" },
  );
}

export function batchTagModels(
  modelIds: number[],
  add: string[],
  remove: string[],
  expectedVersions: Record<number, EditingBase>,
): Promise<ModelEditBatchResult> {
  return sendJson<ModelEditBatchResult>(
    "/api/v1/models/batch/tags",
    "POST",
    {
      model_ids: modelIds,
      add,
      remove,
      expected_versions: expectedVersions,
    },
    { "X-PrintStash-Edit-Contract": "conditional-v1" },
  );
}

export function batchSetRevisionLabels(
  fileIds: number[],
  revisionLabel: string | null,
): Promise<RevisionBatchResult> {
  return sendJson<RevisionBatchResult>("/api/v1/models/batch/revision-labels", "PATCH", {
    file_ids: fileIds,
    revision_label: revisionLabel,
  });
}

export function batchDeleteModels(modelIds: number[]): Promise<ModelBatchResult> {
  return sendJson<ModelBatchResult>("/api/v1/models/batch/delete", "POST", {
    model_ids: modelIds,
  });
}

export function listTrash(options: GetJsonOptions = {}): Promise<TrashedModelRead[]> {
  return getJson<TrashedModelRead[]>("/api/v1/models/trash", options);
}

export function restoreModel(
  id: number,
  options: Pick<GetJsonOptions, "signal"> = {},
): Promise<ModelRead> {
  return requestMutation<ModelRead>(`/api/v1/models/${id}/restore`, {
    method: "POST",
    headers: jsonHeaders(),
    body: "{}",
    signal: options.signal,
  });
}

export async function purgeModel(
  id: number,
  confirmStorageRisk = false,
  options: Pick<GetJsonOptions, "signal"> = {},
): Promise<TrashPurgeRead> {
  const query = confirmStorageRisk ? "?confirm_storage_risk=true" : "";
  return normalizeTrashPurgeRead(
    await requestMutation<Partial<TrashPurgeRead>>(
      `/api/v1/models/${id}/purge${query}`,
      { method: "DELETE", signal: options.signal },
      `/api/v1/models/${id}/purge`,
    ),
  );
}

export async function purgeExpiredTrash(confirmStorageRisk = false): Promise<TrashPurgeRead> {
  const query = confirmStorageRisk ? "?confirm_storage_risk=true" : "";
  return normalizeTrashPurgeRead(
    await requestMutation<Partial<TrashPurgeRead>>(
      `/api/v1/models/trash/expired${query}`,
      { method: "DELETE" },
      "/api/v1/models/trash/expired",
    ),
  );
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

export function replaceFileTags(
  modelId: number,
  fileId: number,
  tags: string[],
): Promise<ModelRead> {
  return sendJson<ModelRead>(`/api/v1/models/${modelId}/files/${fileId}/tags`, "PUT", { tags });
}

export async function trashSourceFile(modelId: number, fileId: number): Promise<ModelRead> {
  const path = `/api/v1/models/${modelId}/files/${fileId}`;
  return requestMutation<ModelRead>(path, { method: "DELETE" });
}

export function restoreSourceFile(modelId: number, fileId: number): Promise<ModelRead> {
  return sendJson<ModelRead>(`/api/v1/models/${modelId}/files/${fileId}/restore`, "POST", {});
}

export async function deleteFileRevision(modelId: number, fileId: number): Promise<ModelRead> {
  const path = `/api/v1/models/${modelId}/files/${fileId}/revision`;
  return requestMutation<ModelRead>(path, {
    method: "DELETE",
  });
}

export function addGcodeRevision(modelId: number, formData: FormData): Promise<ModelRead> {
  return sendForm<ModelRead>(`/api/v1/models/${modelId}/gcode-revisions`, formData);
}

export function ingestOrca(formData: FormData): Promise<JobAccepted> {
  return sendForm<JobAccepted>("/api/v1/ingest/orca", formData);
}

export function ingestModel(formData: FormData): Promise<JobAccepted> {
  return sendForm<JobAccepted>("/api/v1/ingest/model", formData);
}

export function ingestUrl(payload: {
  url: string;
  collection?: string;
  tags?: string;
  review?: boolean;
}): Promise<JobAccepted> {
  return sendJson<JobAccepted>("/api/v1/ingest/url", "POST", payload);
}

export function selectModelFiles(
  filesToken: string,
  payload: { file_ids: string[]; collection?: string; tags?: string },
): Promise<JobAccepted> {
  return sendJson<JobAccepted>(`/api/v1/ingest/url/files/${filesToken}/select`, "POST", payload);
}

export function selectCollectionMembers(
  collectionToken: string,
  payload: { member_ids: string[]; collection?: string; tags?: string },
): Promise<JobAccepted> {
  return sendJson<JobAccepted>(
    `/api/v1/ingest/collection/${collectionToken}/select`,
    "POST",
    payload,
  );
}

export function inspectArchive(
  formData: FormData,
  signal?: AbortSignal,
  onProgress?: (loaded: number, total: number) => void,
): Promise<JobAccepted> {
  const path = "/api/v1/ingest/archive/inspect";
  return signal && onProgress
    ? sendFormWithProgress<JobAccepted>(path, formData, signal, onProgress)
    : sendForm<JobAccepted>(path, formData, signal);
}

export function selectArchiveEntries(
  archiveId: string,
  payload: { names: string[]; collection?: string; tags?: string },
): Promise<JobAccepted> {
  return sendJson<JobAccepted>(`/api/v1/ingest/archive/${archiveId}/select`, "POST", payload);
}
