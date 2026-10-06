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
  return getJson<VaultConfigRead>("/api/v1/config", { ...options, fresh: true });
}

export function getHealthDetails<T>(): Promise<T> {
  return getJson<T>("/api/v1/health/details", { fresh: true });
}

export function enrollStorageRoot(role: StorageRootRole): Promise<StorageRootEnrollmentRead> {
  return sendJson<StorageRootEnrollmentRead>("/api/v1/config/storage-roots/enroll", "POST", {
    role,
    confirm: true,
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

export function getLatestRelease(refresh = false): Promise<ReleaseStatus> {
  const query = refresh ? "?refresh=true" : "";
  return getJson<ReleaseStatus>(`/api/v1/health/releases/latest${query}`, { fresh: true });
}

export function updateVaultConfig(
  body: VaultConfigUpdate,
  options: Pick<GetJsonOptions, "signal"> = {},
): Promise<VaultConfigRead> {
  return requestApi<VaultConfigRead>("/api/v1/config", {
    method: "PUT",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
    signal: options.signal,
  });
}
