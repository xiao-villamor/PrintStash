import {
  getJson,
  GetJsonOptions,
  sendAction,
  sendJson,
} from "@/lib/api/request";
import {
  FilamentProfileCreate,
  FilamentProfileRead,
  FilamentProfileUpdate,
} from "@/types";

export function listFilamentProfiles(
  options?: GetJsonOptions,
): Promise<FilamentProfileRead[]> {
  return getJson<FilamentProfileRead[]>("/api/v1/filament-profiles", options);
}

export function createFilamentProfile(
  payload: FilamentProfileCreate,
): Promise<FilamentProfileRead> {
  return sendJson<FilamentProfileRead>(
    "/api/v1/filament-profiles",
    "POST",
    payload,
  );
}

export function updateFilamentProfile(
  id: number,
  payload: FilamentProfileUpdate,
): Promise<FilamentProfileRead> {
  return sendJson<FilamentProfileRead>(
    `/api/v1/filament-profiles/${id}`,
    "PATCH",
    payload,
  );
}

export function deleteFilamentProfile(id: number): Promise<void> {
  return sendAction(`/api/v1/filament-profiles/${id}`, "DELETE");
}
