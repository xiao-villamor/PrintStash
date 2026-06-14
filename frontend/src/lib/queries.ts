import { useQuery } from "@tanstack/react-query";

import {
  getVaultStats,
  listCollections,
  listFilamentProfiles,
  listPrinterProfiles,
  listPrinters,
  listTags,
} from "@/lib/api";
import { queryKeys } from "@/lib/query-client";
import type {
  CollectionRead,
  FilamentProfileRead,
  PrinterProfileRead,
  PrinterRead,
  TagRead,
  VaultStatsRead,
} from "@/types";

/**
 * Query hooks for the shared, read-only taxonomy lists.
 *
 * These were previously fetched into local `useState` in ~5 places; now they
 * share one TanStack Query cache entry, dedupe in-flight requests, and
 * revalidate on window focus. Mutations go through the api layer, whose keyed
 * invalidation (`invalidateQueriesForPath`) busts these after a
 * create/move/delete, so they refetch automatically.
 *
 * The `queryFn`s pass `{ fresh: true }` to bypass the legacy in-memory cache in
 * `request.ts`, making TanStack Query the single source of truth for them.
 */

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

/**
 * Same shared-cache treatment for the other read-mostly resources that were
 * each fetched into local `useState` per component. Mutations through the api
 * layer invalidate these by key (see `invalidateQueriesForPath`), so a printer
 * added on one screen shows up on every other without a manual reload.
 *
 * `fresh: true` bypasses the legacy in-memory cache in `request.ts` so TanStack
 * Query stays the single source of truth, matching `useCollections`/`useTags`.
 */
export function usePrinters(options?: { enabled?: boolean }) {
  return useQuery<PrinterRead[]>({
    queryKey: queryKeys.printers,
    queryFn: () => listPrinters(undefined, { fresh: true }),
    enabled: options?.enabled ?? true,
  });
}

export function usePrinterProfiles() {
  return useQuery<PrinterProfileRead[]>({
    queryKey: queryKeys.printerProfiles,
    queryFn: () => listPrinterProfiles({ fresh: true }),
  });
}

export function useFilamentProfiles() {
  return useQuery<FilamentProfileRead[]>({
    queryKey: queryKeys.filamentProfiles,
    queryFn: () => listFilamentProfiles({ fresh: true }),
  });
}

export function useVaultStats() {
  return useQuery<VaultStatsRead>({
    queryKey: queryKeys.vaultStats,
    queryFn: () => getVaultStats({ fresh: true }),
  });
}
