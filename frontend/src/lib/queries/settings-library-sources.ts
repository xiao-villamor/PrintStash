/** Library source reads and exact management gestures own their authoritative projection. */
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { queryOptions, useQueryClient } from "@tanstack/react-query";
import { getVaultConfig, updateVaultConfig } from "@/lib/api/config";
import { listStorageConnections } from "@/lib/api/storage-connections";
import {
  createExternalLibrary,
  updateExternalLibrary,
  enrollExternalLibraryRoot,
  deleteExternalLibrary,
  scanExternalLibrary,
  listExternalLibraries,
} from "@/lib/api/libraries";
import { useAuth } from "@/lib/auth-context";
import { onAuthChange } from "@/lib/auth-store";
import { ApiError, parseApiError } from "@/lib/errors";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import { waitForImportJob } from "@/lib/task-center";
import { storageConnectionKeys } from "@/lib/queries/settings-storage";
import type {
  StorageConnection,
  ExternalLibrary,
  ExternalLibraryCreate,
  ExternalLibraryUpdate,
} from "@/types";
type TaskText = NonNullable<Parameters<typeof waitForImportJob>[1]>;

export interface LibrarySourcesApi {
  getConfig: typeof getVaultConfig;
  updateConfig: typeof updateVaultConfig;
  list: typeof listExternalLibraries;
  listConnections: typeof listStorageConnections;
  create: typeof createExternalLibrary;
  update: typeof updateExternalLibrary;
  enroll: typeof enrollExternalLibraryRoot;
  remove: typeof deleteExternalLibrary;
  scan: typeof scanExternalLibrary;
}
export const defaultLibrarySourcesApi: LibrarySourcesApi = {
  getConfig: getVaultConfig,
  updateConfig: updateVaultConfig,
  list: listExternalLibraries,
  listConnections: listStorageConnections,
  create: createExternalLibrary,
  update: updateExternalLibrary,
  enroll: enrollExternalLibraryRoot,
  remove: deleteExternalLibrary,
  scan: scanExternalLibrary,
};
export const librarySourceKeys = { all: ["library-sources"] as const };
export type LibrarySources = { kind: "enabled"; items: ExternalLibrary[] } | { kind: "disabled" };
export function librarySourcesOptions(
  reader: typeof listExternalLibraries = listExternalLibraries,
) {
  return queryOptions({
    queryKey: librarySourceKeys.all,
    queryFn: async ({ signal }): Promise<LibrarySources> => {
      try {
        return { kind: "enabled", items: await reader({ fresh: true, signal }) };
      } catch (error) {
        const failure = parseApiError(error);
        if (failure.status === 404 && failure.code === "feature_disabled")
          return { kind: "disabled" };
        throw error;
      }
    },
    retry: false,
  });
}
export type LibrarySourceCommand = { session: number } & (
  | { kind: "create"; payload: ExternalLibraryCreate }
  | { kind: "update"; id: number; payload: ExternalLibraryUpdate }
  | { kind: "enroll"; id: number; root: string }
  | { kind: "delete"; id: number }
  | { kind: "scan"; id: number; title: TaskText }
);
type CommandState =
  | { status: "idle" | "success" }
  | { status: "pending"; id: number | "create" }
  | { status: "error"; error: unknown };
