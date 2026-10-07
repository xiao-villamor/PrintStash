import { QueryClient } from "@tanstack/react-query";
import { onAuthChange } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";

import type { CollectionRole } from "@/types";

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
 * Feature command owners declare invalidation; transport never imports this module.
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

// Authentication retires private Query state independently of HTTP imports.
onAuthChange(() => queryClient.clear());

// ---------------------------------------------------------------------------
// Query keys — one factory, mirroring the backend resource roots so keys stay
// consistent and invalidation can target a whole resource by prefix.
//
// Invalidating a prefix (e.g. ["models"]) matches every more specific key
// (["models", id], ["models", "list", params]) by React Query's default
// partial matching, so a single entry covers a resource's lists + details.
// ---------------------------------------------------------------------------
export const queryKeys = {
  outliner: ["outliner"] as const,
  models: ["models"] as const,
  model: (id: number) => ["models", id] as const,
  multipartModels: ["multipart-models"] as const,
  multipartModel: (id: number) => ["multipart-models", id] as const,
  multipartCandidates: (id: number, q: string) =>
    ["multipart-models", id, "candidates", q] as const,
  collections: ["collections"] as const,
  // Under the collections root, so a readme write's `collections` invalidation
  // refreshes it together with the list's `has_readme` flag.
  collectionReadme: (id: number) => ["collections", id, "readme"] as const,
  // The lazily loaded tree, under the same root: any collection write that
  // invalidates `collections` refreshes every loaded level, lookup and search.
  collectionChildren: (parentId: number | null) => ["collections", "children", parentId] as const,
  collectionLookup: (path: string | null) => ["collections", "lookup", path] as const,
  collectionLookupById: (id: number | null) => ["collections", "lookup-id", id] as const,
  collectionSearch: (query: string, minRole: CollectionRole) =>
    ["collections", "search", query, minRole] as const,
  tags: ["tags"] as const,
  printers: ["printers"] as const,
  printerDashboard: ["printers", "dashboard"] as const,
  fleetQueue: ["fleet", "queue"] as const,
  fleetSummary: ["fleet", "summary"] as const,
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
  const session = getSessionVersion();
  const keys = [
    queryKeys.models,
    queryKeys.collections,
    queryKeys.vaultStats,
    queryKeys.multipartModels,
  ];
  await Promise.all(keys.map((queryKey) => queryClient.cancelQueries({ queryKey })));
  // Cancellation can yield across logout or an access-scope replacement.
  if (session !== getSessionVersion()) return;
  await Promise.all([
    queryClient.resetQueries({ queryKey: queryKeys.outliner }),
    ...keys.map((queryKey) => queryClient.invalidateQueries({ queryKey })),
  ]);
}
