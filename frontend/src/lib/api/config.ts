import { requireEditingBase, requireEditingReceipt } from "@/lib/api/editing";
import type { EditingBase } from "@/types/editing";
import { getJson, sendJson, requestApi, jsonHeaders, type GetJsonOptions } from "@/lib/api/request";
import {
  SetupRequest,
  SetupResponse,
  SetupStorageRequest,
  SetupStorageCheck,
  SetupStatus,
  VaultConfigRead,
  VaultConfigUpdate,
  StorageProvider,
  StorageRootEnrollmentRead,
  StorageRootRole,
} from "@/types";

export function getSetupStatus(options?: GetJsonOptions): Promise<SetupStatus> {
  return getJson<SetupStatus>("/api/v1/setup/status", options);
}

export function getStorageProviders(options?: GetJsonOptions): Promise<StorageProvider[]> {
  return getJson<StorageProvider[]>("/api/v1/storage/providers", options);
}

export function beginSetup(options?: {
  signal?: AbortSignal;
}): Promise<{ csrf: string; expires_in: number }> {
  return requestApi("/api/v1/setup/session", {
    method: "POST",
    headers: jsonHeaders(),
    body: "{}",
    signal: options?.signal,
  });
}

export function checkSetupStorage(
  body: SetupStorageRequest,
  csrf: string,
  options?: { signal?: AbortSignal },
): Promise<SetupStorageCheck> {
  return requestApi("/api/v1/setup/check-storage", {
    method: "POST",
    headers: { ...jsonHeaders(), "X-PrintStash-Setup-CSRF": csrf },
    body: JSON.stringify(body),
    signal: options?.signal,
  });
}

/**
 * Finish pending storage. With a body, choose it: the owner provisioned from
 * VAULT_SETUP_ADMIN_* signs in before any storage exists.
 */
export function prepareSetupStorage(body: SetupStorageRequest = {}): Promise<SetupStorageCheck> {
  return sendJson("/api/v1/setup/prepare-storage", "POST", body);
}

export function completeSetup(
  body: SetupRequest,
  csrf: string,
  options?: { signal?: AbortSignal },
): Promise<SetupResponse> {
  return requestApi<SetupResponse>("/api/v1/setup", {
    method: "POST",
    headers: { ...jsonHeaders(), "X-PrintStash-Setup-CSRF": csrf },
    body: JSON.stringify(body),
    signal: options?.signal,
  });
}

export function getVaultConfig(options: GetJsonOptions = {}): Promise<VaultConfigRead> {
  return getJson<VaultConfigRead>("/api/v1/config", { ...options });
}

export function getHealthDetails<T>(options: GetJsonOptions = {}): Promise<T> {
  return getJson<T>("/api/v1/health/details", { ...options });
}

export function enrollStorageRoot(
  role: StorageRootRole,
  expectedPath: string,
  options: Pick<GetJsonOptions, "signal"> = {},
): Promise<StorageRootEnrollmentRead> {
  return requestApi<StorageRootEnrollmentRead>("/api/v1/config/storage-roots/enroll", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify({ role, confirm: true, expected_path: expectedPath }),
    signal: options.signal,
  });
}

export interface ReleaseStatus {
  status: "update_available" | "up_to_date" | "unavailable";
  current_version: string;
  latest_version: string | null;
  update_available: boolean;
  release_url: string | null;
  published_at: string | null;
  checked_at: string;
}

export function getLatestRelease(
  refresh = false,
  options: GetJsonOptions = {},
): Promise<ReleaseStatus> {
  const query = refresh ? "?refresh=true" : "";
  return getJson<ReleaseStatus>(`/api/v1/health/releases/latest${query}`, {
    ...options,
  });
}

export async function updateVaultConfig(
  body: VaultConfigUpdate,
  options: Pick<GetJsonOptions, "signal"> & { base: EditingBase },
): Promise<VaultConfigRead> {
  const base = options.base;
  requireEditingBase(base);
  const headers = jsonHeaders();
  headers["If-Match"] = `"vault-config-e${base.edit_epoch}-v${base.edit_version}"`;
  headers["X-PrintStash-Edit-Contract"] = "conditional-v1";
  const row = await requestApi<VaultConfigRead>("/api/v1/config", {
    method: "PUT",
    headers,
    body: JSON.stringify(body),
    signal: options.signal,
  });
  requireEditingReceipt(row, base);
  return row;
}
