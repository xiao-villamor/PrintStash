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
  multipartModels: ["multipart-models"] as const,
  multipartModel: (id: number) => ["multipart-models", id] as const,
  multipartCandidates: (id: number, q: string) =>
    ["multipart-models", id, "candidates", q] as const,
  collections: ["collections"] as const,
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
  const keys = [
    queryKeys.models,
    queryKeys.collections,
    queryKeys.vaultStats,
    queryKeys.multipartModels,
  ];
  await Promise.all(keys.map((queryKey) => queryClient.cancelQueries({ queryKey })));
  await Promise.all(keys.map((queryKey) => queryClient.invalidateQueries({ queryKey })));
}

/**
 * Invalidate the query keys a mutated API path can affect.
 *
 * Keyed (not blanket) invalidation: a collection/tag write also touches how
 * models are listed/labelled, so those fan out to ["models"]. An unrecognised
 * path is intentionally left alone; callers that do not know the affected
 * resource can omit the path and request a full invalidation instead.
 */
type ApiMethod = "GET" | "HEAD" | "OPTIONS" | "POST" | "PUT" | "PATCH" | "DELETE";

/**
 * Invalidate read models affected by a successful API mutation.
 *
 * `request.ts` calls this only for writes. Keeping the method argument here is
 * useful for callers that work with a generic transport and, importantly,
 * makes an accidental GET invalidation a no-op. Resource names are matched as
 * path segments, never as substrings: `/printer-profiles` must not be treated
 * as `/printers`, and `/multipart-models` is not the only write that can make
 * a multipart view stale.
 */
export function invalidateQueriesForPath(path: string, method: ApiMethod = "POST"): void {
  if (method === "GET" || method === "HEAD" || method === "OPTIONS") return;

  const segments = path.split(/[/?#]/).filter(Boolean);
  const has = (...names: string[]) => names.some((name) => segments.includes(name));
  const invalidated = new Set<string>();
  const bust = (queryKey: readonly unknown[]) => {
    const identity = JSON.stringify(queryKey);
    if (invalidated.has(identity)) return;
    invalidated.add(identity);
    void queryClient.invalidateQueries({ queryKey });
  };

  const multipartAffected = () => {
    // The root key is a prefix, so this refreshes list, detail, and candidate
    // queries together (including entries with query parameters).
    bust(queryKeys.multipartModels);
  };

  if (has("collections")) {
    bust(queryKeys.collections);
    bust(queryKeys.models);
    multipartAffected();
  }
  if (has("tags")) {
    bust(queryKeys.tags);
    bust(queryKeys.models);
    multipartAffected();
  }
  if (
    has(
      "models",
      "files",
      "artifacts",
      "ingest",
      "gcode",
      "gcode-revision",
      "gcode-revisions",
      "revisions",
    )
  ) {
    bust(queryKeys.models);
    // Vault totals (count, size, material breakdown) are derived from models,
    // so any model/file write can change them.
    bust(queryKeys.vaultStats);
    // Collections carry a `model_count`; a move/delete/import shifts those
    // counts, so refresh the collection list (and its sidebar badges) too.
    bust(queryKeys.collections);
    multipartAffected();
  }
  if (has("trash", "restore", "purge", "gc")) {
    // Trash routes can operate on Models, Files, or collections. These reads
    // are cheap and keeping them in sync also covers restore/purge actions
    // whose route does not include the affected resource name.
    bust(queryKeys.models);
    bust(queryKeys.collections);
    bust(queryKeys.vaultStats);
    multipartAffected();
  }
  if (has("multipart-models")) {
    multipartAffected();
  }
  if (has("printers")) {
    bust(queryKeys.printers);
  }
  if (has("fleet")) {
    bust(queryKeys.fleetQueue);
    bust(queryKeys.fleetSummary);
    bust(queryKeys.printers);
  }
  if (has("filament-profiles")) {
    bust(queryKeys.filamentProfiles);
  }
  if (has("printer-profiles")) {
    bust(queryKeys.printerProfiles);
  }
  if (segments.includes("admin") && segments.includes("users")) {
    bust(queryKeys.adminUsers);
  }
  if (has("spoolman")) {
    bust(queryKeys.spoolmanStatus);
    bust(queryKeys.spools);
    // A filament sync rewrites linked presets.
    if (segments.includes("sync-filaments")) {
      bust(queryKeys.filamentProfiles);
    }
  }
}
