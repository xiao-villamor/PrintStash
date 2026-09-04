import { createContext, useContext } from "react";
import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";
import type { InfiniteData, QueryKey } from "@tanstack/react-query";

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
  listModelPage,
  listMultipartModelCandidates,
  listMultipartModels,
  getMultipartModel,
  listOutlinerModels,
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
  ModelPageRead,
  ModelSort,
  ModelFacetsRead,
  MultipartModelCandidate,
  MultipartModelListItem,
  MultipartModelRead,
  OutlinerModelRead,
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

/**
 * The api-layer reads these hooks depend on, gathered into one collaborator.
 *
 * Every production render uses `defaultQueryApi` — the context default — so no
 * provider is required and the call sites stay `useCollections()`. Tests wrap
 * the tree in `QueryApiProvider` to drive the hooks against an in-memory
 * implementation instead of intercepting this module's imports.
 */
export const defaultQueryApi = {
  getDashboard,
  getFleetSummary,
  getModelFacets,
  getPrintStatistics,
  getSpoolmanStatus,
  getVaultConfig,
  getVaultStats,
  listCollections,
  listFilamentProfiles,
  listFleetQueue,
  listModelPage,
  listMultipartModelCandidates,
  listMultipartModels,
  getMultipartModel,
  listOutlinerModels,
  listPrinterProfiles,
  listPrinters,
  listSpools,
  listTags,
};

export type QueryApi = typeof defaultQueryApi;

const QueryApiContext = createContext<QueryApi>(defaultQueryApi);

/** Swaps the api implementation the hooks below call. */
export const QueryApiProvider = QueryApiContext.Provider;

function useQueryApi(): QueryApi {
  return useContext(QueryApiContext);
}

export function useCollections() {
  const api = useQueryApi();
  return useQuery<CollectionRead[]>({
    queryKey: queryKeys.collections,
    queryFn: () => api.listCollections({ fresh: true }),
  });
}

