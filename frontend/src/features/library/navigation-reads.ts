import { useCallback, useEffect, useRef } from "react";
import { hashKey, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { collectionLookupOptions } from "@/lib/queries";
import type { LibraryBrowseParams } from "@/types/library-browse";
import { libraryBrowseOptions } from "./browse";

/** Start the chosen destination's reads before Router renders its pending view. */
export function useLibraryNavigationReads() {
  const client = useQueryClient();
  const pending = useRef<QueryKey[]>([]);
  const cancel = useCallback(
    (retained: ReadonlySet<string> = new Set()) => {
      for (const key of pending.current) {
        if (retained.has(hashKey(key))) continue;
        const query = client.getQueryCache().find({ queryKey: key, exact: true });
        // Once mounted, the destination's Query observer owns cancellation.
        if (query && query.getObserversCount() === 0)
          void client.cancelQueries({ queryKey: key, exact: true });
      }
      pending.current = [];
    },
    [client],
  );
  useEffect(() => cancel, [cancel]);
  return useCallback(
    (path: string | null, params: LibraryBrowseParams) => {
      const browse = libraryBrowseOptions(params);
      const lookup = path === null ? null : collectionLookupOptions(path, client);
      const keys = [browse.queryKey, ...(lookup ? [lookup.queryKey] : [])];
      cancel(new Set(keys.map(hashKey)));
      pending.current = keys;
      void client.prefetchInfiniteQuery(browse);
      if (lookup) void client.prefetchQuery(lookup);
    },
    [client, cancel],
  );
}
