/**
 * Storage insights are expensive to measure, so they are read once and kept.
 *
 * The keys live under the vault-stats root: every library write already
 * invalidates `queryKeys.vaultStats`, which marks these stale too, so a changed
 * library is re-measured on the next view and an unchanged one never is. The
 * only other way to re-read them is the panel's explicit refresh.
 */
import { keepPreviousData, queryOptions } from "@tanstack/react-query";
import {
  getCollectionStorage,
  getStorageCapacityActivity,
  getStorageCleanupOpportunities,
  getStorageInventory,
  type CleanupOpportunity,
  type CollectionStorageRow,
  type StorageCapacityActivity,
  type StorageInventoryReport,
} from "@/lib/api/storage-inventory";
import { queryKeys } from "@/lib/query-client";

export const COLLECTION_PAGE_SIZE = 10;

export const storageInsightsKeys = {
  all: [...queryKeys.vaultStats, "storage-insights"] as const,
  summary: [...queryKeys.vaultStats, "storage-insights", "summary"] as const,
  collections: (offset: number) =>
    [...queryKeys.vaultStats, "storage-insights", "collections", offset] as const,
};

/** Kept until a library write or an explicit refresh invalidates it. */
const kept = {
  staleTime: Infinity,
  gcTime: Infinity,
  refetchOnWindowFocus: false,
  retry: false,
} as const;

export interface StorageInsightsSummary {
  report: StorageInventoryReport;
  /** Secondary evidence: its own failure never hides the report. */
  activity: StorageCapacityActivity | null;
  cleanup: CleanupOpportunity[];
}

async function readSummary(): Promise<StorageInsightsSummary> {
  const report = await getStorageInventory();
  const [activity, cleanup] = await Promise.allSettled([
    getStorageCapacityActivity(),
    getStorageCleanupOpportunities(),
  ]);
  return {
    report,
    activity:
      activity.status === "fulfilled" &&
      Array.isArray(activity.value.active_reservations) &&
      Array.isArray(activity.value.recent_denials)
        ? activity.value
        : null,
    cleanup: cleanup.status === "fulfilled" && Array.isArray(cleanup.value) ? cleanup.value : [],
  };
}

export function storageInsightsOptions() {
  return queryOptions({ queryKey: storageInsightsKeys.summary, queryFn: readSummary, ...kept });
}

export interface CollectionStoragePage {
  rows: CollectionStorageRow[];
  hasMore: boolean;
}

export function collectionStorageOptions(offset: number) {
  return queryOptions({
    queryKey: storageInsightsKeys.collections(offset),
    queryFn: async (): Promise<CollectionStoragePage> => {
      const rows = await getCollectionStorage(offset, COLLECTION_PAGE_SIZE + 1);
      return {
        rows: Array.isArray(rows) ? rows.slice(0, COLLECTION_PAGE_SIZE) : [],
        hasMore: Array.isArray(rows) && rows.length > COLLECTION_PAGE_SIZE,
      };
    },
    placeholderData: keepPreviousData,
    ...kept,
  });
}
