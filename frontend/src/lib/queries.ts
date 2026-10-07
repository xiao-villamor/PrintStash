import { multipartDetailOptions } from "@/features/library/multipart";
import { vaultConfigOptions } from "@/lib/queries/settings-config";
import { filamentProfilesOptions, printerProfilesOptions } from "@/lib/queries/profiles";
import { printStatisticsOptions } from "@/lib/queries/statistics";
import { markStartup } from "@/lib/startup-timing";

import { listOutlinerCollections, listOutlinerEntries, searchOutliner } from "@/lib/api/outliner";
import type { OutlinerParams } from "@/types/outliner";
import { createContext, useContext, useMemo } from "react";
import {
  infiniteQueryOptions,
  keepPreviousData,
  queryOptions,
  useInfiniteQuery,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import type { InfiniteData, QueryKey } from "@tanstack/react-query";

import {
  getCollectionReadme,
  getPrintStatistics,
  getDashboard,
  getFleetSummary,
  getModelFacets,
  getSpoolmanStatus,
  getVaultConfig,
  getVaultStats,
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
  listCollectionChildren,
  lookupCollection,
  lookupCollectionById,
  searchCollections,
  type StatsPeriod,
} from "@/lib/api";
import { queryKeys } from "@/lib/query-client";
import type {
  CollectionLookupRead,
  CollectionPage,
  CollectionRole,
  Dashboard,
  FleetSummary,
  ListModelsParams,
  ModelPageRead,
  ModelSort,
  ModelFacetsRead,
  MultipartModelCandidate,
  MultipartModelListItem,
  OutlinerModelRead,
  PrintJobRead,
  SpoolmanStatus,
  SpoolRead,
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
 * JSON transport always reads the network; Query owns reuse and freshness.
 */

/**
 * The api-layer reads these hooks depend on, gathered into one collaborator.
 *
 * Every production render uses `defaultQueryApi` — the context default — so no
 * provider is required. Tests wrap
 * the tree in `QueryApiProvider` to drive the hooks against an in-memory
 * implementation instead of intercepting this module's imports.
 */
export const defaultQueryApi = {
  listOutlinerCollections,
  listOutlinerEntries,
  searchOutliner,
  getCollectionReadme,
  getDashboard,
  getFleetSummary,
  getModelFacets,
  getPrintStatistics,
  getSpoolmanStatus,
  getVaultConfig,
  getVaultStats,
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
  listCollectionChildren,
  lookupCollection,
  lookupCollectionById,
  searchCollections,
};

export type QueryApi = typeof defaultQueryApi;

const QueryApiContext = createContext<QueryApi>(defaultQueryApi);

/** Swaps the api implementation the hooks below call. */
export const QueryApiProvider = QueryApiContext.Provider;

function useQueryApi(): QueryApi {
  return useContext(QueryApiContext);
}

function collectionReadmeOptions(api: QueryApi, collectionId: number) {
  return queryOptions<string | null>({
    queryKey: queryKeys.collectionReadme(collectionId),
    queryFn: async ({ signal }) => (await api.getCollectionReadme(collectionId, { signal })).readme,
  });
}

/**
 * One level of the collection tree, a page at a time: `parentId` null is the
 * caller's top level. Loaded only while `enabled` (a row that is open), so the
 * tree fetches what the user expands and nothing else.
 */
export function useCollectionChildren(parentId: number | null, options?: { enabled?: boolean }) {
  const api = useQueryApi();
  return useInfiniteQuery({
    queryKey: queryKeys.collectionChildren(parentId),
    queryFn: ({ pageParam, signal }) =>
      api.listCollectionChildren(parentId, pageParam, undefined, { signal }),
    initialPageParam: null,
    getNextPageParam: (page: CollectionPage) => page.next_cursor,
    enabled: options?.enabled ?? true,
  });
}

/** The collection at `path` and its ancestors; idle while `path` is null. */
export function useCollectionLookup(path: string | null) {
  const api = useQueryApi();
  return useQuery<CollectionLookupRead>({
    queryKey: queryKeys.collectionLookup(path),
    queryFn: ({ signal }) => {
      if (path === null || path === "") throw new Error("Collection lookup requires a path");
      return api.lookupCollection(path, { signal });
    },
    enabled: path !== null && path !== "",
    placeholderData: keepPreviousData,
  });
}

/** A saved collection id and its named path; idle while no id is selected. */
export function useCollectionLookupById(id: number | null) {
  const api = useQueryApi();
  return useQuery<CollectionLookupRead>({
    queryKey: queryKeys.collectionLookupById(id),
    queryFn: ({ signal }) => {
      if (id === null) throw new Error("Collection lookup requires an id");
      return api.lookupCollectionById(id, { signal });
    },
    enabled: id !== null,
  });
}

/**
 * Collections whose name contains `query`, held at `minRole` or above — for
 * pickers and the tree's name filter. The caller debounces `query`.
 */
export function useCollectionSearch(
  query: string,
  minRole: CollectionRole = "view",
  options?: { enabled?: boolean },
) {
  const api = useQueryApi();
  return useInfiniteQuery({
    queryKey: queryKeys.collectionSearch(query, minRole),
    queryFn: ({ pageParam, signal }) =>
      api.searchCollections(query, minRole, pageParam, undefined, { signal }),
    initialPageParam: null,
    getNextPageParam: (page: CollectionPage) => page.next_cursor,
    enabled: options?.enabled ?? true,
    placeholderData: keepPreviousData,
  });
}

/**
 * A folder's readme, cached per folder so revisiting one costs no request.
 * Disable it for a folder whose `has_readme` is false — most have none.
 */
export function useCollectionReadme(collectionId: number, options?: { enabled?: boolean }) {
  const api = useQueryApi();
  return useQuery({
    ...collectionReadmeOptions(api, collectionId),
    enabled: options?.enabled ?? true,
  });
}

export function useTags(options?: { enabled?: boolean }) {
  const api = useQueryApi();
  return useQuery<TagRead[]>({
    queryKey: queryKeys.tags,
    enabled: options?.enabled,
    queryFn: () => api.listTags({}),
  });
}

/**
 * Same shared-cache treatment for the other read-mostly resources that were
 * each fetched into local `useState` per component. Mutations through the api
 * layer invalidate these by key (see `invalidateQueriesForPath`), so a printer
 * added on one screen shows up on every other without a manual reload.
 *
 * Query is the sole freshness owner; the transport consumes the signal.
 * TanStack
 * Query stays the single source of truth, matching the other taxonomy hooks.
 */
export function printersOptions(api: Pick<QueryApi, "listPrinters"> = defaultQueryApi) {
  return queryOptions({
    queryKey: queryKeys.printers,
    queryFn: ({ signal }) => api.listPrinters(undefined, { signal }),
  });
}

export function usePrinters(options?: { enabled?: boolean; refetchInterval?: number }) {
  const api = useQueryApi();
  return useQuery({
    ...printersOptions(api),
    enabled: options?.enabled ?? true,
    refetchInterval: options?.refetchInterval,
  });
}

export function usePrinterDashboard(options?: { enabled?: boolean; refetchInterval?: number }) {
  const api = useQueryApi();
  return useQuery<Dashboard>({
    queryKey: queryKeys.printerDashboard,
    queryFn: () => api.getDashboard({}),
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

// Migrated option owners retain this injected facade until the M10 caller cutover.
export function usePrinterProfiles() {
  const api = useQueryApi();
  return useQuery(printerProfilesOptions(api.listPrinterProfiles));
}

export function useFilamentProfiles() {
  const api = useQueryApi();
  return useQuery(filamentProfilesOptions(api.listFilamentProfiles));
}

export function useVaultStats() {
  const api = useQueryApi();
  return useQuery<VaultStatsRead>({
    queryKey: queryKeys.vaultStats,
    queryFn: () => api.getVaultStats({}),
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

function multipartModelsOptions(api: QueryApi, filters?: MultipartModelListFilters) {
  return queryOptions<MultipartModelListItem[]>({
    queryKey: [...queryKeys.multipartModels, "list", filters ?? {}],
    queryFn: () => api.listMultipartModels(filters),
  });
}

/**
 * Multipart list keyed by its filters. Like `useModelList`, it keeps the
 * previous result on screen while a new folder or search loads, so switching
 * folders never drops the grid back to its first-load skeleton.
 */
export function useMultipartModels(
  filters?: MultipartModelListFilters,
  options?: { enabled?: boolean },
) {
  const api = useQueryApi();
  return useQuery({
    ...multipartModelsOptions(api, filters),
    enabled: options?.enabled,
    placeholderData: keepPreviousData,
  });
}

export function useMultipartModel(id: number | null) {
  const api = useQueryApi();
  return useQuery(multipartDetailOptions(id, api.getMultipartModel));
}

export const MULTIPART_CANDIDATE_PAGE_SIZE = 48;

export function useMultipartModelCandidates(
  id: number | null,
  query: string,
  options?: { enabled?: boolean; collection?: string; offset?: number; direct?: boolean },
) {
  const api = useQueryApi();
  return useQuery<MultipartModelCandidate[]>({
    queryKey:
      id === null
        ? [...queryKeys.multipartModels, "candidates", "empty"]
        : [
            ...queryKeys.multipartCandidates(id, query),
            options?.collection,
            options?.offset ?? 0,
            options?.direct ?? false,
          ],
    queryFn: () => {
      if (id === null) return Promise.reject(new Error("Multipart model id is required"));
      return api.listMultipartModelCandidates(id, {
        q: query,
        limit: MULTIPART_CANDIDATE_PAGE_SIZE + 1,
        offset: options?.offset ?? 0,
        collection: options?.collection,
        direct: options?.direct,
      });
    },
    enabled: id !== null && (options?.enabled ?? true),
  });
}

export function usePrintStatistics(period: StatsPeriod) {
  const api = useQueryApi();
  return useQuery(printStatisticsOptions(period, api.getPrintStatistics));
}

export function useVaultConfig(options?: { enabled?: boolean; retry?: false }) {
  const api = useQueryApi();
  const read = { ...vaultConfigOptions(api.getVaultConfig), enabled: options?.enabled ?? true };
  return useQuery(options?.retry === false ? { ...read, retry: false } : read);
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
function modelFacetsOptions(api: QueryApi, filters: ModelListFilters) {
  return queryOptions<ModelFacetsRead>({
    queryKey: [...queryKeys.models, "facets", filters],
    queryFn: () => api.getModelFacets(filters),
  });
}

export function useModelFacets(filters: ModelListFilters, options?: { enabled?: boolean }) {
  const api = useQueryApi();
  return useQuery({
    ...modelFacetsOptions(api, filters),
    enabled: options?.enabled,
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

function modelListOptions(
  api: QueryApi,
  filters: ModelListFilters,
  pageSize: number,
  sort: ModelSort,
) {
  return infiniteQueryOptions<
    ModelPageRead,
    Error,
    InfiniteData<ModelPageRead>,
    QueryKey,
    ModelPageCursor
  >({
    queryKey: [...queryKeys.models, "list", filters, sort],
    queryFn: ({ pageParam }) => {
      markStartup("library-requests");
      return api.listModelPage({
        ...filters,
        limit: pageSize,
        sort,
        cursor: pageParam ?? undefined,
      });
    },
    initialPageParam: null,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });
}

export function useModelList(
  filters: ModelListFilters,
  pageSize: number,
  sort: ModelSort,
  enabled = true,
) {
  const api = useQueryApi();
  return useInfiniteQuery({
    ...modelListOptions(api, filters, pageSize, sort),
    enabled,
    placeholderData: keepPreviousData,
  });
}

/**
 * Warms the cache for a view the user is about to open (a folder under the
 * pointer), so the click lands on data that is already there. Each warmer takes
 * the same arguments as its hook and so fills exactly the entry the hook reads;
 * data still fresh under `staleTime` is not refetched.
 */
export function useLibraryPrefetch() {
  const api = useQueryApi();
  const client = useQueryClient();
  return useMemo(
    () => ({
      modelList: (filters: ModelListFilters, pageSize: number, sort: ModelSort) =>
        client.prefetchInfiniteQuery(modelListOptions(api, filters, pageSize, sort)),
      multipartModels: (filters?: MultipartModelListFilters) =>
        client.prefetchQuery(multipartModelsOptions(api, filters)),
      modelFacets: (filters: ModelListFilters) =>
        client.prefetchQuery(modelFacetsOptions(api, filters)),
      collectionReadme: (collectionId: number) =>
        client.prefetchQuery(collectionReadmeOptions(api, collectionId)),
    }),
    [api, client],
  );
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

export function useOutlinerCollections(params: OutlinerParams, enabled = true) {
  const api = useQueryApi();
  return useInfiniteQuery({
    queryKey: [...queryKeys.outliner, "collections", params],
    queryFn: ({ pageParam, signal }: { pageParam: string | null; signal: AbortSignal }) =>
      api.listOutlinerCollections({ ...params, cursor: pageParam ?? undefined }, signal),
    initialPageParam: null,
    getNextPageParam: (page) => page.next_cursor,
    enabled,
  });
}
export function useOutlinerEntries(params: OutlinerParams, enabled = true) {
  const api = useQueryApi();
  return useInfiniteQuery({
    queryKey: [...queryKeys.outliner, "entries", params],
    queryFn: ({ pageParam, signal }: { pageParam: string | null; signal: AbortSignal }) =>
      api.listOutlinerEntries({ ...params, cursor: pageParam ?? undefined }, signal),
    initialPageParam: null,
    getNextPageParam: (page) => page.next_cursor,
    enabled,
  });
}
export function useOutlinerSearch(params: OutlinerParams, enabled = true) {
  const api = useQueryApi();
  return useInfiniteQuery({
    queryKey: [...queryKeys.outliner, "search", params],
    queryFn: ({ pageParam, signal }: { pageParam: string | null; signal: AbortSignal }) =>
      api.searchOutliner({ ...params, cursor: pageParam ?? undefined }, signal),
    initialPageParam: null,
    getNextPageParam: (page) => page.next_cursor,
    enabled,
  });
}
