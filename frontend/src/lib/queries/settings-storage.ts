/** Public provider metadata and private reusable connections have separate remote owners. */
import { useEffect, useRef, useState } from "react";
import { queryOptions, useQueryClient } from "@tanstack/react-query";
import { getStorageProviders } from "@/lib/api/config";
import {
  listStorageConnections,
  createStorageConnection,
  updateStorageConnection,
  deleteStorageConnection,
  probeStorageConnection,
  type StorageConnectionCreate,
  type StorageConnectionUpdate,
} from "@/lib/api/storage-connections";
import { onAuthChange } from "@/lib/auth-store";
import { parseApiError, type ApiError } from "@/lib/errors";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import type { StorageConnection } from "@/types";

export const storageConnectionKeys = { all: ["storage-connections"] as const };
export const storageProviderKeys = { all: ["storage-providers"] as const };
export function storageConnectionsOptions(
  reader: typeof listStorageConnections = listStorageConnections,
) {
  return queryOptions({
    queryKey: storageConnectionKeys.all,
    queryFn: ({ signal }) => reader({ fresh: true, signal }),
    retry: false,
  });
}
export function storageProvidersOptions(reader: typeof getStorageProviders = getStorageProviders) {
  return queryOptions({
    queryKey: storageProviderKeys.all,
    queryFn: ({ signal }) => reader({ fresh: true, signal }),
    retry: false,
  });
}
export function storageReadDenied(error: ApiError) {
  return [401, 403, 404].includes(error.status);
}
export type StorageConnectionCommand = { session: number } & (
  | { kind: "create"; payload: StorageConnectionCreate }
  | { kind: "update"; id: number; payload: StorageConnectionUpdate }
  | { kind: "delete"; id: number }
  | { kind: "probe"; id: number }
);
export type StorageConnectionReceipt =
  | { kind: "saved"; connection: StorageConnection }
  | { kind: "deleted"; id: number }
  | { kind: "probed"; ok: boolean };
type PendingStorageCommand =
  | { kind: "create" }
  | { kind: "update" | "delete" | "probe"; id: number };
type StorageCommandState =
  | { status: "idle" | "success" }
  | { status: "pending"; command: PendingStorageCommand }
  | { status: "error"; error: unknown };

/** Credential arguments live only in this active async call, never a shared mutation entry. */
export function useStorageConnectionCommand() {
  const client = useQueryClient();
  const live = useRef(true);
  const active = useRef<AbortController | null>(null);
  const [state, setState] = useState<StorageCommandState>({ status: "idle" });
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
  async function mutateAsync(command: StorageConnectionCommand): Promise<StorageConnectionReceipt> {
    if (!live.current) throw new DOMException("Storage connection view was disposed", "AbortError");
    if (active.current) throw new Error("A storage connection command is already pending");
    requireSessionVersion(command.session);
    const controller = new AbortController();
    active.current = controller;
    setState({
      status: "pending",
      command:
        command.kind === "create" ? { kind: command.kind } : { kind: command.kind, id: command.id },
    });
    function assertCurrent() {
      requireSessionVersion(command.session);
      controller.signal.throwIfAborted();
    }
    try {
      assertCurrent();
      if (command.kind !== "probe")
        await client.cancelQueries({ queryKey: storageConnectionKeys.all, exact: true });
      assertCurrent();
      const options = { signal: controller.signal };
      let receipt: StorageConnectionReceipt;
      switch (command.kind) {
        case "create":
          receipt = {
            kind: "saved",
            connection: await createStorageConnection(command.payload, options),
          };
          break;
        case "update":
          receipt = {
            kind: "saved",
            connection: await updateStorageConnection(command.id, command.payload, options),
          };
          break;
        case "delete":
          await deleteStorageConnection(command.id, options);
          receipt = { kind: "deleted", id: command.id };
          break;
        case "probe":
          receipt = { kind: "probed", ...(await probeStorageConnection(command.id, options)) };
          break;
      }
      assertCurrent();
      if (receipt.kind !== "probed") {
        await client.cancelQueries({ queryKey: storageConnectionKeys.all, exact: true });
        assertCurrent();
        client.setQueryData<StorageConnection[]>(storageConnectionKeys.all, (previous) => {
          assertCurrent();
          if (!previous) return previous;
          if (receipt.kind === "deleted") return previous.filter((row) => row.id !== receipt.id);
          if (receipt.kind === "saved")
            return previous.some((row) => row.id === receipt.connection.id)
              ? previous.map((row) => (row.id === receipt.connection.id ? receipt.connection : row))
              : [...previous, receipt.connection];
          return previous;
        });
      }
      if (live.current && active.current === controller && command.session === getSessionVersion())
        setState({ status: "success" });
      return receipt;
    } catch (error) {
      if (command.session === getSessionVersion() && storageReadDenied(parseApiError(error)))
        void client.invalidateQueries({ queryKey: storageConnectionKeys.all, exact: true });
      if (live.current && active.current === controller && command.session === getSessionVersion())
        setState({ status: "error", error });
      throw error;
    } finally {
      if (active.current === controller) active.current = null;
    }
  }
  return {
    mutateAsync,
    pending: state.status === "pending" ? state.command : null,
    isPending: state.status === "pending",
    error: state.status === "error" ? state.error : null,
  };
}
