import { getJson, sendAction, sendJson } from "@/lib/api/request";
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

export function listStorageConnections(): Promise<StorageConnection[]> {
  return getJson<StorageConnection[]>("/api/v1/storage-connections", { fresh: true });
}

export function createStorageConnection(body: StorageConnectionCreate): Promise<StorageConnection> {
  return sendJson<StorageConnection>("/api/v1/storage-connections", "POST", body);
}

export function probeStorageConnection(id: number): Promise<{ ok: boolean }> {
  return sendJson<{ ok: boolean }>(`/api/v1/storage-connections/${id}/probe`, "POST", {});
}

export function updateStorageConnection(
  id: number,
  body: { enabled?: boolean; purpose?: StorageConnectionPurpose },
): Promise<StorageConnection> {
  return sendJson<StorageConnection>(`/api/v1/storage-connections/${id}`, "PATCH", body);
}

export function deleteStorageConnection(id: number): Promise<void> {
  return sendAction(`/api/v1/storage-connections/${id}`, "DELETE");
}
