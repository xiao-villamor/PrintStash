import type { EditingBase } from "@/types/editing";
import { editHeaders, requireEditingReceipt } from "./editing";
import {
  type GetJsonOptions,
  getJson,
  requestApi,
  jsonHeaders,
  expectOk,
  sendJson,
} from "@/lib/api/request";
import {
  ExternalLibrary,
  ExternalLibraryCreate,
  ExternalLibraryRootEnrollment,
  ExternalLibraryUpdate,
} from "@/types";
import { JobAccepted } from "@/types/models";

export function listExternalLibraries(options?: GetJsonOptions): Promise<ExternalLibrary[]> {
  return getJson<ExternalLibrary[]>("/api/v1/libraries", { ...options });
}

export function createExternalLibrary(
  body: ExternalLibraryCreate,
  options: Pick<GetJsonOptions, "signal"> = {},
): Promise<ExternalLibrary> {
  return requestApi<ExternalLibrary>("/api/v1/libraries", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
    signal: options.signal,
  });
}

export async function updateExternalLibrary(
  id: number,
  body: ExternalLibraryUpdate,
  options: { base: EditingBase; signal?: AbortSignal },
): Promise<ExternalLibrary> {
  const saved = await requestApi<ExternalLibrary>(`/api/v1/libraries/${id}`, {
    method: "PATCH",
    headers: { ...jsonHeaders(), ...editHeaders("library-source", id, options.base) },
    body: JSON.stringify(body),
    signal: options.signal,
  });
  requireEditingReceipt(saved, options.base);
  if (saved.id !== id) throw new Error("Invalid source editing acknowledgement");
  return saved;
}

export function enrollExternalLibraryRoot(
  id: number,
  body: ExternalLibraryRootEnrollment,
  options: Pick<GetJsonOptions, "signal"> = {},
): Promise<ExternalLibrary> {
  return requestApi<ExternalLibrary>(`/api/v1/libraries/${id}/root/enroll`, {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
    signal: options.signal,
  });
}

export function deleteExternalLibrary(
  id: number,
  options: Pick<GetJsonOptions, "signal"> = {},
): Promise<void> {
  return requestApi(
    `/api/v1/libraries/${id}`,
    { method: "DELETE", signal: options.signal },
    expectOk,
  );
}

export function scanExternalLibrary(
  id: number,
  options: Pick<GetJsonOptions, "signal"> = {},
): Promise<JobAccepted> {
  return requestApi<JobAccepted>(`/api/v1/libraries/${id}/scan`, {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify({}),
    signal: options.signal,
  });
}

export function scanExternalLibraryPath(id: number, path: string): Promise<JobAccepted> {
  return sendJson<JobAccepted>(`/api/v1/libraries/${id}/scan-path`, "POST", { path });
}

export function discoverLibraryLocations(options?: GetJsonOptions): Promise<string[]> {
  return getJson<string[]>("/api/v1/libraries/locations", options);
}
