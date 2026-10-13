import { getJson, requestApi, jsonHeaders, type GetJsonOptions } from "@/lib/api/request";
import type {
  AuditPolicy,
  BackupVerification,
  VaultAuditFinding,
  VaultAuditMode,
  VaultAuditRun,
} from "@/types/maintenance";

export function startVaultAudit(
  mode: VaultAuditMode,
  options: GetJsonOptions = {},
): Promise<VaultAuditRun> {
  return requestApi<VaultAuditRun>("/api/v1/maintenance/audits", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify({ mode }),
    signal: options.signal,
  });
}

export function getLatestVaultAudit(options: GetJsonOptions = {}): Promise<VaultAuditRun> {
  return getJson<VaultAuditRun>("/api/v1/maintenance/audits/latest", options);
}

export function getVaultAudit(id: number, options: GetJsonOptions = {}): Promise<VaultAuditRun> {
  return getJson<VaultAuditRun>(`/api/v1/maintenance/audits/${id}`, options);
}

export function cancelVaultAudit(id: number, options: GetJsonOptions = {}): Promise<VaultAuditRun> {
  return requestApi<VaultAuditRun>(`/api/v1/maintenance/audits/${id}/cancel`, {
    method: "POST",
    headers: jsonHeaders(),
    body: "{}",
    signal: options.signal,
  });
}

export function repairAuditFinding(
  id: number,
  options: GetJsonOptions = {},
): Promise<VaultAuditFinding> {
  return requestApi<VaultAuditFinding>(`/api/v1/maintenance/findings/${id}/repair`, {
    method: "POST",
    headers: jsonHeaders(),
    body: "{}",
    signal: options.signal,
  });
}

export function ignoreAuditFinding(
  id: number,
  options: GetJsonOptions = {},
): Promise<VaultAuditFinding> {
  return requestApi<VaultAuditFinding>(`/api/v1/maintenance/findings/${id}/ignore`, {
    method: "POST",
    headers: jsonHeaders(),
    body: "{}",
    signal: options.signal,
  });
}

export function verifyBackup(
  backupId: string,
  sourceRef?: string | null,
  options: GetJsonOptions = {},
): Promise<BackupVerification> {
  const query = sourceRef ? `?source_ref=${encodeURIComponent(sourceRef)}` : "";
  return requestApi<BackupVerification>(
    `/api/v1/backups/${encodeURIComponent(backupId)}/verify${query}`,
    { method: "POST", headers: jsonHeaders(), body: "{}", signal: options.signal },
  );
}

export function listAuditPolicies(options: GetJsonOptions = {}): Promise<AuditPolicy[]> {
  return getJson<AuditPolicy[]>("/api/v1/maintenance/audit-policies", options);
}

export function saveAuditPolicy(
  policy: AuditPolicy,
  options: GetJsonOptions = {},
): Promise<AuditPolicy> {
  const {
    mode,
    estimated_remote_bytes: _estimated,
    overdue: _overdue,
    revision: _revision,
    next_due_at: _next,
    last_attempt_at: _attempt,
    last_success_at: _success,
    deferred_reason: _reason,
    ...payload
  } = policy;
  return requestApi<AuditPolicy>(`/api/v1/maintenance/audit-policies/${mode}`, {
    method: "PUT",
    headers: jsonHeaders(),
    body: JSON.stringify({
      ...payload,
      expected_revision: _revision,
    }),
    signal: options.signal,
  });
}

export function skipAuditSlot(
  mode: VaultAuditMode,
  options: GetJsonOptions = {},
): Promise<AuditPolicy> {
  return requestApi<AuditPolicy>(`/api/v1/maintenance/audit-policies/${mode}/skip`, {
    method: "POST",
    headers: jsonHeaders(),
    body: "{}",
    signal: options.signal,
  });
}

export function listVaultAudits(options: GetJsonOptions = {}): Promise<VaultAuditRun[]> {
  return getJson<VaultAuditRun[]>("/api/v1/maintenance/audits", options);
}
