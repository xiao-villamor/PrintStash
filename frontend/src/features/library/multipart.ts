import { acceptsEditingSnapshot } from "./editing";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { queryOptions, useQueryClient } from "@tanstack/react-query";
import { getMultipartModel } from "@/lib/api/multipart-models";
import { onAuthChange } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";
import { queryKeys } from "@/lib/query-client";
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
      return accepted;
    },
    [client, id, isCurrent, observedEpoch],
  );
  return { publish, active: session === current, isCurrent };
}
