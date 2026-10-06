import { getJson, requestApi, jsonHeaders, type GetJsonOptions } from "@/lib/api/request";
import type {
  LibrarySourceKind,
  StorageConnection,
  StorageConnectionConfiguration,
  StorageConnectionPurpose,
} from "@/types";

export interface StorageConnectionCreate {
  name: string;
  kind: Exclude<LibrarySourceKind, "mounted">;
  purpose?: StorageConnectionPurpose;
  configuration: StorageConnectionConfiguration;
  secrets: Record<string, string>;
}

export function listStorageConnections(options: GetJsonOptions = {}): Promise<StorageConnection[]> {
  return getJson<StorageConnection[]>("/api/v1/storage-connections", { ...options, fresh: true });
}

export function createStorageConnection(
  body: StorageConnectionCreate,
  options: Pick<GetJsonOptions, "signal"> = {},
): Promise<StorageConnection> {
  return requestApi<StorageConnection>("/api/v1/storage-connections", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
    signal: options.signal,
  });
}

export function probeStorageConnection(
  id: number,
  options: Pick<GetJsonOptions, "signal"> = {},
): Promise<{ ok: boolean }> {
  return requestApi<{ ok: boolean }>(`/api/v1/storage-connections/${id}/probe`, {
    method: "POST",
    headers: jsonHeaders(),
    body: "{}",
    signal: options.signal,
  });
}

export interface StorageConnectionUpdate {
  name?: string;
  configuration?: StorageConnectionConfiguration;
  secrets?: Record<string, string>;
  enabled?: boolean;
  purpose?: StorageConnectionPurpose;
  manual_backup_enabled?: boolean;
  automatic_backup_enabled?: boolean;
}
export function updateStorageConnection(
  id: number,
  body: StorageConnectionUpdate,
  options: Pick<GetJsonOptions, "signal"> = {},
): Promise<StorageConnection> {
  return requestApi<StorageConnection>(`/api/v1/storage-connections/${id}`, {
    method: "PATCH",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
    signal: options.signal,
  });
}

export function deleteStorageConnection(
  id: number,
  options: Pick<GetJsonOptions, "signal"> = {},
): Promise<void> {
  return requestApi<void>(`/api/v1/storage-connections/${id}`, {
    method: "DELETE",
    signal: options.signal,
  });
}
