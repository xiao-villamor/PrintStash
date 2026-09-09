import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowRight, ArrowRightLeft, RefreshCw } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input, inputClasses } from "@/components/ui/input";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { Localized } from "@/components/ui/localized";
import { StorageProviderPicker, defaultProviderValues } from "@/components/storage-provider-picker";
import { getStorageProviders } from "@/lib/api/config";
import { listBackupSources, type BackupMeta } from "@/lib/api/backup";
import {
  pauseVaultMigration,
  resumeVaultMigration,
  retryVaultMigration,
  auditVaultMigration,
  retainVaultMigration,
  downloadVaultMigrationReport,
  getVaultMigrationReport,
  type VaultMigrationReport,
  cleanupVaultMigration,
  cutoverVaultMigration,
  getVaultMigration,
  listVaultMigrations,
  preflightVaultMigration,
  recoverVaultMigration,
  startVaultMigration,
  type VaultMigrationRun,
  type VaultMigrationState,
} from "@/lib/api/vault-migration";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { parseApiError } from "@/lib/errors";
import { providerFormError } from "@/lib/storage-provider-form";
import type { StorageProvider, StorageProviderConfigValues } from "@/types";

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
type MigrationAction =
  | Confirmation
  | "recover"
  | "start"
  | "resume"
  | "pause"
  | "retry"
  | "audit"
  | "retain";

