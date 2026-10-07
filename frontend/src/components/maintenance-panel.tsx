import { currentLocale } from "@/lib/locale";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Database,
  HardDrive,
  RefreshCw,
  ShieldCheck,
  Wrench,
} from "lucide-react";

import { AuditSchedulePanel } from "@/components/audit-schedule-panel";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { useQuery } from "@tanstack/react-query";
import {
  auditHistoryOptions,
  isActiveAudit as isActive,
  useMaintenanceCommands,
} from "@/lib/queries/settings-maintenance";
import {
  backupSourcesOptions,
  backupSourceKey as sourceKey,
} from "@/lib/queries/settings-backup-catalog";
import { parseApiError } from "@/lib/errors";
import { toast } from "@/lib/toast";
import { useI18n } from "@/lib/i18n";
import { formatBytes } from "@/lib/format";
import type { BackupMeta } from "@/lib/api";
import type { BackupVerification, VaultAuditFinding } from "@/types";

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

function shortOpaque(value: string | null | undefined): string {
  return value ? `${value.slice(0, 16)}…` : uiText("unavailable");
}

function formatAuditDate(value: string): string {
  return new Intl.DateTimeFormat(currentLocale(), {
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
  useUiLocale();
  const { t } = useI18n();
  const commands = useMaintenanceCommands();
  const history = useQuery({ ...auditHistoryOptions(), enabled: !commands.retired });
  const catalog = useQuery({ ...backupSourcesOptions(), enabled: !commands.retired });
  const denied = commands.retired || [401, 403, 404].includes(parseApiError(history.error).status);
  const run = denied ? null : (history.data?.[0] ?? null);
  const busy = commands.busy;
  const backups = catalog.isError ? [] : (catalog.data ?? []);
  const [severity, setSeverity] = useState<"all" | "critical" | "warning" | "info">("all");
  const [verifications, setVerifications] = useState<Record<string, BackupVerification>>({});
  const [verifying, setVerifying] = useState<string | null>(null);
  const [repairTarget, setRepairTarget] = useState<VaultAuditFinding | null>(null);
  const [repairing, setRepairing] = useState(false);

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
    try {
      await commands.start(mode);
    } catch (error) {
      if (commands.current()) toast.error(error);
    }
  }
  async function cancel() {
    if (!run) return;
    try {
      await commands.cancel(run.id);
    } catch (error) {
      if (commands.current()) toast.error(error);
    }
  }
  async function act(finding: VaultAuditFinding, action: "repair" | "ignore") {
    try {
      if (action === "repair") await commands.repair(finding.id);
      else await commands.ignore(finding.id);
      if (!commands.current()) return false;
      toast.success(
        action === "repair" ? uiText("Repair completed") : t("settings.auditMarkedReviewed"),
      );
      return true;
    } catch (error) {
      if (commands.current()) toast.error(error);
      return false;
    }
  }
  async function confirmRepair() {
    if (!repairTarget) return;
    setRepairing(true);
    try {
      if (await act(repairTarget, "repair")) setRepairTarget(null);
    } finally {
      if (commands.current()) setRepairing(false);
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
      const result = await commands.verify(item.backup_id, item.source_ref);
      if (!commands.current()) return;
      setVerifications((current) => ({ ...current, [sourceRef]: result }));
    } catch (error) {
      if (commands.current()) toast.error(error);
    } finally {
      if (commands.current()) setVerifying(null);
    }
  }

  if (commands.retired) return null;
  return (
    <div className="space-y-5 animate-panel-in">
      <Card role="region" aria-labelledby="maintenance-heading" className="overflow-hidden">
        <div className="border-b border-border px-4 py-4 sm:px-5">
          <h2 id="maintenance-heading" className="text-sm font-semibold">
            {t("maintenance.title")}
          </h2>
          <p className="text-xs text-muted-foreground">{t("maintenance.subtitle")}</p>
        </div>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4" aria-hidden />
            {t("maintenance.checkNow")}
          </CardTitle>
          <CardDescription>{t("maintenance.checkNowDescription")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="divide-y divide-border rounded-md border border-border">
            <div className="flex flex-col items-stretch gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <h3 className="text-sm font-semibold">{t("auditSchedule.quick")}</h3>
                <p className="mt-1 text-sm text-muted-foreground">
                  {t("maintenance.quickDescription")}
                </p>
              </div>
              <Button
                className="w-full sm:w-auto"
                onClick={() => void start("quick")}
                loading={busy}
                disabled={busy || history.isPending || history.isError || denied || isActive(run)}
              >
                {t("maintenance.runQuick")}
              </Button>
            </div>
            <div className="flex flex-col items-stretch gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <h3 className="text-sm font-semibold">{t("auditSchedule.full")}</h3>
                <p className="mt-1 text-sm text-muted-foreground">
                  {t("maintenance.fullDescription")}
                </p>
              </div>
              <Button
                className="w-full sm:w-auto"
                variant="outline"
                onClick={() => void start("full")}
                loading={busy}
                disabled={busy || history.isPending || history.isError || denied || isActive(run)}
              >
                {t("maintenance.runFull")}
              </Button>
            </div>
          </div>
          {history.isError || denied ? (
            <div role="alert">
              <p>{t("auditSchedule.historyLoadFailed")}</p>
              <Button
                onClick={() => void history.refetch()}
                aria-label={t("maintenance.retryHistory")}
              >
                {t("auditSchedule.retry")}
              </Button>
            </div>
          ) : history.isPending ? (
            <p role="status">{t("auditSchedule.loading")}</p>
          ) : !run ? (
            <p className="text-sm text-muted-foreground">{t("maintenance.noRun")}</p>
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
                  {t(
                    run.state === "completed"
                      ? "auditSchedule.statusCompleted"
                      : run.state === "running"
                        ? "auditSchedule.statusRunning"
                        : run.state === "pending"
                          ? "auditSchedule.statusPending"
                          : run.state === "failed"
                            ? "auditSchedule.statusFailed"
                            : "auditSchedule.statusCancelled",
                  )}
                </Badge>
                <span className="text-muted-foreground">
                  {t(run.mode === "quick" ? "auditSchedule.quick" : "auditSchedule.full")}
                </span>
                {isActive(run) && (
                  <span className="ml-auto text-xs tabular-nums">{Math.round(run.progress)}%</span>
                )}
                {isActive(run) && (
                  <Button size="xs" variant="outline" disabled={busy} onClick={() => void cancel()}>
                    {uiText("Cancel")}
                  </Button>
                )}
              </div>
              {isActive(run) && (
                <div
                  className="h-2 overflow-hidden rounded-full bg-muted"
                  aria-label={uiText("{value1} percent complete", {
                    value1: String(Math.round(run.progress)),
                  })}
                >
                  <div
                    className="h-full origin-left bg-primary transition-transform duration-fast ease-out"
                    style={{ transform: `scaleX(${run.progress / 100})` }}
                  />
                </div>
              )}
              {run.findings.length > 0 && (
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
              )}
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
              {(run.findings.length > 0 || run.state === "completed" || run.state === "failed") && (
                <div className="space-y-2">
                  {findings.length === 0 ? (
                    <div className="flex items-center gap-2 rounded-md border border-border p-3 text-sm text-muted-foreground">
                      {run.state === "failed" ? (
                        <AlertTriangle className="h-4 w-4 shrink-0 text-warning" />
                      ) : (
                        <CheckCircle2 className="h-4 w-4 shrink-0 text-success" />
                      )}
                      {run.state === "failed"
                        ? t("maintenance.checkIncomplete")
                        : run.findings.length === 0
                          ? t("maintenance.noProblems")
                          : uiText(" No findings in this category.")}
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
                                <Button
                                  size="xs"
                                  disabled={busy || history.isError}
                                  onClick={() => setRepairTarget(finding)}
                                >
                                  <Wrench className="h-3.5 w-3.5" />
                                  {uiText(" Repair")}
                                </Button>
                              )}
                              <Button
                                size="xs"
                                variant="ghost"
                                disabled={busy || history.isError}
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
              )}
            </>
          )}
        </CardContent>
      </Card>
      <AuditSchedulePanel />

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Database className="h-4 w-4" />
            {t("maintenance.backupTitle")}
          </CardTitle>
          <CardDescription>{t("maintenance.backupDescription")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          {catalog.isError ? (
            <div role="alert">
              <p>{t("maintenance.backupLoadFailed")}</p>
              <Button onClick={() => void catalog.refetch()}>{t("auditSchedule.retry")}</Button>
            </div>
          ) : catalog.isPending ? (
            <p role="status">{t("auditSchedule.loading")}</p>
          ) : backups.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("maintenance.noBackups")}</p>
          ) : (
            backups.map((item) => {
              const sourceRef = sourceKey(item);
              const result = verifications[sourceRef];
              return (
                <div
                  key={sourceRef}
                  className="flex min-w-0 flex-col items-stretch gap-3 rounded-md border border-border p-3 sm:flex-row sm:items-center"
                >
                  <div className="flex min-w-0 items-start gap-3 sm:flex-1">
                    {result?.valid ? (
                      <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" />
                    ) : (
                      <Database className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                    )}
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium">{formatAuditDate(item.created_at)}</p>
                      <p className="text-xs text-muted-foreground">
                        {formatBytes(item.size_bytes)} ·{" "}
                        {t("maintenance.backupFiles", { count: item.file_count })}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {result
                          ? result.valid
                            ? uiText("{value1} members verified", {
                                value1: String(result.checked_members),
                              })
                            : uiText("{value1} verification findings", {
                                value1: String(result.findings.length),
                              })
                          : uiText("Not verified this session")}
                      </p>
                      <details className="mt-2 text-xs text-muted-foreground">
                        <summary className="cursor-pointer font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                          {t("maintenance.backupDetails")}
                        </summary>
                        <div className="mt-2 space-y-1 break-all font-mono text-2xs">
                          <p>
                            {t("settings.backupSourceLocator", {
                              source: item.source_ref ?? t("settings.backupSourceUnavailable"),
                            })}
                          </p>
                          <p>
                            {t("settings.backupProviderRef", {
                              provider: shortOpaque(item.provider_ref),
                            })}
                          </p>
                          {item.key && <p>{t("settings.backupExactKey", { key: item.key })}</p>}
                          {item.prefix && (
                            <p>{t("settings.backupPrefix", { prefix: item.prefix })}</p>
                          )}
                          {item.archive_sha256 && (
                            <p>
                              {t("settings.backupSha256", {
                                digest: shortOpaque(item.archive_sha256),
                              })}
                            </p>
                          )}
                        </div>
                      </details>
                    </div>
                  </div>
                  <Button
                    className="w-full sm:w-auto"
                    size="xs"
                    variant="outline"
                    loading={verifying === sourceRef}
                    disabled={busy || !item.source_ref}
                    title={!item.source_ref ? t("settings.backupSourceUnavailable") : undefined}
                    onClick={() => void checkBackup(item)}
                  >
                    <RefreshCw className="h-3.5 w-3.5" />
                    {uiText(" Verify")}
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
        title={uiText("Repair this finding?")}
        description={uiText(
          "PrintStash will apply the targeted repair and record the action in the audit log. Original Artifact bytes are never replaced by thumbnail or metadata repairs.",
        )}
        confirmLabel={uiText("Repair")}
        busy={repairing}
      />
    </div>
  );
}
