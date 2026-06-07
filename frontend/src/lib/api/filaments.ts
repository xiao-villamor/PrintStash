import { getJson, sendAction, sendJson } from "@/lib/api/request";
import {
  FilamentProfileCreate,
  FilamentProfileRead,
  FilamentProfileUpdate,
} from "@/types";

export function listFilamentProfiles(): Promise<FilamentProfileRead[]> {
  return getJson<FilamentProfileRead[]>("/api/v1/filament-profiles");
}

export function createFilamentProfile(
  payload: FilamentProfileCreate,
  apiKey?: string,
): Promise<FilamentProfileRead> {
  return sendJson<FilamentProfileRead>(
    "/api/v1/filament-profiles",
    "POST",
    payload,
    apiKey,
  );
}

export function updateFilamentProfile(
  id: number,
  payload: FilamentProfileUpdate,
  apiKey?: string,
): Promise<FilamentProfileRead> {
  return sendJson<FilamentProfileRead>(
    `/api/v1/filament-profiles/${id}`,
    "PATCH",
    payload,
    apiKey,
  );
}

export function deleteFilamentProfile(id: number, apiKey?: string): Promise<void> {
  return sendAction(`/api/v1/filament-profiles/${id}`, "DELETE", apiKey);
}