export function VaultMigrationPanel() {
  const { t, locale } = useI18n();
  const [runs, setRuns] = useState<VaultMigrationRun[]>([]);
  const [run, setRun] = useState<VaultMigrationRun | null>(null);
  const [report, setReport] = useState<VaultMigrationReport | null>(null);
  const [providers, setProviders] = useState<StorageProvider[]>([]);
  const [providerId, setProviderId] = useState("local");
  const [values, setValues] = useState<StorageProviderConfigValues>({});
  const [backups, setBackups] = useState<BackupMeta[]>([]);
  const [backupId, setBackupId] = useState("");
  const [loading, setLoading] = useState(true);
  const [now, setNow] = useState(Date.now);
  const [busy, setBusy] = useState(false);
  const [retentionDays, setRetentionDays] = useState(7);
  const [concurrency, setConcurrency] = useState(1);
  const [bandwidth, setBandwidth] = useState("");
  const [error, setError] = useState<MessageKey | null>(null);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const mounted = useRef(true);
  const selectedBackup = backups.find((backup) => backupKey(backup) === backupId);
  const selectedProvider = providers.find((provider) => provider.id === providerId);
  const providerAvailable =
    selectedProvider?.available &&
    selectedProvider.selectable &&
    selectedProvider.uses?.vault?.available !== false;
  const running = busy;
  const date = (value: string) => new Date(value).toLocaleString(locale);
  const update = useCallback((next: VaultMigrationRun) => {
    if (!mounted.current) return;
    setRun(next);
    setRuns((current) => [next, ...current.filter((item) => item.id !== next.id)]);
  }, []);

  useEffect(() => {
    mounted.current = true;
    const clock = window.setInterval(() => setNow(Date.now()), 60_000);
    void Promise.allSettled([
      listVaultMigrations(),
      getStorageProviders(),
      listBackupSources(),
    ]).then(([migrations, catalogue, sources]) => {
      if (!mounted.current) return;
      if (migrations.status === "fulfilled") {
        setRuns(migrations.value);
        setRun(migrations.value[0] ?? null);
      } else setError("migration.failure");
      if (catalogue.status === "fulfilled") {
        setProviders(catalogue.value);
        const initial =
          catalogue.value.find((provider) => provider.id === "local") ?? catalogue.value[0];
        if (initial) {
          setProviderId(initial.id);
          setValues(defaultProviderValues(initial));
        }
      }
      if (sources.status === "fulfilled") setBackups(sources.value);
      setLoading(false);
    });
    return () => {
      window.clearInterval(clock);
      mounted.current = false;
    };
  }, []);

  const runId = run?.id;
  const runState = run?.state;
  const fullAuditState = run?.full_audit?.state;
  const failedObjects = run?.failed_objects;

  useEffect(() => {
    if (!runId || !runState) return;
    const polling =
      ["copying", ...finalizing].includes(runState) ||
      ["pending", "running"].includes(fullAuditState ?? "");
    if (!polling) return;
    let pending = false;
    let stopped = false;
    const timer = window.setInterval(() => {
      if (pending) return;
      pending = true;
      void getVaultMigration(runId)
        .then((next) => {
          if (!stopped) update(next);
        })
        .catch((cause) => {
          if (!stopped) setError(errorMessage(parseApiError(cause).code));
        })
        .finally(() => {
          pending = false;
        });
    }, 2000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [runId, runState, fullAuditState, update]);

  useEffect(() => {
    if (!runId) return;
    let stopped = false;
    void getVaultMigrationReport(runId)
      .then((next) => {
        if (!stopped) setReport(next);
      })
      .catch(() => {
        /* The main run remains readable when the detailed report is unavailable. */
      });
    return () => {
      stopped = true;
    };
  }, [runId, runState, failedObjects]);

  async function downloadReport() {
    if (!run) return;
    setBusy(true);
    try {
      await downloadVaultMigrationReport(run.id);
    } catch (cause) {
      setError(errorMessage(parseApiError(cause).code));
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  async function refresh() {
    setBusy(true);
    setError(null);
    setNow(Date.now());
    try {
      const current = await listVaultMigrations();
      if (!mounted.current) return;
      setRuns(current);
      setRun(current.find((item) => item.id === run?.id) ?? current[0] ?? null);
      const [sources, catalogue] = await Promise.all([listBackupSources(), getStorageProviders()]);
      if (mounted.current) {
        setBackups(sources);
        setProviders(catalogue);
      }
    } catch (cause) {
      if (mounted.current) setError(errorMessage(parseApiError(cause).code));
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  async function preflight() {
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
    setBusy(true);
    setError(null);
    try {
      const destination = Object.fromEntries(
        Object.entries(values).filter(
          ([name, value]) => name !== "secret_fields_set" && value !== "",
        ),
      );
      update(
        await preflightVaultMigration({
          destination: { ...destination, provider: providerId },
          backup_id: selectedBackup.backup_id,
          backup_source_ref: selectedBackup.source_ref,
          policy: {
            retention_days: retentionDays,
            concurrency,
            bandwidth_bytes_per_second: bandwidth === "" ? null : Number(bandwidth),
          },
        }),
      );
      // Candidate secrets are write-only and are never retained in a plan view.
      setValues({});
    } catch (cause) {
      setError(errorMessage(parseApiError(cause).code));
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  async function act(action: MigrationAction) {
    if (!run || running) return;
    setBusy(true);
    setError(null);
    try {
      const next =
        action === "start"
          ? await startVaultMigration(run.id, run.plan_digest)
          : action === "resume"
            ? await resumeVaultMigration(run.id)
            : action === "pause"
              ? await pauseVaultMigration(run.id)
              : action === "retry"
                ? await retryVaultMigration(run.id)
                : action === "audit"
                  ? await auditVaultMigration(run.id)
                  : action === "retain"
                    ? await retainVaultMigration(run.id)
                    : action === "manual"
                      ? await retainVaultMigration(run.id, true)
                      : action === "cutover"
                        ? await cutoverVaultMigration(run.id)
                        : action === "recover"
                          ? await recoverVaultMigration(run.id)
                          : await cleanupVaultMigration(
                              run.id,
                              action === "source",
                              action === "source" && selectedBackup
                                ? {
                                    backup_id: selectedBackup.backup_id,
                                    backup_source_ref: selectedBackup.source_ref,
                                  }
                                : undefined,
                            );
      update(next);
      setConfirmation(null);
      if (next.state === "active") setBackupId("");
    } catch (cause) {
      if (mounted.current) setError(errorMessage(parseApiError(cause).code));
      setConfirmation(null);
      try {
        update(await getVaultMigration(run.id));
      } catch {
        /* A failed status read cannot prove completion. */
      }
    } finally {
      if (mounted.current) setBusy(false);
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
        disabled={running || backups.length === 0}
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
        {t(backups.length ? "migration.backupHelp" : "migration.backupEmpty")}
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
            disabled={loading || running}
            onClick={() => void refresh()}
            aria-label={t("migration.reload")}
          >
            <RefreshCw className="h-4 w-4" aria-hidden />
            {t("migration.reload")}
          </Button>
        </div>
        {loading ? (
          <p role="status" className="p-4 text-sm text-muted-foreground">
            {t("migration.loading")}
          </p>
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
                    setRun(runs.find((item) => item.id === event.target.value) ?? null);
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
            {!run ? (
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
                    setValues((current) => ({ ...current, [name]: value }))
                  }
                />
                <fieldset className="space-y-3 border-t pt-4">
                  <legend className="text-sm font-semibold">{t("migration.policy")}</legend>
                  <div className="grid gap-3 sm:grid-cols-3">
                    <label className="space-y-1 text-xs">
                      {t("migration.retentionDays")}
                      <Input
                        type="number"
                        min={0}
                        max={3650}
                        value={retentionDays}
                        disabled={busy}
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
                        disabled={busy}
                        onChange={(event) => setConcurrency(Number(event.target.value))}
                      />
                    </label>
                    <label className="space-y-1 text-xs">
                      {t("migration.bandwidth")}
                      <Input
                        type="number"
                        min={1024}
                        value={bandwidth}
                        disabled={busy}
                        onChange={(event) => setBandwidth(event.target.value)}
                      />
                    </label>
                  </div>
                  <p className="text-xs text-muted-foreground">{t("migration.policyHelp")}</p>
                </fieldset>
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
                                disabled={busy}
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
                              <Button disabled={busy} onClick={() => void act("resume")}>
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
                        <Button disabled={busy} onClick={() => void act("retry")}>
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
                            disabled={busy}
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
                            disabled={busy || ["pending", "running"].includes(fullAuditState ?? "")}
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
                                      disabled={!cleanupAllowed || !selectedBackup || busy}
                                      onClick={() => setConfirmation("source")}
                                    >
                                      {t("migration.cleanup")}
                                    </Button>
                                    <Button
                                      variant="outline"
                                      disabled={busy}
                                      onClick={() => void act("retain")}
                                    >
                                      {t("migration.retain")}
                                    </Button>
                                    <Button
                                      variant="ghost"
                                      disabled={busy}
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
                    <Button variant="outline" disabled={busy} onClick={() => void downloadReport()}>
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
                        setRun(null);
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
          open={confirmation !== null}
          onClose={() => {
            if (!busy) setConfirmation(null);
          }}
          busy={busy}
          onConfirm={() => {
            if (confirmation) void act(confirmation);
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
            confirmation === "start" && run
              ? t("migration.startDescription", {
                  source: run.source_provider_ref,
                  destination: run.destination_provider_ref,
                  backup: run.backup_summary.backup_id,
                  date: date(run.backup_summary.verified_at),
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
