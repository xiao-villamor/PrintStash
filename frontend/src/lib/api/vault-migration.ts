import { getJson, sendJson } from "./request";
import type { StorageProviderConfigValues } from "@/types";

export type VaultMigrationState =
  | "planned"
  | "copying"
  | "ready"
  | "activating"
  | "active"
  | "cleaned"
  | "discarded"
  | "paused"
  | "cutover_pending"
  | "draining"
  | "delta_copy"
  | "verifying"
  | "recovery_required"
  | "failed"
  | "complete";
export interface VaultMigrationPolicy {
  retention_days: number;
  concurrency: number;
  bandwidth_bytes_per_second: number | null;
}
export interface VaultMigrationAudit {
  id: number;
  state: string;
  critical_count: number;
  warning_count: number;
}
export interface VaultMigrationFailure {
  object_id: number | null;
  code: string;
  retryable: boolean;
}

export interface VaultMigrationRun {
  id: string;
  state: VaultMigrationState;
  plan_digest: string;
  backup_summary: { backup_id: string; source_ref: string | null; verified_at: string };
  objects: number;
  verified_objects: number;
  bytes: number;
  error_code: string | null;
  cleanup_after: string | null;
  cleanup_findings: { object_id: number | null; code: string }[];
  source_retained: boolean;
  expires_at: string;
  source: StorageProviderConfigValues;
  destination: StorageProviderConfigValues;
  capacity_warnings: string[];
  recovery_required: boolean;
  source_provider_ref: string;
  destination_provider_ref: string;
  capacity_resources: { role: string; required_bytes: number; available_bytes: number | null }[];
  policy: VaultMigrationPolicy;
  copied_objects: number;
  copied_bytes: number;
  verified_bytes: number;
  skipped_objects: number;
  skipped_bytes: number;
  failed_objects: number;
  failed_bytes: number;
  delta_objects: number;
  throughput_bytes_per_second: number | null;
  last_activity_at: string | null;
  retryable: boolean;
  phase_history: { phase: string; at: string }[];
  pre_audit: VaultMigrationAudit | null;
  post_audit: VaultMigrationAudit | null;
  full_audit: VaultMigrationAudit | null;
  cleanup_outcome: "retained_indefinitely" | "manual_cleanup" | "cleaned" | null;
  notification_events: number;
}
export interface VaultMigrationPreflight {
  destination: StorageProviderConfigValues;
  backup_id: string;
  backup_source_ref?: string | null;
  policy?: VaultMigrationPolicy;
}
const base = "/api/v1/storage/migrations";
const path = (id: string) => `${base}/${encodeURIComponent(id)}`;
export const listVaultMigrations = () => getJson<VaultMigrationRun[]>(base, { fresh: true });
export const getVaultMigration = (id: string) =>
  getJson<VaultMigrationRun>(path(id), { fresh: true });
export const preflightVaultMigration = (body: VaultMigrationPreflight) =>
  sendJson<VaultMigrationRun>(`${base}/preflight`, "POST", body);
export const startVaultMigration = (id: string, planDigest: string) =>
  sendJson<VaultMigrationRun>(`${path(id)}/start`, "POST", { plan_digest: planDigest });
export const advanceVaultMigration = (id: string) =>
  sendJson<VaultMigrationRun>(`${path(id)}/advance`, "POST", {});
export const cutoverVaultMigration = (id: string) =>
  sendJson<VaultMigrationRun>(`${path(id)}/cutover`, "POST", {});
export const recoverVaultMigration = (id: string) =>
  sendJson<VaultMigrationRun>(`${path(id)}/recover`, "POST", {});
export const cleanupVaultMigration = (
  id: string,
  source: boolean,
  backup?: Pick<VaultMigrationPreflight, "backup_id" | "backup_source_ref">,
) =>
  sendJson<VaultMigrationRun>(`${path(id)}/cleanup`, "POST", {
    confirmation: id,
    source,
    ...backup,
  });

export const pauseVaultMigration = (id: string) =>
  sendJson<VaultMigrationRun>(`${path(id)}/pause`, "POST", {});
export const resumeVaultMigration = (id: string) =>
  sendJson<VaultMigrationRun>(`${path(id)}/resume`, "POST", {});
export const retryVaultMigration = (id: string) =>
  sendJson<VaultMigrationRun>(`${path(id)}/retry`, "POST", {});
export const auditVaultMigration = (id: string) =>
  sendJson<VaultMigrationRun>(`${path(id)}/full-audit`, "POST", {});
export const retainVaultMigration = (id: string, removeCredentials = false) =>
  sendJson<VaultMigrationRun>(`${path(id)}/retain`, "POST", {
    remove_credentials: removeCredentials,
  });
export interface VaultMigrationReport extends VaultMigrationRun {
  resource_kind_totals: { resource_type: string; objects: number; bytes: number }[];
  recent_failures: VaultMigrationFailure[];
}
export const getVaultMigrationReport = (id: string) =>
  getJson<VaultMigrationReport>(`${path(id)}/report`, { fresh: true });
export async function downloadVaultMigrationReport(id: string): Promise<void> {
  const report = await getVaultMigrationReport(id);
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = `printstash-migration-${encodeURIComponent(id)}.json`;
  document.body.appendChild(link);
  try {
    link.click();
  } finally {
    link.remove();
    URL.revokeObjectURL(url);
  }
}
