import { queryKeys } from "@/lib/query-client";
import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import {
  queryOptions,
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { ApiError } from "@/lib/errors";
import { isLoggedIn, onAuthChange } from "@/lib/auth-store";
import { getSessionVersion, withSessionRequest } from "@/lib/session-transport";
import { subscribeEvents } from "@/lib/events";
import {
  listMaintenanceWindows,
  listMaintenanceLog,
  createMaintenanceWindow,
  createMaintenanceLog,
  deleteMaintenanceWindow,
  deleteMaintenanceLog,
  updatePrinterRouting,
} from "@/lib/api/fleet";

import {
  getPrinter,
  createPrinter,
  deletePrinter,
  listPrinterJobs,
  listPrinterFiles,
  getPrinterDiagnostics,
  getMoonrakerConfig,
  syncPrinterFiles,
  deletePrinterFile,
  startPrinterFile,
} from "@/lib/api/printers";
import type { PrinterRead, StartPrinterFile } from "@/types";

export const printerKeys = {
  all: ["printers"] as const,
  detail: (id: number) => ["printers", id] as const,
  jobs: (id: number) => ["printers", id, "jobs", 50] as const,
  files: (id: number) => ["printers", id, "files"] as const,
  diagnostics: (id: number) => ["printers", id, "diagnostics"] as const,
  config: (id: number) => ["printers", id, "config"] as const,
  windows: (id: number) => ["printers", id, "maintenance", "windows"] as const,
  log: (id: number) => ["printers", id, "maintenance", "log"] as const,
};

export interface MaintenanceApi {
  listWindows: typeof listMaintenanceWindows;
  listLog: typeof listMaintenanceLog;
  createWindow: typeof createMaintenanceWindow;
  createLog: typeof createMaintenanceLog;
  deleteWindow: typeof deleteMaintenanceWindow;
  deleteLog: typeof deleteMaintenanceLog;
  updateRouting: typeof updatePrinterRouting;
}
export const maintenanceApi: MaintenanceApi = {
  listWindows: listMaintenanceWindows,
  listLog: listMaintenanceLog,
  createWindow: createMaintenanceWindow,
  createLog: createMaintenanceLog,
  deleteWindow: deleteMaintenanceWindow,
  deleteLog: deleteMaintenanceLog,
  updateRouting: updatePrinterRouting,
};

export function maintenanceWindowsOptions(id: number, api = maintenanceApi) {
  return queryOptions({
    queryKey: printerKeys.windows(id),
    queryFn: ({ signal }) => api.listWindows(id, { signal }),
    staleTime: 30_000,
  });
}
export function maintenanceLogOptions(id: number, api = maintenanceApi) {
  return queryOptions({
    queryKey: printerKeys.log(id),
    queryFn: ({ signal }) => api.listLog(id, { signal }),
    staleTime: 30_000,
  });
}

/** The per-printer API still costs two initial reads; stable keys avoid fleet-wide rerenders. */
export function usePrinterMaintenance(ids: readonly number[], api = maintenanceApi) {
  useSyncExternalStore(onAuthChange, getSessionVersion, getSessionVersion);
  const enabled = isLoggedIn();
  const selected = [...new Set(ids)];
  const windowQueries = useQueries({
    queries: selected.map((id) => ({ ...maintenanceWindowsOptions(id, api), enabled })),
  });
  const logQueries = useQueries({
    queries: selected.map((id) => ({ ...maintenanceLogOptions(id, api), enabled })),
  });
  const client = useQueryClient();
  useEffect(() => {
    if (!enabled || !ids.length) return;
    return subscribeEvents((notice) => {
      if (notice.type !== "resync") return;
      void client.invalidateQueries(
        { queryKey: printerKeys.all, predicate: (query) => query.queryKey[2] === "maintenance" },
        { cancelRefetch: false },
      );
    });
  }, [client, enabled, ids.length]);
  return {
    windows: enabled ? windowQueries.flatMap((query) => query.data ?? []) : [],
    logs: enabled ? logQueries.flatMap((query) => query.data ?? []) : [],
    error: [...windowQueries, ...logQueries].find((query) => query.error)?.error ?? null,
    retry: () =>
      Promise.all(
        [...windowQueries, ...logQueries]
          .filter((query) => query.isError)
          .map((query) => query.refetch({ cancelRefetch: false })),
      ),
  };
}

type MaintenanceChange =
  | {
      kind: "create-window";
      printerId: number;
      payload: Parameters<typeof createMaintenanceWindow>[1];
    }
  | { kind: "create-log"; printerId: number; payload: Parameters<typeof createMaintenanceLog>[1] }
  | { kind: "delete-window"; printerId: number; id: number }
  | { kind: "delete-log"; printerId: number; id: number }
  | { kind: "routing"; printerId: number; payload: Parameters<typeof updatePrinterRouting>[1] };

/** Reconcile acknowledged changes only, fencing cancellation/invalidation across scope retirement. */
export function useMaintenanceMutation(api = maintenanceApi) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (change: MaintenanceChange) =>
      withSessionRequest(async (request) => {
        let keys: readonly (readonly unknown[])[];
        switch (change.kind) {
          case "create-window":
            await api.createWindow(change.printerId, change.payload);
            keys = [printerKeys.windows(change.printerId)];
            break;
          case "create-log":
            await api.createLog(change.printerId, change.payload);
            keys = [printerKeys.log(change.printerId)];
            break;
          case "delete-window":
            await api.deleteWindow(change.printerId, change.id);
            keys = [printerKeys.windows(change.printerId)];
            break;
          case "delete-log":
            await api.deleteLog(change.printerId, change.id);
            keys = [printerKeys.log(change.printerId)];
            break;
          case "routing":
            await api.updateRouting(change.printerId, change.payload);
            keys = [
              printerKeys.detail(change.printerId),
              printerKeys.all,
              ["printers", "dashboard"],
              ["fleet", "queue"],
              ["fleet", "summary"],
            ];
            break;
        }
        request.assertCurrent();
        await Promise.all(keys.map((queryKey) => client.cancelQueries({ queryKey, exact: true })));
        request.assertCurrent();
        await Promise.all(
          keys.map((queryKey) => client.invalidateQueries({ queryKey, exact: true })),
        );
        request.assertCurrent();
      }),
  });
}

