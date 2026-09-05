import { getJson, sendJson } from "@/lib/api/request";
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
  IngestResponse,
} from "@/types";

export function getSetupStatus(): Promise<SetupStatus> {
  return getJson<SetupStatus>("/api/v1/setup/status", { fresh: true });
}

export function getStorageProviders(): Promise<StorageProvider[]> {
  return getJson<StorageProvider[]>("/api/v1/storage/providers");
}

export function beginSetup(): Promise<{ csrf: string; expires_in: number }> {
  return sendJson("/api/v1/setup/session", "POST", {});
}

export function checkSetupStorage(
  body: SetupStorageRequest,
  csrf: string,
): Promise<SetupStorageCheck> {
  return sendJson("/api/v1/setup/check-storage", "POST", body, { "X-PrintStash-Setup-CSRF": csrf });
}

export function prepareSetupStorage(): Promise<SetupStorageCheck> {
  return sendJson("/api/v1/setup/prepare-storage", "POST", {});
}

export function completeSetup(body: SetupRequest, csrf: string): Promise<SetupResponse> {
  return sendJson<SetupResponse>("/api/v1/setup", "POST", body, {
    "X-PrintStash-Setup-CSRF": csrf,
  });
}

export function getVaultConfig(): Promise<VaultConfigRead> {
  return getJson<VaultConfigRead>("/api/v1/config");
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

export function updateVaultConfig(body: VaultConfigUpdate): Promise<VaultConfigRead> {
  return sendJson<VaultConfigRead>("/api/v1/config", "PUT", body);
}

export function rebuildModelThumbnails(): Promise<IngestResponse> {
  return sendJson<IngestResponse>("/api/v1/files/thumbnails/rebuild?force=true", "POST", {});
}
