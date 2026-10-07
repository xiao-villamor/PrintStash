import { useEffect, useRef, useState } from "react";
import { ArrowRight, ArrowRightLeft, RefreshCw } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input, inputClasses } from "@/components/ui/input";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { Localized } from "@/components/ui/localized";
import { StorageProviderPicker, defaultProviderValues } from "@/components/storage-provider-picker";
import { Skeleton } from "@/components/ui/skeleton";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@/lib/auth-context";
import { onAuthChange } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";
import { backupSourcesOptions } from "@/lib/queries/settings-backup-catalog";
import { storageProvidersOptions, storageReadDenied } from "@/lib/queries/settings-storage";
import {
  migrationHistoryOptions,
  migrationRunOptions,
  migrationReportOptions,
  useMigrationCommand,
  type MigrationAction,
} from "@/lib/queries/settings-vault-migration";
import type { BackupMeta } from "@/lib/api/backup";
import type { VaultMigrationRun, VaultMigrationState } from "@/lib/api/vault-migration";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { parseApiError } from "@/lib/errors";
import { providerFormError } from "@/lib/storage-provider-form";
import type { StorageProviderConfigValues } from "@/types";

const labels = {
  planned: "migration.planReady",
  copying: "migration.copying",
  ready: "migration.ready",
  activating: "migration.activating",
  active: "migration.active",
  cleaned: "migration.cleaned",
  discarded: "migration.discarded",
  paused: "migration.paused",
  cutover_pending: "migration.cutover_pending",
  draining: "migration.draining",
  delta_copy: "migration.delta_copy",
  verifying: "migration.verifying",
  recovery_required: "migration.recovery",
  failed: "migration.failed",
  complete: "migration.complete",
} satisfies Record<VaultMigrationState, MessageKey>;
const errors = {
  storage_capacity_exceeded: "migration.capacityError",
  storage_capacity_unavailable: "migration.capacityUnavailable",
  storage_capacity_volume_changed: "migration.identityError",
  migration_audit_busy: "migration.auditBusy",
  migration_audit_failed: "migration.auditFailed",
  migration_full_audit_required: "migration.fullAuditRequired",
  migration_recent_backup_required: "migration.backupError",
  migration_verified_compatible_backup_required: "migration.backupError",
  migration_destination_not_empty: "migration.conflictError",
  migration_destination_collision: "migration.conflictError",
  migration_destination_matches_source: "migration.conflictError",
  migration_destination_root_unavailable: "migration.capacityUnavailable",
  migration_destination_binding_invalid: "migration.identityError",
  migration_source_unhealthy: "migration.identityError",
  migration_destination_identity_changed: "migration.identityError",
  migration_activated_destination_unhealthy: "migration.identityError",
  migration_source_configuration_unavailable: "migration.identityError",
  migration_destination_exact_receipts_required: "migration.providerError",
  migration_destination_verification_failed: "migration.verificationError",
  migration_source_hash_mismatch: "migration.verificationError",
  migration_partial_destination: "migration.verificationError",
  migration_plan_stale: "migration.staleError",
  migration_source_generation_changed: "migration.staleError",
  migration_already_running: "migration.runningError",
  migration_source_grace_required: "migration.graceError",
  migration_readers_busy: "migration.readersBusy",
  migration_recovery_ambiguous: "migration.recoveryHelp",
  migration_recovery_destination_changed: "migration.recoveryHelp",
} satisfies Record<string, MessageKey>;
function errorMessage(code: string): MessageKey {
  return Object.entries(errors).find(([name]) => name === code)?.[1] ?? "migration.failure";
}
function backupKey(backup: BackupMeta): string {
  return `${backup.backup_id}:${backup.source_ref ?? ""}`;
}
function location(config: StorageProviderConfigValues): string {
  return [config.data_dir, config.thumb_dir, config.bucket, config.root, config.endpoint_url]
    .filter(Boolean)
    .join(" · ");
}
const finalizing: VaultMigrationState[] = [
  "cutover_pending",
  "draining",
  "delta_copy",
  "verifying",
  "activating",
];
type Confirmation = "start" | "cutover" | "source" | "discard" | "manual";
type ReviewedMigration = {
  kind: Confirmation;
  target: VaultMigrationRun;
  backup: BackupMeta | undefined;
};

