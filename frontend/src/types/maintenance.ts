export type VaultAuditMode = "quick" | "full";
export type VaultAuditState = "pending" | "running" | "completed" | "cancelled" | "failed";
export type VaultAuditSeverity = "info" | "warning" | "critical";

export interface VaultAuditFinding {
  id: number;
  run_id: number;
  code: string;
  severity: VaultAuditSeverity;
  resource_type: string;
  resource_identifier: string;
  repair_action: string | null;
  state: "open" | "resolved" | "ignored";
  details: Record<string, unknown>;
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
