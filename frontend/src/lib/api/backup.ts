import { getJson, sendJson } from "@/lib/api/request";

export interface BackupMeta {
  backup_id: string;
  created_at: string;
  size_bytes: number;
  file_count: number;
  storage_backend: string;
  app_version: string;
  location: string;
}

export function createBackup(): Promise<BackupMeta> {
  return sendJson<BackupMeta>("/api/v1/backups", "POST", undefined);
}

export function listBackups(): Promise<BackupMeta[]> {
  return getJson<BackupMeta[]>("/api/v1/backups");
}
