import { getJson, jsonHeaders, requestApi, type GetJsonOptions } from "@/lib/api/request";
import { requireEditingBase, requireEditingReceipt } from "./editing";
import type { EditingBase } from "@/types/editing";

/** Where the running mesh job count comes from. */
export type MeshWorkersSource = "database" | "environment" | "automatic";

export interface MeshWorkersRead extends EditingBase {
  /** Mesh jobs this process runs at once. */
  jobs: number;
  /** The administrator's stored count; `null` when none is stored. */
  configured: number | null;
  /** What the CPU-and-memory rule picks for this machine. */
  automatic: number;
  /** Most jobs the native memory pool fits. */
  maximum: number;
  source: MeshWorkersSource;
  /** Separate worker processes apply a change when they restart. */
  restart_required: boolean;
}

type WriteOptions = GetJsonOptions & { base: EditingBase };

async function write(method: "PUT" | "DELETE", jobs: number | null, options: WriteOptions) {
  const { base } = options;
  requireEditingBase(base);
  const row = await requestApi<MeshWorkersRead>("/api/v1/config/mesh-workers", {
    method,
    headers: {
      ...jsonHeaders(),
      "If-Match": `"vault-config-e${base.edit_epoch}-v${base.edit_version}"`,
      "X-PrintStash-Edit-Contract": "conditional-v1",
    },
    body: jobs === null ? undefined : JSON.stringify({ jobs }),
    signal: options.signal,
  });
  requireEditingReceipt(row, base);
  return row;
}

export const meshWorkersApi = {
  read: (options?: GetJsonOptions) =>
    getJson<MeshWorkersRead>("/api/v1/config/mesh-workers", options),
  save: (jobs: number, options: WriteOptions) => write("PUT", jobs, options),
  reset: (options: WriteOptions) => write("DELETE", null, options),
};
