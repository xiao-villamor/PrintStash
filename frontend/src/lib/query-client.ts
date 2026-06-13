import { QueryClient } from "@tanstack/react-query";

/**
 * Single app-wide query cache.
 *
 * Defaults tuned for a self-hosted, multi-user (RBAC) dashboard:
 *  - `staleTime` 30s — matches the old in-memory TTL, so rapid re-renders and
 *    back-navigation reuse data instead of refetching.
 *  - `refetchOnWindowFocus` — when a user tabs back, shared data (collections,
 *    tags, …) silently revalidates, so another user's changes show up without
 *    a manual reload. This is the main freshness win over the old flat cache.
 *  - `gcTime` 5m — unobserved data is dropped after five minutes.
 *  - one retry — transient blips recover; hard failures surface quickly.
 *
 * Lives in its own module (no imports from the api layer) so `request.ts` can
 * import it to invalidate the cache on mutations without a circular import.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 5 * 60_000,
      refetchOnWindowFocus: true,
      retry: 1,
    },
  },
});
