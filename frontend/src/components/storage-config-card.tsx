"use client";

import { knownUiText, uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { providerFormError } from "@/lib/storage-provider-form";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { AlertTriangle, Save, ShieldAlert, ShieldCheck } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { vaultConfigOptions, useVaultConfigCommand } from "@/lib/queries/settings-config";
import { storageProvidersOptions, storageReadDenied } from "@/lib/queries/settings-storage";
import {
  useStorageRootEnrollment,
  type ReviewedStorageRoot,
} from "@/lib/queries/settings-storage-root";
import { captureEditingBase } from "@/lib/api/editing";
import type { EditingBase } from "@/types/editing";
import { getSessionVersion } from "@/lib/session-transport";
import { onAuthChange } from "@/lib/auth-store";
import { parseApiError } from "@/lib/errors";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { StorageProviderFields } from "@/components/storage-provider-fields";
import { providerFields } from "@/lib/storage-provider-form";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { ImportCopyWarning } from "@/components/import-copy-warning";
import type {
  StorageHealthRead,
  StorageRootRole,
  VaultConfigRead,
  VaultConfigUpdate,
} from "@/types";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n";
import { toast } from "@/lib/toast";
import { Localized } from "@/components/ui/localized";
import {
  defaultProviderValues,
  StorageProviderPicker,
  StorageProviderSummary,
  type ProviderValues,
} from "@/components/storage-provider-picker";

type SaveState = "idle" | "saving" | "saved" | "error";
interface StorageDraft {
  providerId: string;
  values: ProviderValues;
  base: EditingBase;
  snapshot: ProviderValues;
}
type StorageReview =
  | { phase: "idle" }
  | { phase: "required" | "loading"; problem: "conflict" | "unconfirmed" }
  | { phase: "ready"; problem: "conflict" | "unconfirmed"; snapshot: VaultConfigRead };

export function StorageConfigCard({
  storageHealth,
  migrationManaged = false,
}: {
  storageHealth?: StorageHealthRead | null;
  migrationManaged?: boolean;
}) {
  useUiLocale();
  const { user } = useAuth();
  const { t } = useI18n();
  const [session] = useState(getSessionVersion);
  const [retired, setRetired] = useState(false);
  const mounted = useRef(true);
  const enabled = !retired && !!user?.is_superuser;
  const current = () => mounted.current && session === getSessionVersion();
  const configuration = useQuery({ ...vaultConfigOptions(), enabled, retry: false });
  const catalogue = useQuery({ ...storageProvidersOptions(), enabled });
  const readError = configuration.error ?? catalogue.error;
  const denied = !enabled || storageReadDenied(parseApiError(readError));
  const cfg = denied ? undefined : configuration.data;
  const providers = denied || catalogue.isError ? [] : (catalogue.data ?? []);
  const loading = enabled && (configuration.isPending || catalogue.isPending);
  const [draft, setDraft] = useState<StorageDraft | null>(null);
  const [review, setReview] = useState<StorageReview>({ phase: "idle" });
  const draftRef = useRef(draft);
  useLayoutEffect(() => {
    draftRef.current = draft;
  }, [draft]);
  const providerId =
    draft?.providerId ??
    (cfg?.storage_provider || (cfg?.storage_backend === "s3" ? "s3" : "local"));
  const providerValues: ProviderValues = {};
  if (draft) Object.assign(providerValues, draft.snapshot, draft.values);
  else if (cfg && providerId === cfg.storage_provider)
    Object.assign(providerValues, cfg.storage_provider_config);
  else if (cfg?.storage_provider === "" && cfg.storage_backend === "s3") {
    // Older installations expose native S3 through these explicit wire fields.
    Object.assign(providerValues, {
      bucket: cfg.s3_bucket,
      endpoint_url: cfg.s3_endpoint_url,
      region: cfg.s3_region,
    });
  }
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [errorMsg, setErrorMsg] = useState("");
  const [enrollment, setEnrollment] = useState<ReviewedStorageRoot | null>(null);
  if (denied && enrollment) setEnrollment(null);
  const enrollRole = enrollment?.role ?? null;
  const enrollmentCommand = useStorageRootEnrollment();
  const enrolling = enrollmentCommand.pending;
  const configCommand = useVaultConfigCommand();
  const canEdit = enabled && !!cfg && !readError;
  useEffect(() => {
    mounted.current = true;
    const release = onAuthChange(() => {
      setRetired(true);
      setDraft(null);
      setReview({ phase: "idle" });
      setEnrollment(null);
      setSaveState("idle");
      setErrorMsg("");
    });
    return () => {
      mounted.current = false;
      release();
    };
  }, []);
  function editField(name: string, value: ProviderValues[string]) {
    if (!cfg) return;
    setDraft((previous) => ({
      providerId,
      base: previous?.base ?? captureEditingBase(cfg),
      snapshot: previous?.snapshot ?? { ...providerValues },
      values: { ...previous?.values, [name]: value },
    }));
    setSaveState("idle");
    setErrorMsg("");
  }
  async function save(reviewed?: VaultConfigRead) {
    if (
      !canEdit ||
      !cfg ||
      !current() ||
      configCommand.isPending ||
      (review.phase !== "idle" && !reviewed)
    )
      return;
    setSaveState("saving");
    setErrorMsg("");
    const submitted = draft;
    // A reviewed retry keeps explicit edits while adopting untouched current fields.
    const writeValues: ProviderValues =
      reviewed && providerId === reviewed.storage_provider
        ? { ...reviewed.storage_provider_config, ...draft?.values }
        : providerValues;
    try {
      const selected = providers.find((provider) => provider.id === providerId);
      if (!selected || !selected.available || !selected.selectable) {
        setSaveState("error");
        setErrorMsg(t("settings.storageReadFailed"));
        return;
      }
      const stored = Array.isArray(writeValues.secret_fields_set)
        ? writeValues.secret_fields_set
        : [];
      const values = Object.fromEntries(
        Object.entries(writeValues).filter(([, value]) => value !== ""),
      );
      const invalid = providerFormError(selected, values, "vault", stored);
      if (invalid) {
        setSaveState("error");
        setErrorMsg(invalid);
        return;
      }
      const body: VaultConfigUpdate = {
        storage_provider: providerId,
        storage_provider_config: {
          provider: providerId,
          ...Object.fromEntries(
            Object.entries(writeValues).filter(
              ([name, value]) => name !== "secret_fields_set" && value !== "",
            ),
          ),
        },
      };
      const receipt = await configCommand.mutateAsync({
        session,
        payload: body,
        base: reviewed ? captureEditingBase(reviewed) : (draft?.base ?? captureEditingBase(cfg)),
      });
      if (!current()) return;
      setSaveState(draftRef.current === submitted ? "saved" : "idle");
      setReview({ phase: "idle" });
      setDraft((latest) => {
        if (latest === submitted) return null;
        if (!latest) return latest;
        return {
          ...latest,
          base: captureEditingBase(receipt),
          snapshot:
            latest.providerId === receipt.storage_provider
              ? { ...receipt.storage_provider_config }
              : latest.snapshot,
          values: Object.fromEntries(
            Object.entries(latest.values).filter(
              ([name, value]) =>
                latest.providerId !== providerId || value !== submitted?.values[name],
            ),
          ),
        };
      });
    } catch (error) {
      if (current()) {
        setSaveState("error");
        const status = parseApiError(error).status;
        if (status === 412 || status === 428 || status === 0 || status >= 500)
          setReview({
            phase: "required",
            problem: status === 412 || status === 428 ? "conflict" : "unconfirmed",
          });
        setErrorMsg(parseApiError(error).message);
      }
    }
  }

  async function reviewStorage() {
    if (
      !current() ||
      !enabled ||
      configCommand.isPending ||
      review.phase === "idle" ||
      review.phase === "loading"
    )
      return;
    const problem = review.problem;
    setReview({ phase: "loading", problem });
    setErrorMsg("");
    try {
      const result = await configuration.refetch({ throwOnError: true });
      if (!result.data) throw new Error("Configuration review has no data");
      if (current()) setReview({ phase: "ready", problem, snapshot: result.data });
    } catch (error) {
      if (current()) {
        setReview({ phase: "required", problem });
        setErrorMsg(parseApiError(error).message);
      }
    }
  }
  if (loading) {
    return (
      <Localized>
        <div className="overflow-hidden rounded-lg border border-border bg-card shadow-sm">
          <div className="px-4 sm:px-6 lg:px-8 py-4 sm:py-5 border-b border-border">
            <h3 className="text-sm font-semibold text-foreground">
              {t("settings.currentStorage")}
            </h3>
          </div>
          <div
            role="status"
            aria-label={t("settings.currentStorage")}
            className="space-y-4 p-4 sm:p-5 lg:p-6"
          >
            <Skeleton className="h-5 w-48" />
            <Skeleton className="h-4 w-3/4" />
            <div className="grid gap-3 sm:grid-cols-2">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
            <span className="sr-only">{uiText("Loading...")}</span>
          </div>
        </div>
      </Localized>
    );
  }

  const rootBindings = storageHealth?.diagnostics?.root_bindings ?? {};
  const rootCandidates: Array<[StorageRootRole, string | undefined]> = [
    ["data", cfg?.data_dir],
    ["thumb", cfg?.thumb_dir],
  ];
  const enrollableRoots = rootCandidates.filter(
    ([role, path]) => path && rootBindings[role] === "binding_missing",
  );
  const invalidRoots = rootCandidates.filter(
    ([role, path]) =>
      path && ["binding_mismatch", "binding_invalid", "missing"].includes(rootBindings[role] ?? ""),
  );

  const currentProvider = providers.find((provider) => provider.id === providerId);
  const locationFields = currentProvider
    ? providerFields(currentProvider).flatMap((field) => {
        if (field.secret || (currentProvider.category === "this_machine" && field.name === "root"))
          return [];
        const value =
          providerValues[field.name] ||
          (currentProvider.category === "this_machine" && field.name === "data_dir"
            ? cfg?.data_dir
            : currentProvider.category === "this_machine" && field.name === "thumb_dir"
              ? cfg?.thumb_dir
              : undefined);
        return value ? [{ name: field.name, label: field.label, value: String(value) }] : [];
      })
    : [];
  const credentialFields = currentProvider
    ? providerFields(currentProvider).filter((field) => field.secret)
    : [];

  async function confirmEnrollRoot() {
    if (!enrollment || !current() || !canEdit || enrolling) return;
    try {
      await enrollmentCommand.enroll(enrollment);
      if (current()) {
        toast.success(t("settings.storageEnrollSuccess"));
        setEnrollment(null);
      }
    } catch (error) {
      if (current()) {
        if (parseApiError(error).code === "storage_review_changed") {
          setSaveState("error");
          setErrorMsg(t("settings.storageReviewChanged"));
          setEnrollment(null);
        } else toast.error(error);
      }
    }
  }

  return (
    <Localized>
      <div className="overflow-hidden rounded-lg border border-border bg-card shadow-sm">
        <div className="border-b border-border px-4 py-4 sm:px-5">
          <h3 className="text-sm font-semibold text-foreground">{t("settings.currentStorage")}</h3>
        </div>

        <div className="space-y-5 p-4 sm:p-5 lg:p-6">
          {readError && enabled && (
            <div role="alert" className="space-y-2 text-sm text-destructive">
              <p>{t("settings.storageReadFailed")}</p>
              <Button
                variant="outline"
                onClick={() => {
                  void configuration.refetch();
                  void catalogue.refetch();
                }}
              >
                {t("settings.storageRetry")}
              </Button>
            </div>
          )}
          {!denied && review.phase !== "idle" && (
            <div role="alert" className="space-y-3 text-sm">
              <p>
                {uiText(
                  review.problem === "conflict"
                    ? "library.editConflict"
                    : "library.saveUnconfirmed",
                )}
              </p>
              <Button
                variant="outline"
                disabled={configCommand.isPending || review.phase === "loading"}
                onClick={() => void reviewStorage()}
              >
                {uiText("library.reviewLatest")}
              </Button>
              {review.phase === "ready" && (
                <section aria-label={uiText("library.latestVersion")} className="space-y-3">
                  <h3 className="font-semibold">{uiText("library.latestVersion")}</h3>
                  <p>
                    {knownUiText(
                      providers.find((provider) => provider.id === review.snapshot.storage_provider)
                        ?.label ?? review.snapshot.storage_provider,
                    )}
                  </p>
                  <dl className="grid gap-2">
                    {providers
                      .filter((provider) => provider.id === review.snapshot.storage_provider)
                      .flatMap((provider) => providerFields(provider))
                      .map((field) => {
                        const stored = review.snapshot.storage_provider_config.secret_fields_set;
                        const value = field.secret
                          ? Array.isArray(stored) && stored.includes(field.name)
                            ? uiText("Configured — enter to replace")
                            : uiText("None")
                          : String(review.snapshot.storage_provider_config[field.name] ?? "");
                        return (
                          <div key={field.name}>
                            <dt className="text-muted-foreground">{knownUiText(field.label)}</dt>
                            <dd>{value}</dd>
                          </div>
                        );
                      })}
                  </dl>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="outline"
                      disabled={!canEdit || configCommand.isPending}
                      onClick={() => {
                        setDraft(null);
                        setReview({ phase: "idle" });
                        setErrorMsg("");
                        setSaveState("idle");
                      }}
                    >
                      {uiText("library.useLatest")}
                    </Button>
                    <Button
                      disabled={!canEdit || configCommand.isPending}
                      onClick={() => void save(review.snapshot)}
                    >
                      {uiText("library.retryDraft")}
                    </Button>
                  </div>
                </section>
              )}
            </div>
          )}
          {errorMsg && (
            <p role="alert" className="text-sm text-destructive">
              {errorMsg}
            </p>
          )}
          {draft &&
            cfg &&
            (draft.base.edit_epoch !== cfg.edit_epoch ||
              draft.base.edit_version !== cfg.edit_version) && (
              <div className="space-y-2 text-sm">
                <p>{t("settings.storageReviewChanged")}</p>
                <Button
                  variant="outline"
                  onClick={() => {
                    setDraft(null);
                    setSaveState("idle");
                    setErrorMsg("");
                  }}
                >
                  {t("settings.storageDiscardDraft")}
                </Button>
              </div>
            )}

          {storageHealth && !storageHealth.ok && (
            <div
              role="alert"
              className="flex items-start gap-3 rounded-lg border border-warning/30 bg-warning/10 p-3"
            >
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" aria-hidden />
              <div>
                <p className="text-sm font-semibold text-foreground">
                  {t("settings.storageWarningTitle")}
                </p>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                  {t("settings.storageUnavailableDescription")}
                </p>
              </div>
            </div>
          )}
          <ImportCopyWarning storageHealth={storageHealth} />
          {invalidRoots.length > 0 && (
            <div
              role="alert"
              className="space-y-2 rounded-lg border border-destructive/30 bg-destructive/10 p-3"
            >
              <p className="text-sm font-semibold text-foreground">
                {t("settings.storageRootIdentityTitle")}
              </p>
              <p className="text-xs leading-relaxed text-muted-foreground">
                {t("settings.storageRootIdentityDescription")}
              </p>
              {invalidRoots.map(([role, path]) => (
                <p key={role} className="font-mono text-xs text-foreground">
                  {t("settings.storageRootLocation", {
                    role: role === "data" ? "Data" : "Thumbnail",
                    path: path ?? "",
                    status: rootBindings[role] ?? "invalid",
                  })}
                </p>
              ))}
            </div>
          )}
          {enrollableRoots.length > 0 && (
            <div className="space-y-3 rounded-lg border border-warning/30 bg-warning/10 p-3">
              <div>
                <p className="text-sm font-semibold text-foreground">
                  {t("settings.storageEnrollTitle")}
                </p>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                  {t("settings.storageEnrollDescription")}
                </p>
              </div>
              <div className="space-y-2">
                {enrollableRoots.map(([role, path]) => (
                  <div
                    key={role}
                    className="flex flex-wrap items-center justify-between gap-2 rounded border border-border bg-background/50 px-3 py-2"
                  >
                    <span className="font-mono text-xs text-foreground">
                      {t("settings.storageRootPath", {
                        role: role === "data" ? "Data" : "Thumbnail",
                        path: path ?? "",
                      })}
                    </span>
                    <button
                      type="button"
                      className="rounded border border-warning/40 px-3 py-1.5 text-xs font-medium text-warning hover:bg-warning/10"
                      onClick={() => {
                        if (path) setEnrollment({ role, path });
                      }}
                      disabled={!canEdit || enrolling}
                    >
                      {t("settings.storageEnrollAction")}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
          {migrationManaged ? (
            <div className="space-y-4">
              {currentProvider && (
                <>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="text-lg font-semibold text-foreground">
                      {knownUiText(currentProvider.label)}
                    </p>
                    {cfg?.storage_tier && (
                      <p className="flex items-center gap-1.5 text-sm font-medium text-foreground">
                        {cfg.storage_tier === "verified" ? (
                          <ShieldCheck className="h-4 w-4 text-success" aria-hidden />
                        ) : (
                          <ShieldAlert className="h-4 w-4 text-warning" aria-hidden />
                        )}
                        {t("settings.storageSafety", {
                          tier: knownUiText(cfg.storage_tier),
                        })}
                      </p>
                    )}
                  </div>
                  {(locationFields.length > 0 || currentProvider.expected_tier === "guarded") && (
                    <section
                      aria-label={t("settings.storageConnectionDetails")}
                      className="border-t border-border pt-3"
                    >
                      {locationFields.length > 0 && (
                        <dl className="flex flex-wrap gap-x-8 gap-y-2 text-xs">
                          {locationFields.map((field) => (
                            <div key={field.name} className="min-w-0">
                              <dt className="text-muted-foreground">{field.label}</dt>
                              <dd className="break-all font-medium text-foreground">
                                {field.value}
                              </dd>
                            </div>
                          ))}
                        </dl>
                      )}
                      <StorageProviderSummary
                        provider={currentProvider}
                        activeTier={cfg?.storage_tier}
                        context="current"
                      />
                    </section>
                  )}
                </>
              )}
              {user?.is_superuser && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    document.getElementById("vault-migration")?.scrollIntoView({ block: "start" })
                  }
                >
                  {t("migration.changeEntry")}
                </Button>
              )}
              {currentProvider && credentialFields.length > 0 && (
                <StorageProviderFields
                  provider={{
                    ...currentProvider,
                    fields: credentialFields,
                    fields_by_use: { vault: credentialFields },
                  }}
                  values={providerValues}
                  disabled={!canEdit}
                  storedSecrets={
                    Array.isArray(providerValues.secret_fields_set)
                      ? providerValues.secret_fields_set
                      : []
                  }
                  onChange={editField}
                />
              )}
            </div>
          ) : (
            <>
              <StorageProviderPicker
                providers={providers}
                providerId={providerId}
                values={providerValues}
                activeTier={cfg?.storage_tier}
                disabled={!canEdit}
                onProviderChange={(provider) => {
                  if (cfg)
                    setDraft((previous) => ({
                      providerId: provider.id,
                      values: defaultProviderValues(provider),
                      snapshot: {},
                      base: previous?.base ?? captureEditingBase(cfg),
                    }));
                  setSaveState("idle");
                  setErrorMsg("");
                }}
                onValueChange={editField}
              />
              <p className="text-3xs text-muted-foreground">
                {uiText(
                  "Provider changes require an application restart. Storage risk acknowledgement remains environment-only.",
                )}
              </p>
            </>
          )}

          {/* Save row */}
          {canEdit && (!migrationManaged || credentialFields.length > 0) && (
            <div className="flex items-center gap-3 pt-2">
              <button
                type="button"
                onClick={() => void save()}
                disabled={configCommand.isPending || review.phase !== "idle"}
                className="flex items-center gap-1.5 px-4 py-2 rounded bg-primary text-primary-foreground font-mono text-xs uppercase tracking-wider hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity"
              >
                <Save className="h-3.5 w-3.5" />
                {saveState === "saving" ? uiText("Saving...") : uiText("Save configuration")}
              </button>

              {saveState === "saved" && (
                <span className="text-xs text-green-600 dark:text-green-400">
                  {uiText("Saved")}
                </span>
              )}
            </div>
          )}

          {!canEdit && (
            <p className="text-xs text-muted-foreground italic">
              {uiText("Sign in to modify configuration.")}
            </p>
          )}
        </div>
        <ConfirmModal
          open={enrollment !== null && !denied}
          onClose={() => {
            if (!enrolling) setEnrollment(null);
          }}
          onConfirm={() => void confirmEnrollRoot()}
          busy={enrolling}
          title={t("settings.storageEnrollConfirmTitle", {
            role: enrollRole === "data" ? "data" : "thumbnail",
          })}
          description={t("settings.storageEnrollConfirmDescription", {
            role: enrollRole === "data" ? "data" : "thumbnail",
            path: enrollment?.path ?? "",
          })}
          confirmLabel={t("settings.storageEnrollConfirmAction")}
        />
      </div>
    </Localized>
  );
}