export function useTags() {
  const api = useQueryApi();
  return useQuery<TagRead[]>({
    queryKey: queryKeys.tags,
    queryFn: () => api.listTags({ fresh: true }),
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
  const api = useQueryApi();
  return useQuery<PrinterRead[]>({
    queryKey: queryKeys.printers,
    queryFn: () => api.listPrinters(undefined, { fresh: true }),
    enabled: options?.enabled ?? true,
    refetchInterval: options?.refetchInterval,
  });
}

export function usePrinterDashboard(options?: { enabled?: boolean; refetchInterval?: number }) {
  const api = useQueryApi();
  return useQuery<Dashboard>({
    queryKey: queryKeys.printerDashboard,
    queryFn: () => api.getDashboard({ fresh: true }),
    enabled: options?.enabled ?? true,
    refetchInterval: options?.refetchInterval,
  });
}

export function useFleetQueue(options?: { refetchInterval?: number; historyLimit?: number }) {
  const api = useQueryApi();
  const historyLimit = options?.historyLimit ?? 20;
  return useQuery<PrintJobRead[]>({
    queryKey: [...queryKeys.fleetQueue, historyLimit],
    queryFn: () => api.listFleetQueue(historyLimit),
    refetchInterval: options?.refetchInterval,
  });
}

export function useFleetSummary(options?: { refetchInterval?: number }) {
  const api = useQueryApi();
  return useQuery<FleetSummary>({
    queryKey: queryKeys.fleetSummary,
    queryFn: api.getFleetSummary,
    refetchInterval: options?.refetchInterval,
  });
}

export function usePrinterProfiles() {
  const api = useQueryApi();
  return useQuery<PrinterProfileRead[]>({
    queryKey: queryKeys.printerProfiles,
    queryFn: () => api.listPrinterProfiles({ fresh: true }),
  });
}

export function useFilamentProfiles() {
  const api = useQueryApi();
  return useQuery<FilamentProfileRead[]>({
    queryKey: queryKeys.filamentProfiles,
    queryFn: () => api.listFilamentProfiles({ fresh: true }),
  });
}

export function useVaultStats() {
  const api = useQueryApi();
  return useQuery<VaultStatsRead>({
    queryKey: queryKeys.vaultStats,
    queryFn: () => api.getVaultStats({ fresh: true }),
  });
}

export interface MultipartModelListFilters {
  collection?: string;
  direct?: boolean;
  q?: string;
  tag?: string[];
  favorites?: boolean;
  limit?: number;
  offset?: number;
}

export function useMultipartModels(
  filters?: MultipartModelListFilters,
  options?: { enabled?: boolean },
) {
  const api = useQueryApi();
  return useQuery<MultipartModelListItem[]>({
    queryKey: [...queryKeys.multipartModels, "list", filters ?? {}],
    queryFn: () => api.listMultipartModels(filters),
    enabled: options?.enabled,
  });
}

export function useMultipartModel(id: number | null) {
  const api = useQueryApi();
  return useQuery<MultipartModelRead>({
    queryKey:
      id === null
        ? [...queryKeys.multipartModels, "detail", "empty"]
        : queryKeys.multipartModel(id),
    queryFn: () => {
      if (id === null) return Promise.reject(new Error("Multipart model id is required"));
      return api.getMultipartModel(id);
    },
    enabled: id !== null,
  });
}

export function useMultipartModelCandidates(
  id: number | null,
  query: string,
  options?: { enabled?: boolean },
) {
  const api = useQueryApi();
  return useQuery<MultipartModelCandidate[]>({
    queryKey:
      id === null
        ? [...queryKeys.multipartModels, "candidates", "empty"]
        : queryKeys.multipartCandidates(id, query),
    queryFn: () => {
      if (id === null) return Promise.reject(new Error("Multipart model id is required"));
      return api.listMultipartModelCandidates(id, { q: query, limit: 50 });
    },
    enabled: id !== null && (options?.enabled ?? true),
  });
}

export function usePrintStatistics(period: StatsPeriod) {
  const api = useQueryApi();
  return useQuery<PrintStatisticsRead>({
    queryKey: queryKeys.printStats(period),
    queryFn: () => api.getPrintStatistics(period, { fresh: true }),
  });
}

export function useVaultConfig() {
  const api = useQueryApi();
  return useQuery<VaultConfigRead>({
    queryKey: queryKeys.vaultConfig,
    queryFn: () => api.getVaultConfig(),
  });
}

export function useSpoolmanStatus(options?: { enabled?: boolean }) {
  const api = useQueryApi();
  return useQuery<SpoolmanStatus>({
    queryKey: queryKeys.spoolmanStatus,
    queryFn: () => api.getSpoolmanStatus(),
    enabled: options?.enabled ?? true,
  });
}

/** Spoolman inventory. Only fetched when the integration is enabled. */
export function useSpools(options?: { enabled?: boolean }) {
  const api = useQueryApi();
  return useQuery<SpoolRead[]>({
    queryKey: queryKeys.spools,
    queryFn: () => api.listSpools(),
    enabled: options?.enabled ?? true,
  });
}

/** Filters that key the model-list query (everything but pagination). */
export type ModelListFilters = Omit<ListModelsParams, "limit" | "offset">;

/** Facet counts stay mounted while a changed filter set is recomputed. */
export function useModelFacets(filters: ModelListFilters) {
  const api = useQueryApi();
  return useQuery<ModelFacetsRead>({
    queryKey: [...queryKeys.models, "facets", filters],
    queryFn: () => api.getModelFacets(filters),
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
/** Opaque page cursor as issued by the API; `null` requests the first page. */
type ModelPageCursor = ModelPageRead["next_cursor"];

export function useModelList(filters: ModelListFilters, pageSize: number, sort: ModelSort) {
  const api = useQueryApi();
  return useInfiniteQuery<
    ModelPageRead,
    Error,
    InfiniteData<ModelPageRead>,
    QueryKey,
    ModelPageCursor
  >({
    queryKey: [...queryKeys.models, "list", filters, sort],
    queryFn: ({ pageParam }) =>
      api.listModelPage({
        ...filters,
        limit: pageSize,
        sort,
        cursor: pageParam ?? undefined,
      }),
    initialPageParam: null,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    placeholderData: keepPreviousData,
  });
}

/**
 * Flat, unpaginated model list that feeds the outliner tree. Mirrors the active
 * tag/printer filters but ignores the search query and pagination, so the tree
 * keeps showing every matching leaf.
 */
export function useOutlinerModels(
  filters: ModelListFilters,
  limit: number,
  options?: { enabled?: boolean },
) {
  const api = useQueryApi();
  return useQuery<OutlinerModelRead[]>({
    queryKey: [...queryKeys.models, "outliner", filters, limit],
    queryFn: () => api.listOutlinerModels({ ...filters, limit }),
    enabled: options?.enabled ?? true,
    placeholderData: keepPreviousData,
  });
}