export function VaultMigrationPanel() {
  const { t, locale } = useI18n();
  const { user } = useAuth();
  const [session] = useState(getSessionVersion);
  const [retired, setRetired] = useState(false);
  const mounted = useRef(true);
  const current = () => mounted.current && session === getSessionVersion();
  const enabled = !retired && !!user?.is_superuser;
  const history = useQuery({ ...migrationHistoryOptions(), enabled });
  const providersQuery = useQuery({ ...storageProvidersOptions(), enabled });
  const backupsQuery = useQuery({ ...backupSourcesOptions(), enabled });
  const runs =
    enabled && !storageReadDenied(parseApiError(history.error)) ? (history.data ?? []) : [];
  // undefined selects the newest run on entry; null deliberately starts a new plan.
  const [selection, setSelection] = useState<string | null | undefined>();
  const runId = selection === undefined ? (runs[0]?.id ?? null) : selection;
  // Capture the entry identity once; later history updates do not navigate the view.
  if (enabled && selection === undefined && history.data)
    setSelection(history.data[0]?.id ?? null);
  const listed = runs.find((row) => row.id === runId);
  const command = useMigrationCommand();
  const detail = useQuery({
    ...migrationRunOptions(runId),
    enabled: enabled && runId !== null && !command.pending,
    initialData: listed,
    initialDataUpdatedAt: history.dataUpdatedAt,
  });
  const denied =
    !enabled ||
    storageReadDenied(parseApiError(history.error)) ||
    storageReadDenied(parseApiError(detail.error));
  const run = denied
    ? null
    : ((history.dataUpdatedAt > detail.dataUpdatedAt ? listed : detail.data) ?? listed ?? null);
  const reportQuery = useQuery({
    ...migrationReportOptions(runId, run),
    enabled: enabled && !denied && runId !== null && !command.pending,
  });
  const report = !reportQuery.isError ? reportQuery.data : undefined;
  const providers = providersQuery.isError ? [] : (providersQuery.data ?? []);
  const backups = backupsQuery.isError ? [] : (backupsQuery.data ?? []);
  const [providerDraft, setProviderId] = useState<string | null>(null);
  const providerId =
    providerDraft ?? providers.find((row) => row.id === "local")?.id ?? providers[0]?.id ?? "";
  const selectedProvider = providers.find((provider) => provider.id === providerId);
  const [draftValues, setValues] = useState<StorageProviderConfigValues | null>(null);
  const values = draftValues ?? (selectedProvider ? defaultProviderValues(selectedProvider) : {});
  const [backupId, setBackupId] = useState("");
  const [now, setNow] = useState(Date.now);
  const [refreshing, setRefreshing] = useState(false);
  const busy = command.pending || refreshing;
  const loading =
    enabled && (history.isPending || providersQuery.isPending || backupsQuery.isPending);
  const [retentionDays, setRetentionDays] = useState(7);
  const [concurrency, setConcurrency] = useState(1);
  const [bandwidth, setBandwidth] = useState("");
  const [actionError, setError] = useState<MessageKey | null>(null);
  const readError =
    history.error ??
    detail.error ??
    providersQuery.error ??
    backupsQuery.error ??
    reportQuery.error;
  const error = actionError ?? (readError ? errorMessage(parseApiError(readError).code) : null);
  const [review, setReview] = useState<ReviewedMigration | null>(null);
  const confirmation = review?.kind ?? null;
  const setConfirmation = (kind: Confirmation | null) =>
    setReview(kind && run ? { kind, target: run, backup: selectedBackup } : null);
  const selectedBackup = backups.find((backup) => backupKey(backup) === backupId);
  const providerAvailable =
    selectedProvider?.available &&
    selectedProvider.selectable &&
    selectedProvider.uses?.vault?.available !== false;
  const running = busy || denied || history.isError || detail.isError;
  const fullAuditState = run?.full_audit?.state;
  const date = (value: string) => new Date(value).toLocaleString(locale);
  useEffect(() => {
    mounted.current = true;
    const release = onAuthChange(() => {
      setRetired(true);
      setReview(null);
      setValues(null);
      setBackupId("");
      setError(null);
    });
    // This local clock is for expiry/grace labels, not remote status polling.
    const clock = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => {
      mounted.current = false;
      release();
      window.clearInterval(clock);
    };
  }, []);
  async function refresh() {
    if (!current() || busy) return;
    setRefreshing(true);
    setError(null);
    setNow(Date.now());
    try {
      await Promise.all([
        command.execute({ kind: "refresh" }),
        providersQuery.refetch(),
        backupsQuery.refetch(),
      ]);
    } catch (cause) {
      if (current()) setError(errorMessage(parseApiError(cause).code));
    } finally {
      if (current()) setRefreshing(false);
    }
  }
  async function downloadReport() {
    if (!run || running) return;
    setError(null);
    try {
      await command.execute({ kind: "download", target: run });
    } catch (cause) {
      if (current()) setError(errorMessage(parseApiError(cause).code));
    }
  }
  async function preflight() {
    if (!current() || running) return;
    if (!selectedBackup || !selectedProvider || !providerAvailable) return;
    if (providerFormError(selectedProvider, values, "vault")) {
      setError("migration.formError");
      return;
    }
    if (
      !Number.isInteger(retentionDays) ||
      retentionDays < 0 ||
      retentionDays > 3650 ||
      !Number.isInteger(concurrency) ||
      concurrency < 1 ||
      concurrency > 4 ||
      (bandwidth !== "" && (!Number.isSafeInteger(Number(bandwidth)) || Number(bandwidth) < 1024))
    ) {
      setError("migration.policyInvalid");
      return;
    }
    setError(null);
    try {
      const destination = Object.fromEntries(
        Object.entries(values).filter(
          ([name, value]) => name !== "secret_fields_set" && value !== "",
        ),
      );
      const next = await command.execute({
        kind: "preflight",
        payload: {
          destination: { ...destination, provider: providerId },
          backup_id: selectedBackup.backup_id,
          backup_source_ref: selectedBackup.source_ref,
          policy: {
            retention_days: retentionDays,
            concurrency,
            bandwidth_bytes_per_second: bandwidth === "" ? null : Number(bandwidth),
          },
        },
      });
      if (current() && next) {
        setSelection(next.id);
        setValues(null);
      }
    } catch (cause) {
      if (current()) setError(errorMessage(parseApiError(cause).code));
    }
  }
  async function act(action: MigrationAction, target = run, backup = selectedBackup) {
    if (!target || running || !current()) return;
    setError(null);
    try {
      const next = await command.execute({ kind: action, target, backup });
      if (current()) {
        setReview(null);
        if (next?.state === "active") setBackupId("");
      }
    } catch (cause) {
      if (current()) {
        setError(errorMessage(parseApiError(cause).code));
        setReview(null);
      }
    }
  }

  const cleanupAllowed =
    run?.state === "active" &&
    run.cleanup_after !== null &&
    new Date(run.cleanup_after).getTime() <= now &&
    run.full_audit?.state === "completed" &&
    run.full_audit.critical_count === 0;
  const planExpired = run?.state === "planned" && new Date(run.expires_at).getTime() <= now;
  const backupPicker = (
    <div className="space-y-2">
      <label htmlFor="migration-backup" className="text-sm font-medium">
        {t("migration.backup")}
      </label>
      <select
        id="migration-backup"
        className={inputClasses}
        value={backupId}
        disabled={running || backupsQuery.isError || backups.length === 0}
        onChange={(event) => setBackupId(event.target.value)}
      >
        <option value="">{t("migration.chooseBackup")}</option>
        {backups.map((backup) => (
          <option key={backupKey(backup)} value={backupKey(backup)}>
            {date(backup.created_at)} · {backup.location} · {backup.backup_id}
          </option>
        ))}
      </select>
      <p className="text-xs text-muted-foreground">
        {t(
          backupsQuery.isError
            ? "migration.failure"
            : backups.length
              ? "migration.backupHelp"
              : "migration.backupEmpty",
        )}
      </p>
      <a
        href="/settings?section=backup"
        className="text-xs text-primary underline underline-offset-4"
      >
        {t("migration.manageBackups")}
      </a>
    </div>
  );

  return (
    <Localized>
      <Card
        id="vault-migration"
        role="region"
        aria-label={t("migration.title")}
        className="overflow-hidden"
      >
        <div className="flex flex-wrap items-start justify-between gap-3 border-b px-4 py-4 sm:px-5">
          <div className="flex min-w-0 items-start gap-3">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-muted">
              <ArrowRightLeft className="h-4 w-4" aria-hidden />
            </div>
            <div>
              <h3 className="text-sm font-semibold">{t("migration.title")}</h3>
              <p className="mt-1 text-xs text-muted-foreground">{t("migration.description")}</p>
            </div>
          </div>
          <Button
            variant="ghost"
            size="sm"
            disabled={loading || busy || !enabled}
            onClick={() => void refresh()}
            aria-label={t("migration.reload")}
          >
            <RefreshCw className="h-4 w-4" aria-hidden />
            {t("migration.reload")}
          </Button>
        </div>
        {loading ? (
          <div role="status" aria-label={t("migration.loading")} className="space-y-4 p-4 sm:p-5">
            <Skeleton className="h-5 w-40" />
            <div className="grid gap-2 sm:grid-cols-2">
              {[0, 1, 2, 3].map((item) => (
                <Skeleton key={item} className="h-12 w-full" />
              ))}
            </div>
            <Skeleton className="h-5 w-28" />
            <div className="grid gap-2 sm:grid-cols-2">
              {[0, 1].map((item) => (
                <Skeleton key={item} className="h-16 w-full" />
              ))}
            </div>
            <span className="sr-only">{t("migration.loading")}</span>
          </div>
        ) : (
          <>
            {error && (
              <p role="alert" className="border-b bg-destructive/10 p-4 text-sm text-destructive">
                {t(error)}
              </p>
            )}
            {runs.length > 1 && (
              <div className="border-b p-4">
                <label htmlFor="migration-history" className="mb-2 block text-xs font-medium">
                  {t("migration.history")}
                </label>
                <select
                  id="migration-history"
                  className={inputClasses}
                  value={run?.id ?? ""}
                  disabled={running}
                  onChange={(event) => {
                    setSelection(event.target.value || null);
                    setReview(null);
                    setError(null);
                  }}
                >
                  <option value="">{t("migration.newPlan")}</option>
                  {runs.map((item) => (
                    <option key={item.id} value={item.id}>
                      {t(labels[item.state])} · {item.id}
                    </option>
                  ))}
                </select>
              </div>
            )}
            {denied || (history.isError && !history.data) ? null : !run ? (
              <div className="space-y-5 p-4 sm:p-5">
                <h4 className="text-sm font-semibold">{t("migration.destination")}</h4>
                <StorageProviderPicker
                  providers={providers}
                  providerId={providerId}
                  values={values}
                  disabled={running}
                  onProviderChange={(provider) => {
                    setProviderId(provider.id);
                    setValues(defaultProviderValues(provider));
                  }}
                  onValueChange={(name, value) =>
                    setValues((current) => ({ ...(current ?? values), [name]: value }))
                  }
                />
                <details className="border-t pt-4">
                  <summary className="cursor-pointer text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                    {t("migration.policy")} · {t("similarity.advanced")}
                  </summary>
                  <fieldset className="mt-3 space-y-3">
                    <div className="grid gap-3 sm:grid-cols-3">
                      <label className="space-y-1 text-xs">
                        {t("migration.retentionDays")}
                        <Input
                          type="number"
                          min={0}
                          max={3650}
                          value={retentionDays}
                          disabled={running}
                          onChange={(event) => setRetentionDays(Number(event.target.value))}
                        />
                      </label>
                      <label className="space-y-1 text-xs">
                        {t("migration.concurrency")}
                        <Input
                          type="number"
                          min={1}
                          max={4}
                          value={concurrency}
                          disabled={running}
                          onChange={(event) => setConcurrency(Number(event.target.value))}
                        />
                      </label>
                      <label className="space-y-1 text-xs">
                        {t("migration.bandwidth")}
                        <Input
                          type="number"
                          min={1024}
                          value={bandwidth}
                          disabled={running}
                          onChange={(event) => setBandwidth(event.target.value)}
                        />
                      </label>
                    </div>
                    <p className="text-xs text-muted-foreground">{t("migration.policyHelp")}</p>
                  </fieldset>
                </details>
                {backupPicker}
                <p className="text-xs text-muted-foreground">{t("migration.preflightHelp")}</p>
                <Button
                  loading={busy}
                  disabled={!selectedBackup || !providerAvailable}
                  onClick={() => void preflight()}
                >
                  {t("migration.preflight")}
                </Button>
              </div>
            ) : (
              <div className="divide-y divide-border">
                <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:p-5">
                  <div className="min-w-0 flex-1">
                    <p className="text-xs text-muted-foreground">{t("migration.source")}</p>
                    <p className="mt-1 text-sm font-medium">{String(run.source.provider ?? "")}</p>
                    <p className="break-all text-xs text-muted-foreground">
                      {location(run.source)}
                    </p>
                  </div>
                  <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                  <div className="min-w-0 flex-1">
                    <p className="text-xs text-muted-foreground">{t("migration.destination")}</p>
                    <p className="mt-1 text-sm font-medium">
                      {String(run.destination.provider ?? "")}
                    </p>
                    <p className="break-all text-xs text-muted-foreground">
                      {location(run.destination)}
                    </p>
                  </div>
                </div>
                <div className="space-y-3 p-4 sm:p-5" aria-live="polite">
                  <h4 className="text-sm font-semibold">
                    {t(run.recovery_required ? "migration.recovery" : labels[run.state])}
                  </h4>
                  <p className="text-xs tabular-nums text-muted-foreground">
                    {t("migration.progress", {
                      verified: run.verified_objects.toLocaleString(locale),
                      total: run.objects.toLocaleString(locale),
                      bytes: run.bytes.toLocaleString(locale),
                    })}
                  </p>
                  <p className="break-all text-xs text-muted-foreground">
                    {t("migration.identity", { identity: run.source_provider_ref })} →{" "}
                    {run.destination_provider_ref}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {t("migration.policySummary", {
                      days: String(run.policy.retention_days),
                      concurrency: String(run.policy.concurrency),
                      bandwidth:
                        run.policy.bandwidth_bytes_per_second === null
                          ? t("migration.unlimited")
                          : run.policy.bandwidth_bytes_per_second.toLocaleString(locale),
                    })}
                  </p>
                  <p className="break-all text-xs text-muted-foreground">
                    {t("migration.backupProof", {
                      id: run.backup_summary.backup_id,
                      source: run.backup_summary.source_ref ?? t("migration.unknown"),
                      date: date(run.backup_summary.verified_at),
                    })}
                  </p>
                  {run.pre_audit && (
                    <p className="text-xs text-muted-foreground">
                      {t("migration.preAudit")} ·{" "}
                      {t("migration.auditResult", {
                        state: run.pre_audit.state,
                        critical: String(run.pre_audit.critical_count),
                        warnings: String(run.pre_audit.warning_count),
                      })}
                    </p>
                  )}
                  <dl className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
                    {[
                      {
                        key: "copied",
                        label: "migration.copied" as const,
                        objects: run.copied_objects,
                        bytes: run.copied_bytes,
                      },
                      {
                        key: "verified",
                        label: "migration.verified" as const,
                        objects: run.verified_objects,
                        bytes: run.verified_bytes,
                      },
                      {
                        key: "skipped",
                        label: "migration.skipped" as const,
                        objects: run.skipped_objects,
                        bytes: run.skipped_bytes,
                      },
                      {
                        key: "failed",
                        label: "migration.failedCount" as const,
                        objects: run.failed_objects,
                        bytes: run.failed_bytes,
                      },
                    ].map((metric) => (
                      <div key={metric.key}>
                        <dt className="text-muted-foreground">{t(metric.label)}</dt>
                        <dd className="mt-1 tabular-nums">
                          {t("migration.metric", {
                            objects: metric.objects.toLocaleString(locale),
                            bytes: metric.bytes.toLocaleString(locale),
                          })}
                        </dd>
                      </div>
                    ))}
                  </dl>
                  <p className="text-xs text-muted-foreground">
                    {t("migration.delta", { count: String(run.delta_objects) })} ·{" "}
                    {t("migration.throughput", {
                      value:
                        run.throughput_bytes_per_second === null
                          ? t("migration.unknown")
                          : Math.round(run.throughput_bytes_per_second).toLocaleString(locale),
                    })}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {t("migration.activity", {
                      date: run.last_activity_at
                        ? date(run.last_activity_at)
                        : t("migration.unknown"),
                    })}
                  </p>
                  {run.capacity_resources.map((resource, index) => (
                    <p key={`${resource.role}:${index}`} className="text-xs text-muted-foreground">
                      {t("migration.space", {
                        role: resource.role,
                        required: resource.required_bytes.toLocaleString(locale),
                        available:
                          resource.available_bytes === null
                            ? t("migration.unknown")
                            : resource.available_bytes.toLocaleString(locale),
                      })}
                    </p>
                  ))}
                  {run.phase_history.length > 0 && (
                    <details className="text-xs">
                      <summary className="cursor-pointer font-medium">
                        {t("migration.timeline")}
                      </summary>
                      <ol className="mt-2 space-y-1">
                        {run.phase_history.map((phase, index) => (
                          <li key={`${phase.phase}:${phase.at}:${index}`}>
                            {Object.entries(labels).find(([key]) => key === phase.phase)?.[1]
                              ? t(Object.entries(labels).find(([key]) => key === phase.phase)![1])
                              : phase.phase}{" "}
                            · {date(phase.at)}
                          </li>
                        ))}
                      </ol>
                    </details>
                  )}
                  {report?.id === run.id && report.resource_kind_totals.length > 0 && (
                    <details className="text-xs">
                      <summary className="cursor-pointer font-medium">
                        {t("migration.resourceKinds")}
                      </summary>
                      <ul className="mt-2 space-y-1">
                        {report.resource_kind_totals.map((kind) => (
                          <li key={kind.resource_type}>
                            {kind.resource_type} ·{" "}
                            {t("migration.metric", {
                              objects: kind.objects.toLocaleString(locale),
                              bytes: kind.bytes.toLocaleString(locale),
                            })}
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                  {report?.id === run.id && report.recent_failures.length > 0 && (
                    <div role="alert" className="text-sm text-destructive">
                      <p>{t("migration.failedRecent")}</p>
                      <ul className="mt-2 space-y-1">
                        {report.recent_failures.map((failure, index) => (
                          <li key={`${failure.object_id}:${index}`}>
                            {t(errorMessage(failure.code))}{" "}
                            {t(
                              failure.retryable ? "migration.retryable" : "migration.notRetryable",
                            )}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  <progress
                    className="h-2 w-full accent-primary"
                    max={Math.max(1, run.objects)}
                    value={run.verified_objects}
                    aria-label={t("migration.progressLabel")}
                  />
                  {run.error_code && (
                    <p role="alert" className="text-sm text-destructive">
                      {t(errorMessage(run.error_code))}
                    </p>
                  )}
                  {run.capacity_warnings.length > 0 && (
                    <p className="text-sm text-warning">{t("migration.capacityUnknown")}</p>
                  )}
                  {run.recovery_required ? (
                    <>
                      <p className="text-sm text-muted-foreground">{t("migration.recoveryHelp")}</p>
                      <Button loading={busy} onClick={() => void act("recover")}>
                        {t("migration.recover")}
                      </Button>
                    </>
                  ) : (
                    <>
                      {run.state === "planned" && (
                        <>
                          <p className="text-xs text-muted-foreground">
                            {t("migration.capacityChecked")}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            {t("migration.planExpiry", { date: date(run.expires_at) })}
                          </p>
                          {planExpired && (
                            <p role="alert" className="text-sm text-warning">
                              {t("migration.planExpired")}
                            </p>
                          )}
                          <Button
                            disabled={running || planExpired}
                            onClick={() => setConfirmation("start")}
                          >
                            {t("migration.start")}
                          </Button>
                        </>
                      )}
                      {["copying", "paused"].includes(run.state) && (
                        <>
                          <p className="text-sm text-muted-foreground">{t("migration.online")}</p>
                          <p className="text-xs text-muted-foreground">
                            {t("migration.policyHelp")}
                          </p>
                          {run.state === "copying" ? (
                            <>
                              <p className="text-xs text-muted-foreground">
                                {t("migration.continueBackground")}
                              </p>
                              <Button
                                disabled={running}
                                variant="outline"
                                onClick={() => void act("pause")}
                              >
                                {t("migration.pause")}
                              </Button>
                            </>
                          ) : (
                            <>
                              <p className="text-xs text-muted-foreground">
                                {t("migration.paused")}
                              </p>
                              <Button disabled={running} onClick={() => void act("resume")}>
                                {t("migration.resume")}
                              </Button>
                            </>
                          )}
                        </>
                      )}
                      {run.state === "failed" && (
                        <p className="text-sm text-muted-foreground">
                          {t(run.retryable ? "migration.retryable" : "migration.notRetryable")}
                        </p>
                      )}
                      {run.retryable && run.failed_objects > 0 && (
                        <Button disabled={running} onClick={() => void act("retry")}>
                          {t("migration.retry")}
                        </Button>
                      )}
                      {run.state === "ready" && (
                        <>
                          <p className="text-sm text-muted-foreground">
                            {t("migration.readyHelp")}
                          </p>
                          <Button
                            loading={busy}
                            disabled={running}
                            onClick={() => setConfirmation("cutover")}
                          >
                            {t("migration.switch")}
                          </Button>
                        </>
                      )}
                      {(finalizing.includes(run.state) || (busy && confirmation === "cutover")) && (
                        <p role="status" className="text-sm text-warning">
                          {t("migration.freeze")}
                        </p>
                      )}
                      {["active", "cleaned", "complete"].includes(run.state) && (
                        <>
                          <p className="text-sm text-muted-foreground">{t("migration.rollback")}</p>
                          <h5 className="text-sm font-semibold">{t("migration.audit")}</h5>
                          <p className="text-xs text-muted-foreground">
                            {run.post_audit
                              ? t("migration.auditResult", {
                                  state: run.post_audit.state,
                                  critical: String(run.post_audit.critical_count),
                                  warnings: String(run.post_audit.warning_count),
                                })
                              : t("migration.auditPending")}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            {t("migration.fullAuditRequired")}
                          </p>
                          {run.full_audit && (
                            <p className="text-xs text-muted-foreground">
                              {t("migration.auditResult", {
                                state: run.full_audit.state,
                                critical: String(run.full_audit.critical_count),
                                warnings: String(run.full_audit.warning_count),
                              })}
                            </p>
                          )}
                          <Button
                            variant="outline"
                            disabled={
                              running || ["pending", "running"].includes(fullAuditState ?? "")
                            }
                            onClick={() => void act("audit")}
                          >
                            {t("migration.fullAudit")}
                          </Button>
                          {run.source_retained && (
                            <>
                              <p className="text-sm text-muted-foreground">
                                {t("migration.retained")}
                              </p>
                              {run.cleanup_after && (
                                <p className="text-xs text-muted-foreground">
                                  {t("migration.grace", { date: date(run.cleanup_after) })}
                                </p>
                              )}
                              {run.cleanup_outcome === "retained_indefinitely" && (
                                <p className="text-sm">{t("migration.indefinitely")}</p>
                              )}
                              {run.cleanup_outcome === "manual_cleanup" ? (
                                <p className="text-sm">{t("migration.manualOutcome")}</p>
                              ) : (
                                <>
                                  <p className="text-xs text-muted-foreground">
                                    {t("migration.freshBackup")}
                                  </p>
                                  {backupPicker}
                                  <div className="flex flex-wrap gap-2">
                                    <Button
                                      variant="outline"
                                      disabled={!cleanupAllowed || !selectedBackup || running}
                                      onClick={() => setConfirmation("source")}
                                    >
                                      {t("migration.cleanup")}
                                    </Button>
                                    <Button
                                      variant="outline"
                                      disabled={running}
                                      onClick={() => void act("retain")}
                                    >
                                      {t("migration.retain")}
                                    </Button>
                                    <Button
                                      variant="ghost"
                                      disabled={running}
                                      onClick={() => setConfirmation("manual")}
                                    >
                                      {t("migration.manual")}
                                    </Button>
                                  </div>
                                </>
                              )}
                            </>
                          )}
                        </>
                      )}
                    </>
                  )}
                  <div className="flex flex-wrap items-center gap-3">
                    <Button
                      variant="outline"
                      disabled={running}
                      onClick={() => void downloadReport()}
                    >
                      {t("migration.report")}
                    </Button>
                    <a
                      className="text-xs text-primary underline underline-offset-4"
                      href="/settings?section=notifications"
                    >
                      {t("migration.notifications")}
                    </a>
                  </div>
                  <a
                    className="block text-xs text-primary underline underline-offset-4"
                    href="https://github.com/xiao-villamor/PrintStash/blob/main/docs/storage-migration.md"
                    target="_blank"
                    rel="noreferrer"
                  >
                    {t("migration.recoveryDocs")}
                  </a>
                  <p className="text-xs text-muted-foreground">
                    {t("migration.notificationEvents", { count: String(run.notification_events) })}
                  </p>
                  {run.cleanup_findings.length > 0 && (
                    <div role="alert" className="space-y-2 text-sm text-warning">
                      <p>{t("migration.findings")}</p>
                      <ul className="list-disc space-y-1 pl-5 text-xs">
                        {run.cleanup_findings.map((finding) => (
                          <li key={`${finding.object_id}:${finding.code}`}>
                            {t(
                              finding.code === "ownership_unverified"
                                ? "migration.findingOwnership"
                                : finding.code === "identity_changed"
                                  ? "migration.findingIdentity"
                                  : finding.code === "content_changed"
                                    ? "migration.findingContent"
                                    : "migration.findingUnknown",
                              { id: String(finding.object_id) },
                            )}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
                {!run.recovery_required &&
                  ["planned", "paused", "ready", "failed"].includes(run.state) && (
                    <div className="p-4">
                      <Button
                        variant="outline"
                        disabled={running}
                        onClick={() => setConfirmation("discard")}
                      >
                        {t("migration.discard")}
                      </Button>
                    </div>
                  )}
                {["active", "cleaned", "discarded", "complete"].includes(run.state) && (
                  <div className="p-4">
                    <Button
                      variant="outline"
                      disabled={running}
                      onClick={() => {
                        setSelection(null);
                        setError(null);
                        setBackupId("");
                        setValues(selectedProvider ? defaultProviderValues(selectedProvider) : {});
                      }}
                    >
                      {t("migration.newPlan")}
                    </Button>
                  </div>
                )}
              </div>
            )}
          </>
        )}
        <ConfirmModal
          open={confirmation !== null && !denied}
          onClose={() => {
            if (!busy) setConfirmation(null);
          }}
          busy={busy}
          onConfirm={() => {
            if (review) void act(review.kind, review.target, review.backup);
          }}
          title={t(
            confirmation === "start"
              ? "migration.start"
              : confirmation === "manual"
                ? "migration.manualTitle"
                : confirmation === "cutover"
                  ? "migration.switchTitle"
                  : confirmation === "source"
                    ? "migration.cleanupTitle"
                    : "migration.discardTitle",
          )}
          description={
            confirmation === "start" && review
              ? t("migration.startDescription", {
                  source: review.target.source_provider_ref,
                  destination: review.target.destination_provider_ref,
                  backup: review.target.backup_summary.backup_id,
                  date: date(review.target.backup_summary.verified_at),
                })
              : t(
                  confirmation === "manual"
                    ? "migration.manualDescription"
                    : confirmation === "cutover"
                      ? "migration.switchDescription"
                      : confirmation === "source"
                        ? "migration.cleanupDescription"
                        : "migration.discardDescription",
                )
          }
          confirmLabel={t(
            confirmation === "start"
              ? "migration.start"
              : confirmation === "manual"
                ? "migration.manual"
                : confirmation === "cutover"
                  ? "migration.switch"
                  : confirmation === "source"
                    ? "migration.cleanup"
                    : "migration.discard",
          )}
        />
      </Card>
    </Localized>
  );
}
