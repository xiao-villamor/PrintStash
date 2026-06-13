import { useQuery } from "@tanstack/react-query";

import { listCollections, listTags } from "@/lib/api";
import type { CollectionRead, TagRead } from "@/types";

/**
 * Query hooks for the shared, read-only taxonomy lists.
 *
 * These were previously fetched into local `useState` in ~5 places; now they
 * share one TanStack Query cache entry, dedupe in-flight requests, and
 * revalidate on window focus. Mutations go through the api layer, whose
 * `invalidateApiCache()` invalidates the whole query cache, so these refetch
 * automatically after a create/move/delete.
 *
 * The `queryFn`s pass `{ fresh: true }` to bypass the legacy in-memory cache in
 * `request.ts`, making TanStack Query the single source of truth for them.
 */

export const queryKeys = {
  collections: ["collections"] as const,
  tags: ["tags"] as const,
};

export function useCollections() {
  return useQuery<CollectionRead[]>({
    queryKey: queryKeys.collections,
    queryFn: () => listCollections({ fresh: true }),
  });
}

export function useTags() {
  return useQuery<TagRead[]>({
    queryKey: queryKeys.tags,
    queryFn: () => listTags({ fresh: true }),
  });
}
