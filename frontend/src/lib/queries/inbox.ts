import { useEffect } from "react";
import { type QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  batchPendingImports,
  dismissPendingImport,
  getPendingImport,
  importPendingImport,
  listPendingImports,
  retryPendingImport,
  updatePendingImport,
} from "@/lib/api/inbox";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import type { InboxItem } from "@/types";

export const inboxApi = {
  batchPendingImports,
  dismissPendingImport,
  getPendingImport,
  importPendingImport,
  listPendingImports,
  retryPendingImport,
  updatePendingImport,
};
export type InboxApi = typeof inboxApi;
export const inboxKeys = {
  all: ["inbox"] as const,
  snapshots: ["inbox", "snapshot"] as const,
  snapshot: (includeCompleted: boolean) => ["inbox", "snapshot", includeCompleted] as const,
  detail: (id: number | null) => ["inbox", "detail", id] as const,
};
const ACTIVE_STATES = new Set<InboxItem["state"]>(["captured", "resolving", "importing"]);
export function inboxIsActive(item: InboxItem) {
  return ACTIVE_STATES.has(item.state);
}
export function inboxIsTerminal(item: InboxItem) {
  return item.state === "completed" || item.state === "failed" || item.state === "dismissed";
}

function snapshotOptions(api: Pick<InboxApi, "listPendingImports">, includeCompleted: boolean) {
  return {
    queryKey: inboxKeys.snapshot(includeCompleted),
    queryFn: ({ signal }: { signal: AbortSignal }) =>
      api.listPendingImports(includeCompleted, signal),
  };
}

function publishSnapshotRow(client: QueryClient, item: InboxItem) {
  const version = getSessionVersion();
  for (const [key, rows] of client.getQueriesData<InboxItem[]>({ queryKey: inboxKeys.snapshots })) {
    if (!rows) continue;
    requireSessionVersion(version);
    const includeCompleted = key[2] === true;
    client.setQueryData(
      key,
      rows.flatMap((row) => {
        if (row.id !== item.id) return [row];
        if (item.state === "dismissed" || (!includeCompleted && item.state === "completed"))
          return [];
        return [item];
      }),
    );
  }
}

/** Complete queue/history projection; navigation excludes completed history. */
export function useInboxSnapshot(api: Pick<InboxApi, "listPendingImports"> = inboxApi) {
  return useQuery({
    ...snapshotOptions(api, true),
    refetchInterval: (query) => (query.state.data?.some(inboxIsActive) ? 1_500 : false),
  });
}

export function useInboxCount(enabled: boolean) {
  return useQuery({
    ...snapshotOptions(inboxApi, false),
    enabled,
    select: (items) =>
      items.filter((item) => item.state !== "completed" && item.state !== "dismissed").length,
    refetchInterval: 30_000,
  });
}

export function useInboxItem(
  id: number | null,
  api: Pick<InboxApi, "getPendingImport">,
  forcePolling: boolean,
) {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: inboxKeys.detail(id),
    queryFn: ({ signal }) => {
      if (id === null) throw new Error("Inbox id is required");
      return api.getPendingImport(id, signal);
    },
    enabled: id !== null,
    refetchInterval: (query) =>
      query.state.data &&
      !inboxIsTerminal(query.state.data) &&
      (forcePolling || inboxIsActive(query.state.data))
        ? 1_500
        : false,
  });
  // A detail response refreshes the row represented in the shared list. This is
  // cache reconciliation, never a second copy held in component state.
  useEffect(() => {
    if (!query.data || client.getQueryData(inboxKeys.detail(query.data.id)) !== query.data) return;
    const item = query.data;
    publishSnapshotRow(client, item);
  }, [client, query.data]);
  return query;
}

/** Inbox commands declare the snapshot/detail views they affect. */
export function useInboxCommands(api: InboxApi = inboxApi) {
  const client = useQueryClient();
  const cancel = () => client.cancelQueries({ queryKey: inboxKeys.all });
  const prepare = async () => {
    const version = getSessionVersion();
    await cancel();
    requireSessionVersion(version);
    return version;
  };
  const assertSession = (version: number | undefined) => {
    if (version === undefined) throw new Error("Inbox mutation session context is required");
    requireSessionVersion(version);
  };
  const publish = async (item: InboxItem, version: number | undefined) => {
    await cancel();
    assertSession(version);
    client.setQueryData(inboxKeys.detail(item.id), item);
    assertSession(version);
    publishSnapshotRow(client, item);
  };
  const remove = (ids: number[], version: number | undefined) => {
    assertSession(version);
    client.setQueriesData<InboxItem[]>({ queryKey: inboxKeys.snapshots }, (items) => {
      assertSession(version);
      return items?.filter((item) => !ids.includes(item.id));
    });
    for (const id of ids) {
      assertSession(version);
      client.removeQueries({ queryKey: inboxKeys.detail(id) });
    }
  };
  return {
    dismiss: useMutation({
      mutationFn: (id: number) => api.dismissPendingImport(id),
      onMutate: prepare,
      onSuccess: async (_, id, version) => {
        await cancel();
        remove([id], version);
      },
    }),
    batch: useMutation({
      mutationFn: (payload: Parameters<InboxApi["batchPendingImports"]>[0]) =>
        api.batchPendingImports(payload),
      onMutate: prepare,
      onSuccess: async (items, payload, version) => {
        await cancel();
        if (payload.action === "dismiss") remove(payload.item_ids, version);
        else for (const item of items) await publish(item, version);
      },
    }),
    retry: useMutation({
      mutationFn: (id: number) => api.retryPendingImport(id),
      onMutate: prepare,
      onSuccess: async (item, _, version) => {
        await publish(item, version);
        assertSession(version);
        void client.invalidateQueries({ queryKey: inboxKeys.detail(item.id) });
      },
    }),
    update: useMutation({
      mutationFn: ({
        id,
        payload,
      }: {
        id: number;
        payload: Parameters<typeof updatePendingImport>[1];
      }) => api.updatePendingImport(id, payload),
      onMutate: prepare,
      onSuccess: (item, _, version) => publish(item, version),
    }),
    import: useMutation({
      mutationFn: ({ id, selectedIds }: { id: number; selectedIds: string[] }) =>
        api.importPendingImport(id, selectedIds),
      onMutate: prepare,
      onSuccess: (item, _, version) => publish(item, version),
    }),
  };
}