/** Local disposal aborts browser commands; accepted scans remain in the durable TaskCenter. */
export function useLibrarySourceCommand(
  api: LibrarySourcesApi = defaultLibrarySourcesApi,
  allowed = true,
) {
  const client = useQueryClient();
  const { user } = useAuth();
  const admin = useRef(!!user?.is_superuser && allowed);
  useLayoutEffect(() => {
    admin.current = !!user?.is_superuser && allowed;
  }, [user?.is_superuser, allowed]);
  const live = useRef(true);
  const active = useRef<AbortController | null>(null);
  const [state, setState] = useState<CommandState>({ status: "idle" });
  useEffect(() => {
    live.current = true;
    const release = onAuthChange(() => {
      active.current?.abort();
      active.current = null;
      setState({ status: "idle" });
    });
    return () => {
      live.current = false;
      active.current?.abort();
      active.current = null;
      release();
    };
  }, []);
  function mutateAsync(
    command: Extract<LibrarySourceCommand, { kind: "create" | "update" | "enroll" }>,
  ): Promise<ExternalLibrary>;
  function mutateAsync(
    command: Extract<LibrarySourceCommand, { kind: "scan" }>,
  ): ReturnType<typeof waitForImportJob>;
  function mutateAsync(command: Extract<LibrarySourceCommand, { kind: "delete" }>): Promise<void>;
  function mutateAsync(
    command: LibrarySourceCommand,
  ): Promise<ExternalLibrary | Awaited<ReturnType<typeof waitForImportJob>> | void>;
  async function mutateAsync(
    command: LibrarySourceCommand,
  ): Promise<ExternalLibrary | Awaited<ReturnType<typeof waitForImportJob>> | void> {
    if (!live.current) throw new DOMException("Library sources view was disposed", "AbortError");
    if (active.current) throw new Error("A library source command is already pending");
    requireSessionVersion(command.session);
    const controller = new AbortController();
    active.current = controller;
    setState({ status: "pending", id: command.kind === "create" ? "create" : command.id });
    function assertCurrent() {
      requireSessionVersion(command.session);
      controller.signal.throwIfAborted();
      if (!live.current || active.current !== controller)
        throw new DOMException("Library sources view was disposed", "AbortError");
      if (!admin.current) throw new ApiError(403, "forbidden", "forbidden");
    }
    function isCurrent() {
      return (
        live.current &&
        active.current === controller &&
        !controller.signal.aborted &&
        admin.current &&
        command.session === getSessionVersion()
      );
    }
    function assertEligible() {
      const query = client.getQueryState(librarySourceKeys.all);
      if (query?.status === "error") throw parseApiError(query.error);
      const sources = client.getQueryData<LibrarySources>(librarySourceKeys.all);
      if (sources?.kind !== "enabled")
        throw new ApiError(404, "feature_disabled", "feature_disabled");
      if (
        command.kind === "create" &&
        command.payload.source_kind &&
        command.payload.source_kind !== "mounted"
      ) {
        const connectionRead = client.getQueryState(storageConnectionKeys.all);
        if (connectionRead?.status === "error") throw parseApiError(connectionRead.error);
        const connections = client.getQueryData<StorageConnection[]>(storageConnectionKeys.all);
        const connection = connections?.find((row) => row.id === command.payload.connection_id);
        if (
          !connection ||
          !connection.enabled ||
          connection.kind !== command.payload.source_kind ||
          !["library", "both"].includes(connection.purpose)
        )
          throw new ApiError(
            409,
            "storage_connection_incompatible",
            "storage_connection_incompatible",
          );
      }
      if (command.kind !== "create" && !sources.items.some((row) => row.id === command.id))
        throw new ApiError(404, "library_not_found", "library_not_found");
    }
    async function cancelReads() {
      assertCurrent();
      await client.cancelQueries({ queryKey: librarySourceKeys.all, exact: true });
      assertCurrent();
    }
    try {
      assertCurrent();
      assertEligible();
      await cancelReads();
      assertEligible();
      const options = { signal: controller.signal };
      let row: ExternalLibrary;
      switch (command.kind) {
        case "create":
          row = await api.create(command.payload, options);
          break;
        case "update":
          row = await api.update(command.id, command.payload, options);
          break;
        case "enroll":
          row = await api.enroll(command.id, { confirm_root_path: command.root }, options);
          break;
        case "delete":
          await api.remove(command.id, options);
          await cancelReads();
          client.setQueryData<LibrarySources>(librarySourceKeys.all, (previous) => {
            assertCurrent();
            return previous?.kind === "enabled"
              ? { ...previous, items: previous.items.filter((item) => item.id !== command.id) }
              : previous;
          });
          if (isCurrent()) setState({ status: "success" });
          return;
        case "scan": {
          const accepted = await api.scan(command.id, options);
          // Receipt is known in this session; disposal must not drop valid accepted work.
          requireSessionVersion(command.session);
          if (!admin.current) throw new ApiError(403, "forbidden", "forbidden");
          const job = await waitForImportJob(accepted.job_id, command.title);
          await cancelReads();
          await client.invalidateQueries(
            { queryKey: librarySourceKeys.all, exact: true },
            { throwOnError: true },
          );
          assertCurrent();
          if (job.state !== "completed") throw new Error(job.error ?? "scan_failed");
          if (isCurrent()) setState({ status: "success" });
          return job;
        }
      }
      await cancelReads();
      client.setQueryData<LibrarySources>(librarySourceKeys.all, (previous) => {
        assertCurrent();
        if (previous?.kind !== "enabled") return previous;
        const exists = previous.items.some((item) => item.id === row.id);
        return {
          ...previous,
          items: exists
            ? previous.items.map((item) => (item.id === row.id ? row : item))
            : [...previous.items, row],
        };
      });
      if (isCurrent()) setState({ status: "success" });
      return row;
    } catch (error) {
      if (isCurrent()) setState({ status: "error", error });
      throw error;
    } finally {
      if (active.current === controller) active.current = null;
    }
  }
  return {
    mutateAsync,
    clearError: () =>
      setState((previous) => (previous.status === "error" ? { status: "idle" } : previous)),
    busyId: state.status === "pending" ? state.id : null,
    error: state.status === "error" ? state.error : null,
  };
}
