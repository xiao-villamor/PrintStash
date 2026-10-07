/** Exact owned backup sources and optional discovery catalogs have independent read failures. */
import { queryOptions } from "@tanstack/react-query";
import {
  type BackupMeta,
  listBackupSources,
  listUnownedLocalBackups,
  listUnownedS3Backups,
  listUnownedRemoteBackups,
} from "@/lib/api/backup";
import { parseApiError } from "@/lib/errors";

export const backupCatalogKeys = {
  all: ["backup-catalog"] as const,
  owned: ["backup-catalog", "owned"] as const,
  local: ["backup-catalog", "unowned-local"] as const,
  s3: ["backup-catalog", "unowned-s3"] as const,
  remote: ["backup-catalog", "unowned-remote"] as const,
};
/** Older servers lack discovery routes; a failed supported route must remain an error. */
async function optionalDiscovery<T>(read: Promise<T[]>): Promise<T[]> {
  try {
    return await read;
  } catch (error) {
    if (parseApiError(error).status === 404) return [];
    throw error;
  }
}
export function backupSourcesOptions() {
  return queryOptions({
    queryKey: backupCatalogKeys.owned,
    queryFn: ({ signal }) => listBackupSources({ fresh: true, signal }),
    retry: false,
    staleTime: 0,
  });
}
export function unownedLocalBackupsOptions() {
  return queryOptions({
    queryKey: backupCatalogKeys.local,
    queryFn: ({ signal }) => optionalDiscovery(listUnownedLocalBackups({ fresh: true, signal })),
    retry: false,
    staleTime: 0,
  });
}
export function unownedS3BackupsOptions() {
  return queryOptions({
    queryKey: backupCatalogKeys.s3,
    queryFn: ({ signal }) => optionalDiscovery(listUnownedS3Backups({ fresh: true, signal })),
    retry: false,
    staleTime: 0,
  });
}
export function unownedRemoteBackupsOptions() {
  return queryOptions({
    queryKey: backupCatalogKeys.remote,
    queryFn: ({ signal }) => optionalDiscovery(listUnownedRemoteBackups({ fresh: true, signal })),
    retry: false,
    staleTime: 0,
  });
}

/** Legacy id-only rows stay distinct; destructive operations still require source_ref. */
export function backupSourceKey(backup: BackupMeta): string {
  return (
    backup.source_ref ??
    `${backup.location}:${backup.namespace ?? ""}:${backup.key ?? ""}:${backup.backup_id}`
  );
}
