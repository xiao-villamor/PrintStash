/** Backup commands preserve exact source intent and publish receipts only into their live session. */
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useAuth } from "@/lib/auth-context";
import { onAuthChange } from "@/lib/auth-store";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import { ApiError, parseApiError } from "@/lib/errors";
import { waitForImportJob } from "@/lib/task-center";
import {
  createBackup,
  uploadBackup,
  deleteBackup,
  restoreBackup,
  downloadBackup,
  adoptLocalBackup,
  adoptS3Backup,
  adoptRemoteBackup,
  backupFromJob,
  type BackupMeta,
  type BackupRestoreResult,
  type UnownedBackupCandidate,
  type UnownedS3BackupCandidate,
  type UnownedRemoteBackupCandidate,
} from "@/lib/api/backup";
import { backupCatalogKeys, backupSourceKey } from "./settings-backup-catalog";
import { backupRunKeys } from "./settings-backup-runs";

export type BackupCommand = { session: number } & (
  | { kind: "create" }
  | { kind: "upload"; file: File }
  | { kind: "delete" | "restore" | "download"; target: BackupMeta }
  | { kind: "adopt-local"; target: UnownedBackupCandidate }
  | { kind: "adopt-s3"; target: UnownedS3BackupCandidate }
  | { kind: "adopt-remote"; target: UnownedRemoteBackupCandidate }
);
export type BackupReceipt =
  | { kind: "saved"; backup: BackupMeta }
  | { kind: "restored"; result: BackupRestoreResult }
  | { kind: "deleted" | "downloaded" | "adopted" };

