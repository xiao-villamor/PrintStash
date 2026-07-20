import {
  keepPreviousData,
  useInfiniteQuery,
  useQuery,
} from "@tanstack/react-query";

import {
  getPrintStatistics,
  getDashboard,
  getFleetSummary,
  getModelFacets,
  getSpoolmanStatus,
  getVaultConfig,
  getVaultStats,
  listCollections,
  listFilamentProfiles,
  listFleetQueue,
  listModels,
  listPrinterProfiles,
  listPrinters,
  listSpools,
  listTags,
  type StatsPeriod,
} from "@/lib/api";
import { queryKeys } from "@/lib/query-client";
import type {
  CollectionRead,
  Dashboard,
  FleetSummary,
  FilamentProfileRead,
  ListModelsParams,
  ModelListItem,
  ModelFacetsRead,
  PrinterProfileRead,
  PrinterRead,
  PrintJobRead,
  PrintStatisticsRead,
  SpoolmanStatus,
  SpoolRead,
  TagRead,
  VaultConfigRead,
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
export function usePrinters(options?: { enabled?: boolean; refetchInterval?: number }) {
  return useQuery<PrinterRead[]>({
    queryKey: queryKeys.printers,
    queryFn: () => listPrinters(undefined, { fresh: true }),
    enabled: options?.enabled ?? true,
    refetchInterval: options?.refetchInterval,
  });
}

export function usePrinterDashboard(options?: { enabled?: boolean; refetchInterval?: number }) {
  return useQuery<Dashboard>({
    queryKey: queryKeys.printerDashboard,
    queryFn: () => getDashboard({ fresh: true }),
    enabled: options?.enabled ?? true,
    refetchInterval: options?.refetchInterval,
  });
}

export function useFleetQueue(options?: { refetchInterval?: number; historyLimit?: number }) {
  const historyLimit = options?.historyLimit ?? 20;
  return useQuery<PrintJobRead[]>({
    queryKey: [...queryKeys.fleetQueue, historyLimit],
    queryFn: () => listFleetQueue(historyLimit),
    refetchInterval: options?.refetchInterval,
  });
}

export function useFleetSummary(options?: { refetchInterval?: number }) {
  return useQuery<FleetSummary>({
    queryKey: queryKeys.fleetSummary,
    queryFn: getFleetSummary,
    refetchInterval: options?.refetchInterval,
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

export function usePrintStatistics(period: StatsPeriod) {
  return useQuery<PrintStatisticsRead>({
    queryKey: queryKeys.printStats(period),
    queryFn: () => getPrintStatistics(period, { fresh: true }),
  });
}

export function useVaultConfig() {
  return useQuery<VaultConfigRead>({
    queryKey: queryKeys.vaultConfig,
    queryFn: () => getVaultConfig(),
  });
}

export function useSpoolmanStatus(options?: { enabled?: boolean }) {
  return useQuery<SpoolmanStatus>({
    queryKey: queryKeys.spoolmanStatus,
    queryFn: () => getSpoolmanStatus(),
    enabled: options?.enabled ?? true,
  });
}

/** Spoolman inventory. Only fetched when the integration is enabled. */
export function useSpools(options?: { enabled?: boolean }) {
  return useQuery<SpoolRead[]>({
    queryKey: queryKeys.spools,
    queryFn: () => listSpools(),
    enabled: options?.enabled ?? true,
  });
}

/** Filters that key the model-list query (everything but pagination). */
export type ModelListFilters = Omit<ListModelsParams, "limit" | "offset">;

/** Facet counts stay mounted while a changed filter set is recomputed. */
export function useModelFacets(filters: ModelListFilters) {
  return useQuery<ModelFacetsRead>({
    queryKey: [...queryKeys.models, "facets", filters],
    queryFn: () => getModelFacets(filters),
    placeholderData: keepPreviousData,
  });
}

/**
 * Paginated model grid, cached and keyed by its filters.
 *
 * Replaces the old hand-rolled `useEffect` + debounce + manual loading/`hasMore`
 * bookkeeping. Two wins for search responsiveness:
 *  - `placeholderData: keepPreviousData` keeps the current results on screen
 *    while the next query loads, so typing/clearing a search no longer blanks
 *    the grid (the "clunky" flash).
 *  - Results are cached per filter set, so backspacing to a query you just ran
 *    (or revisiting a folder) is instant instead of a fresh round-trip.
 *
 * Mutations invalidate `["models"]` via `invalidateQueriesForPath`, which by
 * prefix-matching also busts every keyed list here.
 */
export function useModelList(filters: ModelListFilters, pageSize: number) {
  return useInfiniteQuery<ModelListItem[]>({
    queryKey: [...queryKeys.models, "list", filters],
    queryFn: ({ pageParam }) =>
      listModels({ ...filters, limit: pageSize, offset: pageParam as number }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) =>
      lastPage.length === pageSize ? allPages.length * pageSize : undefined,
    placeholderData: keepPreviousData,
  });
}

/**
 * Flat, unpaginated model list that feeds the outliner tree. Mirrors the active
 * tag/printer filters but ignores the search query and pagination, so the tree
 * keeps showing every matching leaf.
 */
export function useOutlinerModels(filters: ModelListFilters, limit: number) {
  return useQuery<ModelListItem[]>({
    queryKey: [...queryKeys.models, "outliner", filters, limit],
    queryFn: () => listModels({ ...filters, limit }),
    placeholderData: keepPreviousData,
  });
}
