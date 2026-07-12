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

// ---------------------------------------------------------------------------
// Query keys — one factory, mirroring the backend resource roots so keys stay
// consistent and invalidation can target a whole resource by prefix.
//
// Invalidating a prefix (e.g. ["models"]) matches every more specific key
// (["models", id], ["models", "list", params]) by React Query's default
// partial matching, so a single entry covers a resource's lists + details.
// ---------------------------------------------------------------------------
export const queryKeys = {
  models: ["models"] as const,
  model: (id: number) => ["models", id] as const,
  collections: ["collections"] as const,
  tags: ["tags"] as const,
  printers: ["printers"] as const,
  printer: (id: number) => ["printers", id] as const,
  filamentProfiles: ["filament-profiles"] as const,
  printerProfiles: ["printer-profiles"] as const,
  adminUsers: ["admin", "users"] as const,
  vaultStats: ["vault-stats"] as const,
  vaultConfig: ["vault-config"] as const,
  printStats: (period: string) => ["print-stats", period] as const,
  spoolmanStatus: ["spoolman", "status"] as const,
  spools: ["spoolman", "spools"] as const,
} as const;

/**
 * Refresh every vault read model after an asynchronous ingest job finishes.
 *
 * Upload POSTs return while ingestion is still queued, so request-level
 * invalidation happens too early. Cancelling any stale refetch started by that
 * POST before invalidating again prevents the pre-ingest result winning the
 * race with this completion refresh.
 */
export async function refreshVaultAfterIngest(): Promise<void> {
  const keys = [queryKeys.models, queryKeys.collections, queryKeys.vaultStats];
  await Promise.all(keys.map((queryKey) => queryClient.cancelQueries({ queryKey })));
  await Promise.all(keys.map((queryKey) => queryClient.invalidateQueries({ queryKey })));
}

/**
 * Invalidate the query keys a mutated API path can affect.
 *
 * Keyed (not blanket) invalidation: a collection/tag write also touches how
 * models are listed/labelled, so those fan out to ["models"]. Anything not
 * recognised here falls back to a full invalidation in `invalidateApiCache`.
 */
export function invalidateQueriesForPath(path: string): void {
  const bust = (queryKey: readonly unknown[]) =>
    queryClient.invalidateQueries({ queryKey });

  if (/\/collections(\/|$|\?)/.test(path)) {
    bust(queryKeys.collections);
    bust(queryKeys.models);
  }
  if (/\/tags(\/|$|\?)/.test(path)) {
    bust(queryKeys.tags);
    bust(queryKeys.models);
  }
  if (/\/(models|files|ingest|gcode)(\/|$|\?|-)/.test(path)) {
    bust(queryKeys.models);
    // Vault totals (count, size, material breakdown) are derived from models,
    // so any model/file write can change them.
    bust(queryKeys.vaultStats);
    // Collections carry a `model_count`; a move/delete/import shifts those
    // counts, so refresh the collection list (and its sidebar badges) too.
    bust(queryKeys.collections);
  }
  if (/\/printers(\/|$|\?)/.test(path)) {
    bust(queryKeys.printers);
  }
  if (/\/filament-profiles(\/|$|\?)/.test(path)) {
    bust(queryKeys.filamentProfiles);
  }
  if (/\/printer-profiles(\/|$|\?)/.test(path)) {
    bust(queryKeys.printerProfiles);
  }
  if (/\/admin\/users(\/|$|\?)/.test(path)) {
    bust(queryKeys.adminUsers);
  }
  if (/\/spoolman(\/|$|\?)/.test(path)) {
    bust(queryKeys.spoolmanStatus);
    bust(queryKeys.spools);
    // A filament sync rewrites linked presets.
    if (/\/spoolman\/sync-filaments/.test(path)) {
      bust(queryKeys.filamentProfiles);
    }
  }
}
