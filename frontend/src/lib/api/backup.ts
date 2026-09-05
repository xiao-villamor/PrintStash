import {
  authHeaders,
  expectOk,
  getJson,
  getUrl,
  sendAction,
  sendForm,
  sendJson,
} from "@/lib/api/request";
import type { StorageOperations } from "@/types";

export interface BackupMeta {
  operations?: StorageOperations;
  backup_id: string;
  created_at: string;
  size_bytes: number;
  file_count: number;
  storage_backend: string;
  app_version: string;
  location: string;
  /** Opaque locator for one exact source. Never use backup_id as a locator. */
  source_ref?: string | null;
  /** Opaque, credential-free provider identity safe to show to an administrator. */
  provider_ref?: string | null;
  namespace?: string | null;
  /** Exact object key (or local archive path) returned by the API. */
  key?: string | null;
  /** Configured object prefix for remote sources. */
  prefix?: string | null;
  /** Why a candidate is present, when the API supplies one. */
  candidate_kind?: string | null;
  canonical?: boolean;
  precedence?: number;
  archive_sha256?: string | null;
}

/** A validated local archive not yet registered in the ownership ledger. */
export interface UnownedBackupCandidate extends BackupMeta {
  filename: string;
}

/** A validated remote archive awaiting explicit administrator adoption. */
export interface UnownedS3BackupCandidate extends BackupMeta {
  key: string;
  prefix: string;
  provider_ref?: string | null;
  candidate_kind?: string | null;
}

/** A validated archive found through a configured OpenDAL connection. */
export interface UnownedRemoteBackupCandidate extends BackupMeta {
  connection_id: number;
  connection_name: string;
  provider: string;
  key: string;
  prefix: string;
}

export interface BackupRestoreResult {
  backup_id: string;
  restored_files: number;
}

export function createBackup(): Promise<BackupMeta> {
  return sendJson<BackupMeta>("/api/v1/backups", "POST", undefined);
}

export function uploadBackup(file: File): Promise<BackupMeta> {
  const body = new FormData();
  body.append("file", file);
  return sendForm<BackupMeta>("/api/v1/backups/upload", body);
}

export function listBackups(): Promise<BackupMeta[]> {
  return getJson<BackupMeta[]>("/api/v1/backups");
}

/** List every exact source, including replicas and ambiguous collisions. */
export function listBackupSources(): Promise<BackupMeta[]> {
  return getJson<BackupMeta[]>("/api/v1/backups/sources");
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

export function listUnownedS3Backups(): Promise<UnownedS3BackupCandidate[]> {
  return getJson<UnownedS3BackupCandidate[]>("/api/v1/backups/unowned-s3");
}

export function listUnownedRemoteBackups(): Promise<UnownedRemoteBackupCandidate[]> {
  return getJson<UnownedRemoteBackupCandidate[]>("/api/v1/backups/unowned-remote");
}

export function adoptS3Backup(
  key: string,
  sourceRef: string,
  expectedArchiveSha256: string,
): Promise<BackupMeta> {
  const params = new URLSearchParams({
    key,
    source_ref: sourceRef,
    expected_archive_sha256: expectedArchiveSha256,
  });
  return sendJson<BackupMeta>(`/api/v1/backups/adopt-s3?${params.toString()}`, "POST", undefined);
}

export function adoptRemoteBackup(
  connectionId: number,
  key: string,
  sourceRef: string,
  expectedArchiveSha256: string,
): Promise<BackupMeta> {
  const params = new URLSearchParams({
    connection_id: String(connectionId),
    key,
    source_ref: sourceRef,
    expected_archive_sha256: expectedArchiveSha256,
  });
  return sendJson<BackupMeta>(
    `/api/v1/backups/adopt-remote?${params.toString()}`,
    "POST",
    undefined,
  );
}

function sourceQuery(sourceRef?: string | null): string {
  return sourceRef ? `?source_ref=${encodeURIComponent(sourceRef)}` : "";
}

export function restoreBackup(
  backupId: string,
  sourceRef?: string | null,
): Promise<BackupRestoreResult> {
  return sendJson<BackupRestoreResult>(
    `/api/v1/backups/${encodeURIComponent(backupId)}/restore${sourceQuery(sourceRef)}`,
    "POST",
    {},
  );
}

export function deleteBackup(backupId: string, sourceRef?: string | null): Promise<void> {
  return sendAction(
    `/api/v1/backups/${encodeURIComponent(backupId)}${sourceQuery(sourceRef)}`,
    "DELETE",
  );
}

export async function downloadBackup(backupId: string, sourceRef?: string | null): Promise<void> {
  const res = await fetch(
    getUrl(`/api/v1/backups/${encodeURIComponent(backupId)}/download${sourceQuery(sourceRef)}`),
    {
      headers: authHeaders(),
      cache: "no-store",
    },
  );
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