export function printerDetailOptions(id: number) {
  return queryOptions({
    queryKey: printerKeys.detail(id),
    queryFn: ({ signal }) => getPrinter(id, { signal }),
    staleTime: 30_000,
  });
}
export function printerJobsOptions(id: number) {
  return queryOptions({
    queryKey: printerKeys.jobs(id),
    queryFn: ({ signal }) => listPrinterJobs(id, 50, { signal }),
    staleTime: 30_000,
  });
}
export function printerFilesOptions(id: number) {
  return queryOptions({
    queryKey: printerKeys.files(id),
    queryFn: ({ signal }) => listPrinterFiles(id, { signal }),
    staleTime: 30_000,
  });
}
export function printerDiagnosticsOptions(id: number) {
  return queryOptions({
    queryKey: printerKeys.diagnostics(id),
    queryFn: ({ signal }) => getPrinterDiagnostics(id, { signal }),
    staleTime: 30_000,
  });
}
export function printerConfigOptions(id: number) {
  return queryOptions({
    queryKey: printerKeys.config(id),
    queryFn: ({ signal }) => getMoonrakerConfig(id, { signal }),
    staleTime: 30_000,
  });
}

/** HTTP state is shared; route snapshots seed only the incarnation that received them. */
export function usePrinterResources(id: number, initialPrinter?: PrinterRead) {
  const session = useSyncExternalStore(onAuthChange, getSessionVersion, getSessionVersion);
  const [initialSession] = useState(session);
  const enabled = isLoggedIn();
  const client = useQueryClient();
  const detail = useQuery({
    ...printerDetailOptions(id),
    enabled,
    initialData:
      session === initialSession && initialPrinter?.id === id ? initialPrinter : undefined,
    initialDataUpdatedAt: 0,
  });
  const accessDenied =
    detail.error instanceof ApiError &&
    (detail.error.status === 403 || detail.error.status === 404);
  const allowed = enabled && !accessDenied;
  const printer = allowed ? (detail.data ?? null) : null;
  const jobs = useQuery({ ...printerJobsOptions(id), enabled: allowed });
  const files = useQuery({ ...printerFilesOptions(id), enabled: allowed });
  const canAdmin = allowed && Boolean(printer?.access.can_admin);
  const diagnostics = useQuery({ ...printerDiagnosticsOptions(id), enabled: canAdmin });
  const canConfigure = canAdmin && printer?.provider === "moonraker";
  const config = useQuery({ ...printerConfigOptions(id), enabled: canConfigure });
  const refresh = useCallback(
    async (queryKey: readonly unknown[]) => {
      await client.invalidateQueries({ queryKey, exact: true }, { cancelRefetch: false });
    },
    [client],
  );
  const refreshAll = useCallback(async () => {
    if (accessDenied) {
      await refresh(printerKeys.detail(id));
      return;
    }
    await Promise.all(
      [
        printerKeys.detail(id),
        printerKeys.jobs(id),
        printerKeys.files(id),
        printerKeys.diagnostics(id),
        printerKeys.config(id),
      ].map(refresh),
    );
  }, [accessDenied, id, refresh]);
  useEffect(() => {
    if (!enabled) return;
    return subscribeEvents((event) => {
      if (event.type === "resync") void refreshAll();
    });
  }, [enabled, refreshAll]);
  return {
    printer,
    accessDenied,
    jobs: allowed ? (jobs.data ?? []) : [],
    files: allowed ? (files.data ?? []) : [],
    diagnostics: canAdmin ? (diagnostics.data ?? null) : null,
    config: canConfigure ? (config.data ?? null) : null,
    checkingDiagnostics: diagnostics.isFetching,
    loadingConfig: config.isFetching,
    error:
      [
        detail,
        jobs,
        files,
        ...(canAdmin ? [diagnostics] : []),
        ...(canConfigure ? [config] : []),
      ].find((query) => query.error)?.error ?? null,
    refresh,
    refreshAll,
  };
}

