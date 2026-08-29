import { authHeaders, expectOk, getJson, getUrl, sendJson } from "@/lib/api/request";

export interface BackupMeta {
  backup_id: string;
  created_at: string;
  size_bytes: number;
  file_count: number;
  storage_backend: string;
  app_version: string;
  location: string;
}

/** A validated local archive not yet registered in the ownership ledger. */
export interface UnownedBackupCandidate extends BackupMeta {
  filename: string;
}

export interface BackupRestoreResult {
  backup_id: string;
  restored_files: number;
}

export function createBackup(): Promise<BackupMeta> {
  return sendJson<BackupMeta>("/api/v1/backups", "POST", undefined);
}

export function listBackups(): Promise<BackupMeta[]> {
  return getJson<BackupMeta[]>("/api/v1/backups");
}

export function listUnownedLocalBackups(): Promise<UnownedBackupCandidate[]> {
  return getJson<UnownedBackupCandidate[]>("/api/v1/backups/unowned-local");
}

export function adoptLocalBackup(filename: string): Promise<BackupMeta> {
  return sendJson<BackupMeta>(
    `/api/v1/backups/adopt-local?filename=${encodeURIComponent(filename)}`,
    "POST",
    undefined,
  );
}

export function restoreBackup(backupId: string): Promise<BackupRestoreResult> {
  return sendJson<BackupRestoreResult>(
    `/api/v1/backups/${encodeURIComponent(backupId)}/restore`,
    "POST",
    {},
  );
}

export async function downloadBackup(backupId: string): Promise<void> {
  const res = await fetch(getUrl(`/api/v1/backups/${encodeURIComponent(backupId)}/download`), {
    headers: authHeaders(),
    cache: "no-store",
  });
  await expectOk(res);
  const blob = await res.blob();
  const disposition = res.headers.get("content-disposition") ?? "";
  const filename =
    disposition.match(/filename="([^"]+)"/)?.[1] ?? `printstash-backup-${backupId}.tar.gz`;
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
