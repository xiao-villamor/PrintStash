import { getJson, sendJson } from "@/lib/api/request";
import type { BackupVerification, VaultAuditFinding, VaultAuditMode, VaultAuditRun } from "@/types/maintenance";

export function startVaultAudit(mode: VaultAuditMode): Promise<VaultAuditRun> {
  return sendJson<VaultAuditRun>("/api/v1/maintenance/audits", "POST", { mode });
}

export function getLatestVaultAudit(): Promise<VaultAuditRun> {
  return getJson<VaultAuditRun>("/api/v1/maintenance/audits/latest", { fresh: true });
}

export function getVaultAudit(id: number): Promise<VaultAuditRun> {
  return getJson<VaultAuditRun>(`/api/v1/maintenance/audits/${id}`, { fresh: true });
}

export function cancelVaultAudit(id: number): Promise<VaultAuditRun> {
  return sendJson<VaultAuditRun>(`/api/v1/maintenance/audits/${id}/cancel`, "POST", {});
}

export function repairAuditFinding(id: number): Promise<VaultAuditFinding> {
  return sendJson<VaultAuditFinding>(`/api/v1/maintenance/findings/${id}/repair`, "POST", {});
}

export function ignoreAuditFinding(id: number): Promise<VaultAuditFinding> {
  return sendJson<VaultAuditFinding>(`/api/v1/maintenance/findings/${id}/ignore`, "POST", {});
}

export function verifyBackup(backupId: string): Promise<BackupVerification> {
  return sendJson<BackupVerification>(`/api/v1/backups/${encodeURIComponent(backupId)}/verify`, "POST", {});
}
