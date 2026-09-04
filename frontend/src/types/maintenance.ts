export type VaultAuditMode = "quick" | "full";
export type VaultAuditState = "pending" | "running" | "completed" | "cancelled" | "failed";
export type VaultAuditSeverity = "info" | "warning" | "critical";

/** An S3 lifecycle rule's expiration clause, as reported by the bucket configuration. */
export interface StorageLifecycleExpiration {
  Days?: number;
  Date?: string;
  ExpiredObjectDeleteMarker?: boolean;
}

/**
 * The diagnostic payload a finding carries. Every key is optional: each check in
 * `services/vault_audit` attaches only the identifiers it knows about, and a repair
 * action reads back the one it needs (`model_id`, `file_id`, `inbox_item_id`).
 */
export interface VaultAuditFindingDetails {
  /** Primary key of the row the finding is about, for blob-level checks. */
  resource_id?: number;
  name?: string;
  model_id?: number;
  file_id?: number | null;
  library_id?: number;
  root_label?: string;
  job_id?: number;
  kind?: string;
  inbox_item_id?: number;
  state?: string;
  /** Backup archive member that failed verification. */
  member?: string;
  expected_size?: number;
  actual_size?: number;
  /** Destructive object-storage lifecycle rule overlapping the vault prefix. */
  rule_id?: string;
  prefix?: string;
  expiration?: StorageLifecycleExpiration;
}

export interface VaultAuditFinding {
  id: number;
  run_id: number;
  code: string;
  severity: VaultAuditSeverity;
  resource_type: string;
  resource_identifier: string;
  repair_action: string | null;
  state: "open" | "resolved" | "ignored";
  details: VaultAuditFindingDetails;
  created_at: string;
  resolved_at: string | null;
  resolved_by: number | null;
}

export interface VaultAuditRun {
  id: number;
  requested_by: number;
  mode: VaultAuditMode;
  state: VaultAuditState;
  info_count: number;
  warning_count: number;
  critical_count: number;
  progress: number;
  current_phase: string | null;
  error_code: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  findings: VaultAuditFinding[];
}

export interface BackupVerification {
  backup_id: string;
  valid: boolean;
  app_compatible: boolean;
  manifest_version: string | null;
  checked_members: number;
  findings: Array<{ code: string; member: string; expected_size?: number; actual_size?: number }>;
}
