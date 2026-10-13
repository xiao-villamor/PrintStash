import { useCallback, useEffect, useRef, useState } from "react";
import { queryOptions, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { getMultipartBuild, listMultipartBuilds } from "@/lib/api/multipart-builds";
import { getSessionVersion } from "@/lib/session-transport";
import { queryKeys } from "@/lib/query-client";
import type { MultipartBuild } from "@/types/multipart-builds";

const lists = ["multipart-builds", "list"] as const;
const detail = (id: number) => ["multipart-builds", "detail", id] as const;

export function buildListOptions(archived: boolean, offset: number) {
  return queryOptions({
    queryKey: [...lists, archived, offset],
    queryFn: ({ signal }) => listMultipartBuilds(archived, offset, { signal }),
    staleTime: 0,
    retry: false,
  });
}

function latestBuild(current: MultipartBuild | undefined, incoming: MultipartBuild) {
  if (!Number.isSafeInteger(incoming.version) || incoming.version < 0)
    throw new Error("Invalid Build version");
  return current && current.version > incoming.version ? current : incoming;
}

export function buildDetailOptions(id: number, client: QueryClient) {
  return queryOptions({
    queryKey: detail(id),
    queryFn: async ({ signal }) => {
      const build = await getMultipartBuild(id, { signal });
      if (build.id !== id) throw new Error("Invalid Build identity");
      return latestBuild(client.getQueryData<MultipartBuild>(detail(id)), build);
    },
    // Query pauses interval reads in hidden tabs and retires them with the observer.
    refetchInterval: (query) => (query.state.status === "error" ? false : 5_000),
    staleTime: 0,
    retry: false,
  });
}

/** Build receipts replace the shared detail only after obsolete reads retire.
 * A route/session departure prevents subsequent UI publication or commands.
 */
export function useBuildCommands() {
  const client = useQueryClient();
  const [session] = useState(getSessionVersion);
  const mounted = useRef(true);
  const pending = useRef(false);
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
  const run = useCallback(
    async (
      operation: () => Promise<MultipartBuild>,
      expectedId?: number,
    ): Promise<MultipartBuild | null> => {
      if (!isCurrent() || pending.current) return null;
      pending.current = true;
      try {
        if (expectedId !== undefined)
          await client.cancelQueries({ queryKey: detail(expectedId), exact: true });
        if (!isCurrent()) return null;
        const receipt = await operation();
        if (!isCurrent()) return null;
        if (
          !Number.isSafeInteger(receipt.id) ||
          receipt.id < 1 ||
          (expectedId !== undefined && receipt.id !== expectedId)
        )
          throw new Error("Invalid Build acknowledgement");
        await client.cancelQueries({ queryKey: detail(receipt.id), exact: true });
        if (!isCurrent()) return null;
        client.setQueryData<MultipartBuild>(detail(receipt.id), (current) =>
          latestBuild(current, receipt),
        );
        void client.invalidateQueries({ queryKey: lists });
        void client.invalidateQueries({ queryKey: queryKeys.fleetQueue });
        void client.invalidateQueries({ queryKey: queryKeys.fleetSummary });
        return receipt;
      } finally {
        pending.current = false;
      }
    },
    [client, isCurrent],
  );
  return { run, isCurrent };
}
