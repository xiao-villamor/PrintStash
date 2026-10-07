import { useAuth } from "@/lib/auth-context";
import type { EditingBase } from "@/types/editing";
import { captureEditingBase } from "@/lib/api/editing";
/** Public provider metadata and private reusable connections have separate remote owners. */
import { useEffect, useLayoutEffect, useRef, useState } from "react";
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
import { parseApiError, ApiError } from "@/lib/errors";
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
  | { kind: "update"; id: number; base: EditingBase; payload: StorageConnectionUpdate }
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

type ConnectionUpdateCommand = Extract<StorageConnectionCommand, { kind: "update" }>;
export type StorageConnectionReview =
  | { phase: "idle" }
  | { phase: "retired" }
  | { phase: "required" | "loading"; problem: "conflict" | "unconfirmed"; id: number }
  | {
      phase: "ready";
      problem: "conflict" | "unconfirmed";
      id: number;
      snapshot: StorageConnection;
      sameIdentity: boolean;
    };

/** Credential intents live only in this editor until acknowledged, discarded or retired. */
export function useStorageConnectionCommand() {
  const client = useQueryClient();
  const { user } = useAuth();
  const admin = useRef(!!user?.is_superuser);
  useLayoutEffect(() => {
    admin.current = !!user?.is_superuser;
  }, [user?.is_superuser]);
  const live = useRef(true);
  const failed = useRef<ConnectionUpdateCommand | null>(null);
  const [review, setReview] = useState<StorageConnectionReview>({ phase: "idle" });
  const reviewRef = useRef(review);
  function changeReview(next: StorageConnectionReview) {
    reviewRef.current = next;
    setReview(next);
  }
  function discardReview() {
    failed.current = null;
    changeReview({ phase: "idle" });
  }
  const active = useRef<AbortController | null>(null);
  const [state, setState] = useState<StorageCommandState>({ status: "idle" });
  useEffect(() => {
    live.current = true;
    const release = onAuthChange(() => {
      active.current?.abort();
      active.current = null;
      failed.current = null;
      changeReview({ phase: "retired" });
      setState({ status: "idle" });
    });
    return () => {
      live.current = false;
      failed.current = null;
      active.current?.abort();
      active.current = null;
      release();
    };
  }, []);
  async function execute(
    command: StorageConnectionCommand,
    revised = false,
  ): Promise<StorageConnectionReceipt> {
    if (!live.current) throw new DOMException("Storage connection view was disposed", "AbortError");
    if (active.current) throw new Error("A storage connection command is already pending");
    requireSessionVersion(command.session);
    if (!admin.current) throw new ApiError(403, "forbidden", "forbidden");
    if (!revised && reviewRef.current.phase !== "idle")
      throw new Error("Connection review is required");
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
      if (!admin.current) throw new ApiError(403, "forbidden", "forbidden");
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
            connection: await updateStorageConnection(command.id, command.payload, {
              ...options,
              base: command.base,
            }),
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
              ? previous.map((row) =>
                  row.id === receipt.connection.id &&
                  !(command.kind === "update" && row.edit_epoch !== command.base.edit_epoch) &&
                  !(
                    row.edit_epoch === receipt.connection.edit_epoch &&
                    row.edit_version > receipt.connection.edit_version
                  )
                    ? receipt.connection
                    : row,
                )
              : command.kind === "create"
                ? [...previous, receipt.connection]
                : previous;
          return previous;
        });
      }
      if (live.current && active.current === controller && command.session === getSessionVersion())
        setState({ status: "success" });
      discardReview();
      return receipt;
    } catch (error) {
      if (
        live.current &&
        active.current === controller &&
        command.session === getSessionVersion() &&
        command.kind === "update"
      ) {
        const status = parseApiError(error).status;
        if (storageReadDenied(parseApiError(error))) {
          failed.current = null;
          changeReview({ phase: "retired" });
        } else if (status === 412 || status === 428 || status === 0 || status >= 500) {
          failed.current = command;
          changeReview({
            phase: "required",
            id: command.id,
            problem: status === 412 || status === 428 ? "conflict" : "unconfirmed",
          });
        }
      }
      if (command.session === getSessionVersion() && storageReadDenied(parseApiError(error)))
        void client.invalidateQueries({ queryKey: storageConnectionKeys.all, exact: true });
      if (live.current && active.current === controller && command.session === getSessionVersion())
        setState({ status: "error", error });
      throw error;
    } finally {
      if (active.current === controller) active.current = null;
    }
  }
  async function readCurrent(adopt: boolean) {
    const intent = failed.current;
    const previous = reviewRef.current;
    if (!intent || (previous.phase !== "required" && previous.phase !== "ready"))
      throw new Error("Connection review is unavailable");
    if (!live.current || active.current) throw new Error("Connection editor is unavailable");
    requireSessionVersion(intent.session);
    const controller = new AbortController();
    active.current = controller;
    changeReview({ phase: "loading", id: intent.id, problem: previous.problem });
    const current = () => {
      requireSessionVersion(intent.session);
      controller.signal.throwIfAborted();
      if (!admin.current) throw new ApiError(403, "forbidden", "forbidden");
    };
    try {
      await client.cancelQueries({ queryKey: storageConnectionKeys.all, exact: true });
      current();
      const rows = adopt
        ? await client.fetchQuery({ ...storageConnectionsOptions(), staleTime: 0 })
        : await listStorageConnections({ signal: controller.signal });
      current();
      const snapshot = rows.find((row) => row.id === intent.id);
      if (!snapshot)
        throw new ApiError(404, "storage_connection_not_found", "storage_connection_not_found");
      if (adopt) discardReview();
      else
        changeReview({
          phase: "ready",
          id: intent.id,
          problem: previous.problem,
          snapshot,
          sameIdentity: snapshot.edit_epoch === intent.base.edit_epoch,
        });
      return snapshot;
    } catch (error) {
      if (live.current && intent.session === getSessionVersion() && !controller.signal.aborted) {
        if (storageReadDenied(parseApiError(error))) {
          failed.current = null;
          changeReview({ phase: "retired" });
          void client.invalidateQueries({ queryKey: storageConnectionKeys.all, exact: true });
        } else changeReview({ phase: "required", id: intent.id, problem: previous.problem });
      }
      throw error;
    } finally {
      if (active.current === controller) active.current = null;
    }
  }
  async function saveRevised(payload?: StorageConnectionUpdate) {
    const current = reviewRef.current;
    const intent = failed.current;
    if (current.phase !== "ready" || !current.sameIdentity || !intent)
      throw new Error("Review this connection before saving");
    return execute(
      { ...intent, base: captureEditingBase(current.snapshot), payload: payload ?? intent.payload },
      true,
    );
  }
  return {
    mutateAsync: (command: StorageConnectionCommand) => execute(command),
    review,
    blocked: review.phase !== "idle",
    discardReview,
    reviewLatest: () => readCurrent(false),
    adopt: () => readCurrent(true),
    saveRevised,
    pending: state.status === "pending" ? state.command : null,
    isPending: state.status === "pending",
    error: state.status === "error" ? state.error : null,
  };
}
