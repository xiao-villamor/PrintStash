/** Migration reads and receipts share one owner; backend state gates authorize every command. */
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { queryOptions, useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/api/vault-migration";
import { useAuth } from "@/lib/auth-context";
import { onAuthChange } from "@/lib/auth-store";
import { ApiError } from "@/lib/errors";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import { backupCatalogKeys } from "./settings-backup-catalog";
import type { BackupMeta } from "@/lib/api/backup";

export const migrationKeys = {
  all: ["vault-migrations"] as const,
  history: ["vault-migrations", "history"] as const,
  run: (id: string | null) => ["vault-migrations", "run", id] as const,
  report: (id: string | null) => ["vault-migrations", "report", id] as const,
};
export function migrationHistoryOptions() {
  return queryOptions({
    queryKey: migrationKeys.history,
    queryFn: ({ signal }) => api.listVaultMigrations({ signal }),
    retry: false,
    staleTime: 0,
  });
}
export function migrationRunOptions(id: string | null) {
  return queryOptions({
    queryKey: migrationKeys.run(id),
    queryFn: ({ signal }) => {
      if (id === null) throw new Error("A migration identity is required");
      return api.getVaultMigration(id, { signal });
    },
    retry: false,
    staleTime: 2000,
    refetchInterval: (query) =>
      query.state.status !== "error" && migrationRunning(query.state.data) ? 2000 : false,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
}
export function migrationReportOptions(id: string | null, run?: api.VaultMigrationRun | null) {
  return queryOptions({
    queryKey: [
      ...migrationKeys.report(id),
      run?.state,
      run?.failed_objects,
      run?.full_audit?.state,
    ],
    queryFn: ({ signal }) => {
      if (id === null) throw new Error("A migration identity is required");
      return api.getVaultMigrationReport(id, { signal });
    },
    retry: false,
    staleTime: 0,
  });
}
export function migrationRunning(run: api.VaultMigrationRun | undefined) {
  return (
    !!run &&
    (["copying", "cutover_pending", "draining", "delta_copy", "verifying", "activating"].includes(
      run.state,
    ) ||
      ["pending", "running"].includes(run.full_audit?.state ?? ""))
  );
}
export type MigrationAction =
  | "start"
  | "cutover"
  | "source"
  | "discard"
  | "manual"
  | "recover"
  | "resume"
  | "pause"
  | "retry"
  | "audit"
  | "retain";
export type MigrationCommand =
  | { kind: "refresh" }
  | { kind: "preflight"; payload: api.VaultMigrationPreflight }
  | { kind: "download"; target: api.VaultMigrationRun }
  | { kind: MigrationAction; target: api.VaultMigrationRun; backup: BackupMeta | undefined };

/** Credentials stay in the active call, never in a mutation cache or a history projection. */
export function useMigrationCommand() {
  const client = useQueryClient();
  const { user } = useAuth();
  const admin = useRef(!!user?.is_superuser);
  useLayoutEffect(() => {
    admin.current = !!user?.is_superuser;
  }, [user?.is_superuser]);
  const [session] = useState(getSessionVersion);
  const live = useRef(true);
  const active = useRef<AbortController | null>(null);
  const [pending, setPending] = useState(false);
  useEffect(() => {
    live.current = true;
    const release = onAuthChange(() => {
      active.current?.abort();
      setPending(false);
    });
    return () => {
      live.current = false;
      active.current?.abort();
      release();
    };
  }, []);
  async function execute(command: MigrationCommand): Promise<api.VaultMigrationRun | null> {
    requireSessionVersion(session);
    if (!live.current || active.current)
      throw new DOMException("Migration command unavailable", "AbortError");
    const controller = new AbortController();
    active.current = controller;
    setPending(true);
    function assertCurrent() {
      requireSessionVersion(session);
      controller.signal.throwIfAborted();
      if (!live.current) throw new DOMException("Migration view disposed", "AbortError");
      if (!admin.current) throw new ApiError(403, "forbidden", "forbidden");
    }
    function assertReviewed() {
      assertCurrent();
      if (command.kind === "preflight" || command.kind === "download" || command.kind === "refresh")
        return;
      const detail = client.getQueryState<api.VaultMigrationRun>(
        migrationKeys.run(command.target.id),
      );
      const history = client.getQueryState<api.VaultMigrationRun[]>(migrationKeys.history);
      if (detail?.status === "error") throw detail.error;
      if (history?.status === "error") throw history.error;
      const current =
        (history?.dataUpdatedAt ?? 0) > (detail?.dataUpdatedAt ?? 0)
          ? history?.data?.find((row) => row.id === command.target.id)
          : detail?.data;
      if (
        !current ||
        current.state !== command.target.state ||
        current.plan_digest !== command.target.plan_digest ||
        current.source_provider_ref !== command.target.source_provider_ref ||
        current.destination_provider_ref !== command.target.destination_provider_ref
      )
        throw new ApiError(409, "migration_plan_stale", "migration_plan_stale");
      if (command.kind === "source") {
        const catalog = client.getQueryState<BackupMeta[]>(backupCatalogKeys.owned);
        if (catalog?.status === "error") throw catalog.error;
        if (
          !command.backup?.source_ref ||
          !catalog?.data?.some(
            (row) =>
              row.source_ref === command.backup?.source_ref &&
              row.backup_id === command.backup?.backup_id,
          )
        )
          throw new ApiError(
            409,
            "migration_recent_backup_required",
            "migration_recent_backup_required",
          );
      }
    }
    async function publish(run: api.VaultMigrationRun) {
      assertCurrent();
      await client.cancelQueries({ queryKey: migrationKeys.all });
      assertCurrent();
      client.setQueryData(migrationKeys.run(run.id), run);
      client.setQueryData<api.VaultMigrationRun[]>(migrationKeys.history, (previous) => [
        run,
        ...(previous ?? []).filter((row) => row.id !== run.id),
      ]);
      void client.invalidateQueries({ queryKey: migrationKeys.report(run.id) });
    }
    let dispatched = false;
    try {
      assertReviewed();
      await client.cancelQueries({ queryKey: migrationKeys.all });
      assertReviewed();
      const options = { signal: controller.signal };
      dispatched = true;
      let next: api.VaultMigrationRun;
      switch (command.kind) {
        case "refresh": {
          const rows = await client.fetchQuery({
            ...migrationHistoryOptions(),
            queryFn: ({ signal }) =>
              api.listVaultMigrations({ signal: AbortSignal.any([signal, controller.signal]) }),
          });
          assertCurrent();
          await client.cancelQueries({ queryKey: migrationKeys.all });
          assertCurrent();
          client.setQueryData(migrationKeys.history, rows);
          for (const row of rows) client.setQueryData(migrationKeys.run(row.id), row);
          void client.invalidateQueries({ queryKey: ["vault-migrations", "report"] });
          return null;
        }
        case "download":
          await api.downloadVaultMigrationReport(command.target.id, options);
          return null;
        case "preflight":
          next = await api.preflightVaultMigration(command.payload, options);
          break;
        case "start":
          next = await api.startVaultMigration(
            command.target.id,
            command.target.plan_digest,
            options,
          );
          break;
        case "cutover":
          next = await api.cutoverVaultMigration(command.target.id, options);
          break;
        case "recover":
          next = await api.recoverVaultMigration(command.target.id, options);
          break;
        case "resume":
          next = await api.resumeVaultMigration(command.target.id, options);
          break;
        case "pause":
          next = await api.pauseVaultMigration(command.target.id, options);
          break;
        case "retry":
          next = await api.retryVaultMigration(command.target.id, options);
          break;
        case "audit":
          next = await api.auditVaultMigration(command.target.id, options);
          break;
        case "retain":
        case "manual":
          next = await api.retainVaultMigration(
            command.target.id,
            command.kind === "manual",
            options,
          );
          break;
        case "discard":
          next = await api.cleanupVaultMigration(command.target.id, false, undefined, options);
          break;
        case "source":
          next = await api.cleanupVaultMigration(
            command.target.id,
            true,
            command.backup
              ? {
                  backup_id: command.backup.backup_id,
                  backup_source_ref: command.backup.source_ref,
                }
              : undefined,
            options,
          );
          break;
      }
      await publish(next);
      return next;
    } catch (error) {
      // A lost response may have committed a transition. Read it; never replay the command.
      if (
        dispatched &&
        command.kind !== "preflight" &&
        command.kind !== "download" &&
        command.kind !== "refresh" &&
        live.current &&
        !controller.signal.aborted &&
        session === getSessionVersion()
      ) {
        try {
          await publish(
            await client.fetchQuery({
              ...migrationRunOptions(command.target.id),
              staleTime: 0,
              queryFn: ({ signal }) =>
                api.getVaultMigration(command.target.id, {
                  signal: AbortSignal.any([signal, controller.signal]),
                }),
            }),
          );
        } catch {
          /* The original command failure remains visible; Refresh permits explicit recovery. */
        }
      }
      throw error;
    } finally {
      if (active.current === controller) {
        active.current = null;
        if (live.current && session === getSessionVersion()) setPending(false);
      }
    }
  }
  return { execute, pending };
}
