import { refreshLibraryMetadata } from "./metadata";
import { acceptsEditingSnapshot } from "./editing";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { type QueryClient, queryOptions, useQueryClient } from "@tanstack/react-query";
import { settingsTrashKeys } from "@/lib/queries/settings-trash";
import {
  deleteMultipartModel,
  getMultipartModel,
  moveMultipartModel,
} from "@/lib/api/multipart-models";
import { onAuthChange } from "@/lib/auth-store";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import { queryKeys } from "@/lib/query-client";
import type { EditingBase } from "@/types/editing";
import type { MultipartModelRead } from "@/types";

export function multipartDetailOptions(id: number | null, read = getMultipartModel) {
  return queryOptions({
    queryKey:
      id === null
        ? [...queryKeys.multipartModels, "detail", "empty"]
        : queryKeys.multipartModel(id),
    queryFn: ({ signal }) => {
      if (id === null) throw new Error("Multipart model id is required");
      return read(id, { signal });
    },
    enabled: id !== null,
    retry: false,
  });
}

/** Confirmed aggregate receipts retire prior reads before replacing the shared detail. */
export function useMultipartPublication(id: number) {
  const client = useQueryClient();
  const [session] = useState(getSessionVersion);
  const current = useSyncExternalStore(onAuthChange, getSessionVersion);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const isCurrent = useCallback(
    () => mounted.current && session === getSessionVersion(),
    [session],
  );
  const observedEpoch =
    client.getQueryData<MultipartModelRead>(queryKeys.multipartModel(id))?.edit_epoch ?? null;
  const publish = useCallback(
    async (
      next: MultipartModelRead | ((value: MultipartModelRead) => MultipartModelRead),
      signal?: AbortSignal,
    ) => {
      if (!isCurrent() || signal?.aborted) return false;
      await client.cancelQueries({ queryKey: queryKeys.multipartModel(id), exact: true });
      if (!isCurrent() || signal?.aborted) return false;
      let accepted = false;
      client.setQueryData<MultipartModelRead>(queryKeys.multipartModel(id), (value) => {
        const candidate = next instanceof Function ? (value ? next(value) : undefined) : next;
        if (!candidate && next instanceof Function) return value;
        // A mutation response must identify its aggregate and the acknowledged
        // version. A malformed success has an unknown outcome and needs review.
        if (
          !candidate ||
          candidate.id !== id ||
          !Number.isSafeInteger(candidate.edit_version) ||
          candidate.edit_version < 1
        )
          throw new Error("Invalid Multipart acknowledgement");
        if (!acceptsEditingSnapshot(value, candidate, observedEpoch)) return value;
        accepted = true;
        return candidate;
      });
      if (accepted) refreshLibraryMetadata(client, session, { kind: "multipart", id });
      return accepted;
    },
    [client, id, isCurrent, observedEpoch, session],
  );
  return { publish, active: session === current, isCurrent };
}

/** Only a confirmed grouping deletion retires its detail and derived metadata. */
export async function deleteLibraryMultipart(client: QueryClient, id: number, session: number) {
  requireSessionVersion(session);
  await client.cancelQueries({ queryKey: queryKeys.multipartModel(id), exact: true });
  requireSessionVersion(session);
  await deleteMultipartModel(id);
  requireSessionVersion(session);
  await client.cancelQueries({ queryKey: queryKeys.multipartModel(id), exact: true });
  requireSessionVersion(session);
  client.removeQueries({ queryKey: queryKeys.multipartModel(id), exact: true });
  void client.invalidateQueries({ queryKey: settingsTrashKeys.rows });
  refreshLibraryMetadata(client, session);
}

/** Only a confirmed move retires the set's stale reads; the acknowledged detail is kept. */
export async function moveLibraryMultipart(
  client: QueryClient,
  source: { id: number } & EditingBase,
  collectionId: number | null,
  session: number,
) {
  requireSessionVersion(session);
  await client.cancelQueries({ queryKey: queryKeys.multipartModel(source.id), exact: true });
  requireSessionVersion(session);
  const moved = await moveMultipartModel(source.id, collectionId, {
    edit_epoch: source.edit_epoch,
    edit_version: source.edit_version,
  });
  requireSessionVersion(session);
  client.setQueryData(queryKeys.multipartModel(source.id), moved);
  refreshLibraryMetadata(client, session, { kind: "multipart", id: source.id });
  return moved;
}
