import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Database, RefreshCw, ShieldCheck, Wrench } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import {
  cancelVaultAudit,
  getLatestVaultAudit,
  getVaultAudit,
  ignoreAuditFinding,
  listBackups,
  repairAuditFinding,
  startVaultAudit,
  verifyBackup,
} from "@/lib/api";
import { toast } from "@/lib/toast";
import type { BackupMeta } from "@/lib/api";
import type { BackupVerification, VaultAuditFinding, VaultAuditRun } from "@/types";

const FINDING_LABELS: Record<string, string> = {
  owned_blob_missing: "Owned Artifact is missing",
  owned_blob_unreadable: "Owned Artifact cannot be read",
  owned_blob_size_mismatch: "Artifact size differs from database",
  owned_blob_hash_mismatch: "Artifact checksum differs from database",
  unowned_blob_detected: "Unclaimed storage object",
  external_root_unavailable: "External Library is unavailable",
  linked_file_missing: "Linked file is missing",
  thumbnail_missing: "Thumbnail is missing",
  thumbnail_unreadable: "Thumbnail cannot be decoded",
  metadata_missing: "Artifact Metadata is missing",
  model_without_live_artifact: "Model has no live Artifact",
  recommended_revision_missing: "Recommended Revision is missing",
  recommended_revision_duplicate: "Multiple Revisions are recommended",
  embedded_image_missing: "Embedded image is missing",
  embedded_image_unreferenced: "Embedded image is no longer referenced",
  background_job_stuck: "Background job may be stuck",
  backup_manifest_invalid: "Backup manifest or archive is invalid",
  backup_member_missing: "Backup member is missing",
  backup_member_size_mismatch: "Backup member size differs from its manifest",
};

function isActive(run: VaultAuditRun | null): boolean {
  return run?.state === "pending" || run?.state === "running";
}

