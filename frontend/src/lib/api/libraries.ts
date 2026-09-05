import { getJson, sendAction, sendJson } from "@/lib/api/request";
import {
  ExternalLibrary,
  ExternalLibraryCreate,
  ExternalLibraryRootEnrollment,
  ExternalLibraryUpdate,
} from "@/types";
import { IngestResponse } from "@/types/models";

export function listExternalLibraries(): Promise<ExternalLibrary[]> {
  return getJson<ExternalLibrary[]>("/api/v1/libraries", { fresh: true });
}

export function createExternalLibrary(body: ExternalLibraryCreate): Promise<ExternalLibrary> {
  return sendJson<ExternalLibrary>("/api/v1/libraries", "POST", body);
}

export function updateExternalLibrary(
  id: number,
  body: ExternalLibraryUpdate,
): Promise<ExternalLibrary> {
  return sendJson<ExternalLibrary>(`/api/v1/libraries/${id}`, "PATCH", body);
}

export function enrollExternalLibraryRoot(
  id: number,
  body: ExternalLibraryRootEnrollment,
): Promise<ExternalLibrary> {
  return sendJson<ExternalLibrary>(`/api/v1/libraries/${id}/root/enroll`, "POST", body);
}

export function deleteExternalLibrary(id: number): Promise<void> {
  return sendAction(`/api/v1/libraries/${id}`, "DELETE");
}

export function scanExternalLibrary(id: number): Promise<IngestResponse> {
  return sendJson<IngestResponse>(`/api/v1/libraries/${id}/scan`, "POST", {});
}

export function scanExternalLibraryPath(id: number, path: string): Promise<IngestResponse> {
  return sendJson<IngestResponse>(`/api/v1/libraries/${id}/scan-path`, "POST", { path });
}

export function discoverLibraryLocations(): Promise<string[]> {
  return getJson<string[]>("/api/v1/libraries/locations", { fresh: true });
}
