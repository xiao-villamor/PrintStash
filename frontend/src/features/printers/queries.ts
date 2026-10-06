import { useEffect, useSyncExternalStore } from "react";
import { queryOptions, useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
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

export const printerKeys = {
  all: ["printers"] as const,
  detail: (id: number) => ["printers", id] as const,
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
