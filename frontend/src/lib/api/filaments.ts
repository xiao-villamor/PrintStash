import { editHeaders, requireEditingReceipt } from "./editing";
import type { EditingBase } from "@/types/editing";
import {
  getJson,
  GetJsonOptions,
  sendAction,
  sendJson,
  requestApi,
  jsonHeaders,
} from "@/lib/api/request";
import { FilamentProfileCreate, FilamentProfileRead, FilamentProfileUpdate } from "@/types";

export function listFilamentProfiles(options?: GetJsonOptions): Promise<FilamentProfileRead[]> {
  return getJson<FilamentProfileRead[]>("/api/v1/filament-profiles", options);
}

export function createFilamentProfile(
  payload: FilamentProfileCreate,
): Promise<FilamentProfileRead> {
  return sendJson<FilamentProfileRead>("/api/v1/filament-profiles", "POST", payload);
}

export async function updateFilamentProfile(
  id: number,
  payload: FilamentProfileUpdate,
  options: { base: EditingBase; signal?: AbortSignal },
): Promise<FilamentProfileRead> {
  const saved = await requestApi<FilamentProfileRead>(`/api/v1/filament-profiles/${id}`, {
    method: "PATCH",
    headers: { ...jsonHeaders(), ...editHeaders("filament-profile", id, options.base) },
    body: JSON.stringify(payload),
    signal: options.signal,
  });
  requireEditingReceipt(saved, options.base);
  if (saved.id !== id) throw new Error("profile_identity_mismatch");
  return saved;
}

export function deleteFilamentProfile(id: number): Promise<void> {
  return sendAction(`/api/v1/filament-profiles/${id}`, "DELETE");
}
