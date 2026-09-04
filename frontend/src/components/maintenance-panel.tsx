import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Database,
  HardDrive,
  RefreshCw,
  ShieldCheck,
  Wrench,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import {
  cancelVaultAudit,
  getLatestVaultAudit,
  getVaultAudit,
  ignoreAuditFinding,
  listBackupSources,
  repairAuditFinding,
  startVaultAudit,
  verifyBackup,
} from "@/lib/api";
import { toast } from "@/lib/toast";
import { useI18n } from "@/lib/i18n";
import { formatBytes } from "@/lib/format";
import type { BackupMeta } from "@/lib/api";
import type { BackupVerification, VaultAuditFinding, VaultAuditRun } from "@/types";

// Audit codes arrive from the API as plain strings, so the lookup is a Map: a
// code this build has no wording for reads back as `undefined` and falls back
// to the raw code instead of silently rendering an empty label.
const FINDING_LABELS = new Map([
  ["owned_blob_missing", "Owned Artifact is missing"],
  ["owned_blob_unreadable", "Owned Artifact cannot be read"],
  ["owned_blob_size_mismatch", "Artifact size differs from database"],
  ["owned_blob_hash_mismatch", "Artifact checksum differs from database"],
  ["external_root_unavailable", "Library source is unavailable"],
  ["linked_file_missing", "Linked file is missing"],
  ["thumbnail_missing", "Thumbnail is missing"],
  ["thumbnail_unreadable", "Thumbnail cannot be decoded"],
  ["metadata_missing", "Artifact Metadata is missing"],
  ["model_without_live_artifact", "Model has no live Artifact"],
  ["recommended_revision_missing", "Recommended Revision is missing"],
  ["recommended_revision_duplicate", "Multiple Revisions are recommended"],
  ["embedded_image_missing", "Embedded image is missing"],
  ["embedded_image_unreferenced", "Embedded image is no longer referenced"],
  ["background_job_stuck", "Background job may be stuck"],
  ["backup_manifest_invalid", "Backup manifest or archive is invalid"],
  ["backup_member_missing", "Backup member is missing"],
  ["backup_member_size_mismatch", "Backup member size differs from its manifest"],
]);

function isActive(run: VaultAuditRun | null): boolean {
  return run?.state === "pending" || run?.state === "running";
}

function sourceKey(item: BackupMeta): string {
  return (
    item.source_ref ??
    `${item.location}:${item.namespace ?? ""}:${item.key ?? ""}:${item.backup_id}`
  );
}

function shortOpaque(value: string | null | undefined): string {
  return value ? `${value.slice(0, 16)}…` : "unavailable";
}

function formatAuditDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function storageFileType(identifier: string): string {
  const name = identifier.split(/[\\/]/).at(-1) ?? identifier;
  const separator = name.lastIndexOf(".");
  return separator > -1 && separator < name.length - 1
    ? name.slice(separator + 1).toUpperCase()
    : "stored";
}