type PrinterFileChange =
  | { kind: "sync"; printerId: number; signal: AbortSignal }
  | { kind: "delete"; printerId: number; fileId: number; signal: AbortSignal }
  | { kind: "start"; printerId: number; payload: StartPrinterFile; signal: AbortSignal };

/** Acknowledged file lists replace cancelled obsolete reads, within the caller's view lifetime. */
export function usePrinterFileMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (change: PrinterFileChange) =>
      withSessionRequest(async (request) => {
        if (change.kind === "start") {
          const job = await startPrinterFile(change.printerId, change.payload, {
            signal: request.signal,
          });
          request.assertCurrent();
          await Promise.all(
            [printerKeys.jobs(change.printerId), printerKeys.detail(change.printerId)].map(
              async (queryKey) => {
                await client.cancelQueries({ queryKey, exact: true });
                request.assertCurrent();
                await client.invalidateQueries({ queryKey, exact: true });
              },
            ),
          );
          request.assertCurrent();
          return job;
        }
        const files =
          change.kind === "sync"
            ? await syncPrinterFiles(change.printerId, { signal: request.signal })
            : await deletePrinterFile(change.printerId, change.fileId, { signal: request.signal });
        request.assertCurrent();
        await client.cancelQueries({ queryKey: printerKeys.files(change.printerId), exact: true });
        request.assertCurrent();
        client.setQueryData(printerKeys.files(change.printerId), files);
        await client.cancelQueries({ queryKey: printerKeys.detail(change.printerId), exact: true });
        request.assertCurrent();
        await client.invalidateQueries({
          queryKey: printerKeys.detail(change.printerId),
          exact: true,
        });
        request.assertCurrent();
        return null;
      }, change.signal),
  });
}

/** Catalog membership changes revalidate all printer choices and dashboards. */
export function usePrinterCatalogCommands() {
  const client = useQueryClient();
  async function change<T>(write: () => Promise<T>): Promise<T> {
    return withSessionRequest(async (request) => {
      const receipt = await write();
      request.assertCurrent();
      await client.cancelQueries({ queryKey: printerKeys.all });
      request.assertCurrent();
      void client.invalidateQueries({ queryKey: printerKeys.all });
      return receipt;
    });
  }
  return {
    createPrinter: (...args: Parameters<typeof createPrinter>) =>
      change(() => createPrinter(...args)),
    deletePrinter: (...args: Parameters<typeof deletePrinter>) =>
      change(() => deletePrinter(...args)),
  };
}

/** Queue acknowledgements affect fleet summaries and the printer dashboard. */
export function refreshFleet(client: QueryClient, session: number): void {
  if (session !== getSessionVersion()) return;
  for (const queryKey of [queryKeys.printers, queryKeys.fleetQueue, queryKeys.fleetSummary])
    void client.invalidateQueries({ queryKey });
}