export function MaintenancePanel() {
  const [run, setRun] = useState<VaultAuditRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [severity, setSeverity] = useState<"all" | "critical" | "warning" | "info">("all");
  const [backups, setBackups] = useState<BackupMeta[]>([]);
  const [verifications, setVerifications] = useState<Record<string, BackupVerification>>({});
  const [verifying, setVerifying] = useState<string | null>(null);
  const [repairTarget, setRepairTarget] = useState<VaultAuditFinding | null>(null);
  const [repairing, setRepairing] = useState(false);

  const refresh = useCallback(async () => {
    try { setRun(await getLatestVaultAudit()); } catch { setRun(null); }
  }, []);

  useEffect(() => {
    void refresh();
    listBackups().then(setBackups).catch(() => setBackups([]));
  }, [refresh]);

  useEffect(() => {
    if (!isActive(run)) return;
    const timer = window.setInterval(() => {
      if (run) getVaultAudit(run.id).then(setRun).catch(() => {});
    }, 1500);
    return () => window.clearInterval(timer);
  }, [run]);

  const findings = useMemo(
    () => (run?.findings ?? []).filter((item) => severity === "all" || item.severity === severity),
    [run, severity],
  );

  async function start(mode: "quick" | "full") {
    setBusy(true);
    try { setRun(await startVaultAudit(mode)); }
    catch (error) { toast.error(error); }
    finally { setBusy(false); }
  }

  async function act(finding: VaultAuditFinding, action: "repair" | "ignore") {
    try {
      if (action === "repair") await repairAuditFinding(finding.id);
      else await ignoreAuditFinding(finding.id);
      if (run) setRun(await getVaultAudit(run.id));
      toast.success(action === "repair" ? "Repair completed" : "Finding ignored");
    } catch (error) { toast.error(error); }
  }

  async function confirmRepair() {
    if (!repairTarget) return;
    setRepairing(true);
    try {
      await act(repairTarget, "repair");
      setRepairTarget(null);
    } finally {
      setRepairing(false);
    }
  }

  async function checkBackup(item: BackupMeta) {
    setVerifying(item.backup_id);
    try {
      const result = await verifyBackup(item.backup_id);
      setVerifications((current) => ({ ...current, [item.backup_id]: result }));
    } catch (error) { toast.error(error); }
    finally { setVerifying(null); }
  }

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader className="gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <CardTitle className="flex items-center gap-2"><ShieldCheck className="h-4 w-4" /> Vault Audit</CardTitle>
            <CardDescription>Read-only checks for owned Artifacts, thumbnails, Metadata, external links, and storage ownership.</CardDescription>
          </div>
          <div className="flex gap-2">
            <Button size="xs" onClick={() => void start("quick")} loading={busy} disabled={isActive(run)}>Quick Audit</Button>
            <Button size="xs" variant="outline" onClick={() => void start("full")} loading={busy} disabled={isActive(run)}>Full Audit</Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {!run ? (
            <p className="text-sm text-muted-foreground">No audit has run yet.</p>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <Badge variant={run.state === "completed" ? "success" : run.state === "failed" ? "destructive" : "secondary"}>{run.state}</Badge>
                <span className="text-muted-foreground">{run.mode} · {run.current_phase ?? "waiting"}</span>
                <span className="ml-auto font-mono text-xs">{Math.round(run.progress)}%</span>
                {isActive(run) && <Button size="xs" variant="outline" onClick={() => void cancelVaultAudit(run.id).then(setRun)}>Cancel</Button>}
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-muted" aria-label={`${Math.round(run.progress)} percent complete`}>
                <div className="h-full origin-left bg-primary transition-transform duration-fast ease-out" style={{ transform: `scaleX(${run.progress / 100})` }} />
              </div>
              <div className="flex flex-wrap gap-2">
                {(["all", "critical", "warning", "info"] as const).map((value) => (
                  <Button key={value} size="xs" variant={severity === value ? "secondary" : "ghost"} onClick={() => setSeverity(value)}>
                    {value}{value === "all" ? ` ${run.findings.length}` : value === "critical" ? ` ${run.critical_count}` : value === "warning" ? ` ${run.warning_count}` : ` ${run.info_count}`}
                  </Button>
                ))}
              </div>
              <div className="space-y-2">
                {findings.length === 0 ? (
                  <div className="flex items-center gap-2 rounded-md border border-border p-3 text-sm text-muted-foreground"><CheckCircle2 className="h-4 w-4 text-success" /> No findings in this category.</div>
                ) : findings.map((finding) => (
                  <div key={finding.id} className="flex flex-col gap-3 rounded-md border border-border p-3 sm:flex-row sm:items-center">
                    <AlertTriangle className={`h-4 w-4 flex-shrink-0 ${finding.severity === "critical" ? "text-destructive" : finding.severity === "warning" ? "text-warning" : "text-muted-foreground"}`} />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium">{FINDING_LABELS[finding.code] ?? finding.code}</p>
                      <p className="truncate text-xs text-muted-foreground">{finding.resource_identifier}</p>
                    </div>
                    {finding.state === "open" && (
                      <div className="flex gap-2">
                        {finding.repair_action && <Button size="xs" onClick={() => setRepairTarget(finding)}><Wrench className="h-3.5 w-3.5" /> Repair</Button>}
                        <Button size="xs" variant="ghost" onClick={() => void act(finding, "ignore")}>Ignore</Button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Database className="h-4 w-4" /> Backup Verification</CardTitle>
          <CardDescription>Streams each archive and checks safe paths, manifest, database member, member counts, and sizes.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          {backups.length === 0 ? <p className="text-sm text-muted-foreground">No backups available.</p> : backups.map((item) => {
            const result = verifications[item.backup_id];
            return (
              <div key={item.backup_id} className="flex items-center gap-3 rounded-md border border-border p-3">
                {result?.valid ? <CheckCircle2 className="h-4 w-4 text-success" /> : <Database className="h-4 w-4 text-muted-foreground" />}
                <div className="min-w-0 flex-1"><p className="truncate text-sm font-medium">{item.backup_id}</p><p className="text-xs text-muted-foreground">{result ? (result.valid ? `${result.checked_members} members verified` : `${result.findings.length} verification findings`) : "Not verified this session"}</p></div>
                <Button size="xs" variant="outline" loading={verifying === item.backup_id} onClick={() => void checkBackup(item)}><RefreshCw className="h-3.5 w-3.5" /> Verify</Button>
              </div>
            );
          })}
        </CardContent>
      </Card>
      <ConfirmModal
        open={repairTarget !== null}
        onClose={() => setRepairTarget(null)}
        onConfirm={() => void confirmRepair()}
        title="Repair this finding?"
        description="PrintStash will apply the targeted repair and record the action in the audit log. Original Artifact bytes are never replaced by thumbnail or metadata repairs."
        confirmLabel="Repair"
        busy={repairing}
      />
    </div>
  );
}