export function MaintenancePanel() {
  const { t } = useI18n();
  const [run, setRun] = useState<VaultAuditRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [severity, setSeverity] = useState<"all" | "critical" | "warning" | "info">("all");
  const [backups, setBackups] = useState<BackupMeta[]>([]);
  const [verifications, setVerifications] = useState<Record<string, BackupVerification>>({});
  const [verifying, setVerifying] = useState<string | null>(null);
  const [repairTarget, setRepairTarget] = useState<VaultAuditFinding | null>(null);
  const [repairing, setRepairing] = useState(false);

  const refresh = useCallback(() => {
    getLatestVaultAudit()
      .then(setRun)
      .catch(() => setRun(null));
  }, []);

  useEffect(() => {
    refresh();
    listBackupSources()
      .then(setBackups)
      .catch(() => setBackups([]));
  }, [refresh]);

  useEffect(() => {
    if (!isActive(run)) return;
    const timer = window.setInterval(() => {
      if (run)
        getVaultAudit(run.id)
          .then(setRun)
          .catch(() => {});
    }, 1500);
    return () => window.clearInterval(timer);
  }, [run]);

  const findings = useMemo(
    () => (run?.findings ?? []).filter((item) => severity === "all" || item.severity === severity),
    [run, severity],
  );
  const unlinkedFindings = useMemo(
    () => (run?.findings ?? []).filter((item) => item.code === "unowned_blob_detected"),
    [run],
  );
  const measuredUnlinkedBytes = useMemo(
    () =>
      unlinkedFindings.reduce((total, finding) => total + (finding.details.actual_size ?? 0), 0),
    [unlinkedFindings],
  );
  const measuredUnlinkedCount = useMemo(
    () => unlinkedFindings.filter((finding) => finding.details.actual_size != null).length,
    [unlinkedFindings],
  );

  async function start(mode: "quick" | "full") {
    setBusy(true);
    try {
      setRun(await startVaultAudit(mode));
    } catch (error) {
      toast.error(error);
    } finally {
      setBusy(false);
    }
  }

  async function act(finding: VaultAuditFinding, action: "repair" | "ignore") {
    try {
      if (action === "repair") await repairAuditFinding(finding.id);
      else await ignoreAuditFinding(finding.id);
      if (run) setRun(await getVaultAudit(run.id));
      toast.success(action === "repair" ? "Repair completed" : t("settings.auditMarkedReviewed"));
    } catch (error) {
      toast.error(error);
    }
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
    const sourceRef = sourceKey(item);
    if (!item.source_ref) {
      toast.error(t("settings.backupSourceUnavailable"));
      return;
    }
    setVerifying(sourceRef);
    try {
      const result = await verifyBackup(item.backup_id, item.source_ref);
      setVerifications((current) => ({ ...current, [sourceRef]: result }));
    } catch (error) {
      toast.error(error);
    } finally {
      setVerifying(null);
    }
  }

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader className="gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <CardTitle className="flex items-center gap-2">
              <ShieldCheck className="h-4 w-4" /> Vault Audit
            </CardTitle>
            <CardDescription>
              Read-only checks for owned Artifacts, thumbnails, Metadata, external links, and
              storage ownership.
            </CardDescription>
          </div>
          <div className="flex gap-2">
            <Button
              size="xs"
              onClick={() => void start("quick")}
              loading={busy}
              disabled={isActive(run)}
            >
              Quick Audit
            </Button>
            <Button
              size="xs"
              variant="outline"
              onClick={() => void start("full")}
              loading={busy}
              disabled={isActive(run)}
            >
              Full Audit
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {!run ? (
            <p className="text-sm text-muted-foreground">No audit has run yet.</p>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <Badge
                  variant={
                    run.state === "completed"
                      ? "success"
                      : run.state === "failed"
                        ? "destructive"
                        : "secondary"
                  }
                >
                  {run.state}
                </Badge>
                <span className="text-muted-foreground">
                  {run.mode} · {run.current_phase ?? "waiting"}
                </span>
                <span className="ml-auto font-mono text-xs">{Math.round(run.progress)}%</span>
                {isActive(run) && (
                  <Button
                    size="xs"
                    variant="outline"
                    onClick={() => void cancelVaultAudit(run.id).then(setRun)}
                  >
                    Cancel
                  </Button>
                )}
              </div>
              <div
                className="h-2 overflow-hidden rounded-full bg-muted"
                aria-label={`${Math.round(run.progress)} percent complete`}
              >
                <div
                  className="h-full origin-left bg-primary transition-transform duration-fast ease-out"
                  style={{ transform: `scaleX(${run.progress / 100})` }}
                />
              </div>
              <div className="flex flex-wrap gap-2">
                {(["all", "critical", "warning", "info"] as const).map((value) => (
                  <Button
                    key={value}
                    size="xs"
                    variant={severity === value ? "secondary" : "ghost"}
                    onClick={() => setSeverity(value)}
                  >
                    {value}
                    {value === "all"
                      ? ` ${run.findings.length}`
                      : value === "critical"
                        ? ` ${run.critical_count}`
                        : value === "warning"
                          ? ` ${run.warning_count}`
                          : ` ${run.info_count}`}
                  </Button>
                ))}
              </div>
              {unlinkedFindings.length > 0 && (severity === "all" || severity === "info") && (
                <div className="rounded-md border border-border bg-muted/30 p-3">
                  <div className="flex items-start gap-3">
                    <HardDrive className="mt-0.5 h-4 w-4 flex-shrink-0 text-muted-foreground" />
                    <div className="min-w-0 space-y-1">
                      <p className="text-sm font-semibold">{t("settings.auditUnlinkedTitle")}</p>
                      <p className="text-xs font-medium text-foreground">
                        {measuredUnlinkedCount === 0
                          ? t(
                              unlinkedFindings.length === 1
                                ? "settings.auditUnlinkedSummaryUnknownOne"
                                : "settings.auditUnlinkedSummaryUnknownMany",
                              { count: String(unlinkedFindings.length) },
                            )
                          : measuredUnlinkedCount === unlinkedFindings.length
                            ? t(
                                unlinkedFindings.length === 1
                                  ? "settings.auditUnlinkedSummaryOne"
                                  : "settings.auditUnlinkedSummaryMany",
                                {
                                  count: String(unlinkedFindings.length),
                                  size: formatBytes(measuredUnlinkedBytes),
                                },
                              )
                            : t("settings.auditUnlinkedSummaryPartial", {
                                count: String(unlinkedFindings.length),
                                size: formatBytes(measuredUnlinkedBytes),
                              })}
                      </p>
                      <p className="max-w-3xl text-xs text-muted-foreground">
                        {t("settings.auditUnlinkedDescription")}
                      </p>
                    </div>
                  </div>
                </div>
              )}
              <div className="space-y-2">
                {findings.length === 0 ? (
                  <div className="flex items-center gap-2 rounded-md border border-border p-3 text-sm text-muted-foreground">
                    <CheckCircle2 className="h-4 w-4 text-success" /> No findings in this category.
                  </div>
                ) : (
                  findings.map((finding) => {
                    const isUnlinked = finding.code === "unowned_blob_detected";
                    const fileType = t("settings.auditFileType", {
                      type: storageFileType(finding.resource_identifier),
                    });
                    const size = finding.details.actual_size;
                    const modifiedAt = finding.details.modified_at;
                    const metadata =
                      size != null && modifiedAt
                        ? t("settings.auditFileMetadata", {
                            type: fileType,
                            size: formatBytes(size),
                            modified: formatAuditDate(modifiedAt),
                          })
                        : size != null
                          ? t("settings.auditFileMetadataSize", {
                              type: fileType,
                              size: formatBytes(size),
                            })
                          : modifiedAt
                            ? t("settings.auditFileMetadataModified", {
                                type: fileType,
                                modified: formatAuditDate(modifiedAt),
                              })
                            : t("settings.auditFileMetadataUnavailable", { type: fileType });
                    return (
                      <div
                        key={finding.id}
                        className="flex flex-col gap-3 rounded-md border border-border p-3 sm:flex-row sm:items-center"
                      >
                        <AlertTriangle
                          className={`h-4 w-4 flex-shrink-0 ${finding.severity === "critical" ? "text-destructive" : finding.severity === "warning" ? "text-warning" : "text-muted-foreground"}`}
                        />
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium">
                            {isUnlinked
                              ? t("settings.auditUnlinkedFinding")
                              : (FINDING_LABELS.get(finding.code) ?? finding.code)}
                          </p>
                          <p className="truncate text-xs text-muted-foreground">
                            {finding.resource_identifier}
                          </p>
                          {isUnlinked && (
                            <p className="text-xs text-muted-foreground">{metadata}</p>
                          )}
                        </div>
                        {finding.state === "open" ? (
                          <div className="flex gap-2">
                            {finding.repair_action && (
                              <Button size="xs" onClick={() => setRepairTarget(finding)}>
                                <Wrench className="h-3.5 w-3.5" /> Repair
                              </Button>
                            )}
                            <Button
                              size="xs"
                              variant="ghost"
                              onClick={() => void act(finding, "ignore")}
                            >
                              {t("settings.auditMarkReviewed")}
                            </Button>
                          </div>
                        ) : finding.state === "ignored" ? (
                          <Badge variant="secondary">{t("settings.auditReviewed")}</Badge>
                        ) : null}
                      </div>
                    );
                  })
                )}
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Database className="h-4 w-4" /> Backup Verification
          </CardTitle>
          <CardDescription>
            Streams each archive and checks safe paths, manifest, database member, member counts,
            and sizes.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          {backups.length === 0 ? (
            <p className="text-sm text-muted-foreground">No backups available.</p>
          ) : (
            backups.map((item) => {
              const sourceRef = sourceKey(item);
              const result = verifications[sourceRef];
              return (
                <div
                  key={sourceRef}
                  className="flex items-center gap-3 rounded-md border border-border p-3"
                >
                  {result?.valid ? (
                    <CheckCircle2 className="h-4 w-4 text-success" />
                  ) : (
                    <Database className="h-4 w-4 text-muted-foreground" />
                  )}
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{item.backup_id}</p>
                    <p className="truncate font-mono text-2xs text-muted-foreground">
                      {t("settings.backupSourceLocator", {
                        source: item.source_ref ?? t("settings.backupSourceUnavailable"),
                      })}
                    </p>
                    <p className="truncate font-mono text-2xs text-muted-foreground">
                      {t("settings.backupProviderRef", {
                        provider: shortOpaque(item.provider_ref),
                      })}
                    </p>
                    {item.key && (
                      <p className="truncate font-mono text-2xs text-muted-foreground">
                        {t("settings.backupExactKey", { key: item.key })}
                      </p>
                    )}
                    {item.prefix && (
                      <p className="truncate font-mono text-2xs text-muted-foreground">
                        {t("settings.backupPrefix", { prefix: item.prefix })}
                      </p>
                    )}
                    {item.archive_sha256 && (
                      <p className="truncate font-mono text-2xs text-muted-foreground">
                        {t("settings.backupSha256", {
                          digest: shortOpaque(item.archive_sha256),
                        })}
                      </p>
                    )}
                    <p className="text-xs text-muted-foreground">
                      {result
                        ? result.valid
                          ? `${result.checked_members} members verified`
                          : `${result.findings.length} verification findings`
                        : "Not verified this session"}
                    </p>
                  </div>
                  <Button
                    size="xs"
                    variant="outline"
                    loading={verifying === sourceRef}
                    disabled={!item.source_ref}
                    title={!item.source_ref ? t("settings.backupSourceUnavailable") : undefined}
                    onClick={() => void checkBackup(item)}
                  >
                    <RefreshCw className="h-3.5 w-3.5" /> Verify
                  </Button>
                </div>
              );
            })
          )}
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