export function useBackupCommand() {
  const client = useQueryClient();
  const { user } = useAuth();
  const admin = useRef(!!user?.is_superuser);
  useLayoutEffect(() => {
    admin.current = !!user?.is_superuser;
  }, [user?.is_superuser]);
  const [session] = useState(getSessionVersion);
  const live = useRef(true);
  const active = useRef<AbortController | null>(null);
  useEffect(() => {
    live.current = true;
    const release = onAuthChange(() => active.current?.abort());
    return () => {
      live.current = false;
      active.current?.abort();
      release();
    };
  }, []);
  async function run(command: BackupCommand): Promise<BackupReceipt> {
    requireSessionVersion(session);
    requireSessionVersion(command.session);
    if (!live.current) throw new DOMException("Backup view was disposed", "AbortError");
    if (active.current) throw new Error("A backup command is already pending");
    const controller = new AbortController();
    active.current = controller;
    function assertCurrent() {
      requireSessionVersion(session);
      requireSessionVersion(command.session);
      controller.signal.throwIfAborted();
      if (!live.current) throw new DOMException("Backup view was disposed", "AbortError");
      if (!admin.current) throw new ApiError(403, "forbidden", "forbidden");
    }
    function readRows<T extends BackupMeta>(key: readonly string[]): T[] {
      const state = client.getQueryState(key);
      if (state?.status === "error") throw parseApiError(state.error);
      const rows = client.getQueryData<T[]>(key);
      if (!rows) throw new ApiError(409, "backup_source_changed", "backup_source_changed");
      return rows;
    }
    function assertEligible() {
      if (!("target" in command)) return;
      const target = command.target;
      const key =
        command.kind === "adopt-local"
          ? backupCatalogKeys.local
          : command.kind === "adopt-s3"
            ? backupCatalogKeys.s3
            : command.kind === "adopt-remote"
              ? backupCatalogKeys.remote
              : backupCatalogKeys.owned;
      const current = readRows(key).find((row) => backupSourceKey(row) === backupSourceKey(target));
      if (
        !current ||
        current.archive_sha256 !== target.archive_sha256 ||
        current.key !== target.key ||
        current.namespace !== target.namespace ||
        current.provider_ref !== target.provider_ref
      )
        throw new ApiError(409, "backup_source_changed", "backup_source_changed");
      if (["delete", "restore", "download"].includes(command.kind) && !target.source_ref)
        throw new ApiError(409, "backup_source_unavailable", "backup_source_unavailable");
      if (command.kind === "delete" && current.operations?.physical_delete.allowed === false)
        throw new ApiError(409, "backup_source_changed", "backup_source_changed");
      if (
        (command.kind === "adopt-s3" || command.kind === "adopt-remote") &&
        (!target.source_ref || !target.archive_sha256)
      )
        throw new ApiError(409, "backup_adoption_unavailable", "backup_adoption_unavailable");
    }
    async function cancelReads() {
      assertCurrent();
      await client.cancelQueries({ queryKey: backupCatalogKeys.all });
      assertCurrent();
    }
    async function publish(backup: BackupMeta) {
      await cancelReads();
      client.setQueryData<BackupMeta[]>(backupCatalogKeys.owned, (previous) => {
        assertCurrent();
        return [
          backup,
          ...(previous ?? []).filter((row) => backupSourceKey(row) !== backupSourceKey(backup)),
        ];
      });
    }
    async function publishAdoption(backup: BackupMeta, key: readonly string[], target: BackupMeta) {
      await publish(backup);
      assertCurrent();
      client.setQueryData<BackupMeta[]>(key, (previous) =>
        previous?.filter((row) => backupSourceKey(row) !== backupSourceKey(target)),
      );
    }
    async function refreshDiscovery(key: readonly string[]) {
      assertCurrent();
      await client.invalidateQueries({ queryKey: key });
      assertCurrent();
    }
    try {
      assertCurrent();
      assertEligible();
      await cancelReads();
      assertEligible();
      const options = { signal: controller.signal };
      switch (command.kind) {
        case "create": {
          const accepted = await createBackup(options);
          // Accepted server work belongs to TaskCenter even if this form has gone away.
          requireSessionVersion(command.session);
          const job = await waitForImportJob(accepted.job_id, "Backup");
          assertCurrent();
          await client.invalidateQueries({ queryKey: backupRunKeys.all });
          assertCurrent();
          const backup = backupFromJob(job);
          if (!backup) throw new Error(job.error ?? "backup_failed");
          await publish(backup);
          return { kind: "saved", backup };
        }
        case "upload": {
          const backup = await uploadBackup(command.file, options);
          await publish(backup);
          return { kind: "saved", backup };
        }
        case "delete": {
          await deleteBackup(command.target.backup_id, command.target.source_ref, options);
          await cancelReads();
          client.setQueryData<BackupMeta[]>(backupCatalogKeys.owned, (previous) =>
            previous?.filter((row) => backupSourceKey(row) !== backupSourceKey(command.target)),
          );
          // Discovery lists retain independent provider identities; refresh rather than deleting matching basenames across providers.
          await Promise.all([
            refreshDiscovery(backupCatalogKeys.local),
            refreshDiscovery(backupCatalogKeys.s3),
            refreshDiscovery(backupCatalogKeys.remote),
          ]);
          assertCurrent();
          return { kind: "deleted" };
        }
        case "restore": {
          const result = await restoreBackup(
            command.target.backup_id,
            command.target.source_ref,
            options,
          );
          assertCurrent();
          return { kind: "restored", result };
        }
        case "download":
          await downloadBackup(command.target.backup_id, command.target.source_ref, options);
          assertCurrent();
          return { kind: "downloaded" };
        case "adopt-local": {
          const saved = await adoptLocalBackup(command.target.filename, options);
          await publishAdoption(saved, backupCatalogKeys.local, command.target);
          return { kind: "adopted" };
        }
        case "adopt-s3": {
          if (!command.target.source_ref || !command.target.archive_sha256)
            throw new ApiError(409, "backup_adoption_unavailable", "backup_adoption_unavailable");
          const saved = await adoptS3Backup(
            command.target.key,
            command.target.source_ref,
            command.target.archive_sha256,
            options,
          );
          await publishAdoption(saved, backupCatalogKeys.s3, command.target);
          return { kind: "adopted" };
        }
        case "adopt-remote": {
          if (!command.target.source_ref || !command.target.archive_sha256)
            throw new ApiError(409, "backup_adoption_unavailable", "backup_adoption_unavailable");
          const saved = await adoptRemoteBackup(
            command.target.connection_id,
            command.target.key,
            command.target.source_ref,
            command.target.archive_sha256,
            options,
          );
          await publishAdoption(saved, backupCatalogKeys.remote, command.target);
          return { kind: "adopted" };
        }
      }
      throw new Error("Unsupported backup command");
    } finally {
      if (active.current === controller) active.current = null;
    }
  }
  return { run };
}
