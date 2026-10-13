import { getJson, sendJson } from "./request";

export interface StorageInventory {
  schema_version: number;
  generated_at: string;
  measured_at: string | null;
  target_ref: string;
  logical_bytes: number;
  external_referenced_bytes: number;
  unique_owned_bytes: number;
  unknown_object_count: number;
  temporary_bytes: number;
  backup_bytes: number;
  measured_provider_bytes: number | null;
  method: string;
  confidence: string;
  provider_capacity: {
    status: string;
    total_bytes: number | null;
    used_bytes: number | null;
    available_bytes: number | null;
    quota_bytes: number | null;
    measured_at: string | null;
    method: string;
    reliability: string;
    error: string | null;
  };
  latest_audit: {
    run_id: number;
    completed_at: string;
    unclaimed_object_count: number;
    unclaimed_bytes: number | null;
    unknown_size_count: number;
    method: string;
  } | null;
  buckets: {
    category: string;
    lifecycle: string;
    count: number;
    logical_bytes: number;
    external_bytes: number;
  }[];
  volumes: {
    domain_id: string;
    roles: string[];
    total_bytes: number | null;
    free_bytes: number | null;
    reserved_bytes: number;
    headroom_bytes: number;
    status: string;
    method: string;
  }[];
}

export interface StorageInventoryReport {
  inventory: StorageInventory;
  history: {
    sampled_at: string;
    owned_bytes: number;
    categories: {
      live_originals: number;
      trash: number;
      derived_cache: number;
      backups: number;
    };
  }[];
  forecast: {
    status: string;
    days_remaining: number | null;
    bytes_per_day: number | null;
    sample_count: number;
    window_days: number;
    confidence: string;
    threshold_at: string | null;
  };
}

export interface StorageCapacityActivity {
  active_reservations: {
    operation_kind: string;
    required_bytes: number;
    roles: string[];
    durable: boolean;
    created_at: string;
    expires_at: string;
  }[];
  recent_denials: {
    operation_kind: string;
    reason: string;
    required_bytes: number;
    available_bytes: number | null;
    reserved_bytes: number;
    headroom_bytes: number;
    occurred_at: string;
  }[];
}

export interface CollectionStorageRow {
  collection_id: number | null;
  name: string;
  logical_bytes: number;
  external_bytes: number;
  model_count: number;
}

export interface ModelStorageRow {
  model_id: number;
  name: string;
  logical_bytes: number;
}

export interface CleanupOpportunity {
  owner: "staging" | "trash" | "backups" | "cache";
  candidate_count: number;
  candidate_bytes: number;
  action: string;
  available: boolean;
}

export const getStorageInventory = () =>
  getJson<StorageInventoryReport>("/api/v1/storage/inventory", {});
export const sampleStorageInventory = () =>
  sendJson<StorageInventory>("/api/v1/storage/inventory/sample", "POST", {});
export const cleanupStorageStaging = () =>
  sendJson<{ leases_removed: number; files_removed: number }>(
    "/api/v1/storage/inventory/cleanup-staging",
    "POST",
    {},
  );
export const cleanupStorageCache = () =>
  sendJson<{ candidates: number; enqueued: number }>(
    "/api/v1/storage/inventory/cleanup-cache",
    "POST",
    {},
  );
export const getStorageCapacityActivity = () =>
  getJson<StorageCapacityActivity>("/api/v1/storage/inventory/activity", {});
export const getStorageCleanupOpportunities = () =>
  getJson<CleanupOpportunity[]>("/api/v1/storage/inventory/cleanup-opportunities", {});
export const getCollectionStorage = (offset = 0, limit = 10) =>
  getJson<CollectionStorageRow[]>(
    `/api/v1/storage/inventory/collections?offset=${offset}&limit=${limit}`,
    {},
  );
export const getModelStorage = (collectionId: number | null, offset = 0, limit = 10) => {
  const collection = collectionId === null ? "" : `&collection_id=${collectionId}`;
  return getJson<ModelStorageRow[]>(
    `/api/v1/storage/inventory/models?offset=${offset}&limit=${limit}${collection}`,
    {},
  );
};
