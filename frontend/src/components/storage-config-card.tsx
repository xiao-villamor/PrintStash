"use client";

import { providerFormError } from "@/lib/storage-provider-form";
import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Save } from "lucide-react";
import {
  enrollStorageRoot,
  getStorageProviders,
  getVaultConfig,
  updateVaultConfig,
} from "@/lib/api";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import type {
  StorageHealthRead,
  StorageRootRole,
  StorageProvider,
  VaultConfigRead,
  VaultConfigUpdate,
} from "@/types";
import { useRequireAuth } from "@/lib/use-require-auth";
import { useI18n } from "@/lib/i18n";
import { toast } from "@/lib/toast";
import { Localized } from "@/components/ui/localized";
import {
  defaultProviderValues,
  StorageProviderPicker,
  type ProviderValues,
} from "@/components/storage-provider-picker";

type SaveState = "idle" | "saving" | "saved" | "error";

export function StorageConfigCard({ storageHealth }: { storageHealth?: StorageHealthRead | null }) {
  const { isAuthenticated } = useRequireAuth();
  const { t } = useI18n();
  const [cfg, setCfg] = useState<VaultConfigRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [providers, setProviders] = useState<StorageProvider[]>([]);
  const [providerId, setProviderId] = useState("local");
  const [providerValues, setProviderValues] = useState<ProviderValues>({});
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [errorMsg, setErrorMsg] = useState("");
  const [enrollRole, setEnrollRole] = useState<StorageRootRole | null>(null);
  const [enrolling, setEnrolling] = useState(false);

  const load = useCallback(async () => {
    try {
      const [c, providerCatalogue] = await Promise.all([getVaultConfig(), getStorageProviders()]);
      setCfg(c);
      setProviders(providerCatalogue);
      setProviderId(c.storage_provider || (c.storage_backend === "s3" ? "s3" : "local"));
      setProviderValues(c.storage_provider_config ?? {});
    } catch {
      // ignore — show empty form
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect -- load() is async: every setState runs after `await getVaultConfig()`, i.e. from the fetch continuation, so nothing is set synchronously during this effect. The rule inlines the useCallback body and cannot see the await boundary.
    void load();
  }, [load]);

  const save = useCallback(async () => {
    setSaveState("saving");
    setErrorMsg("");
    try {
      const selected = providers.find((provider) => provider.id === providerId);
      if (selected) {
        const stored = Array.isArray(providerValues.secret_fields_set)
          ? providerValues.secret_fields_set
          : [];
        const submitted = Object.fromEntries(
          Object.entries(providerValues).filter(([, value]) => value !== ""),
        );
        const invalid = providerFormError(selected, submitted, "vault", stored);
        if (invalid) throw new Error(invalid);
      }
      const body: VaultConfigUpdate = {
        storage_provider: providerId,
        storage_provider_config: {
          provider: providerId,
          ...Object.fromEntries(
            Object.entries(providerValues).filter(
              ([name, value]) => name !== "secret_fields_set" && value !== "",
            ),
          ),
        },
      };

      await updateVaultConfig(body);
      setSaveState("saved");
      await load();

      setTimeout(() => setSaveState("idle"), 2500);
    } catch (e: any) {
      setSaveState("error");
      setErrorMsg(e?.message || "Save failed");
    }
  }, [providerId, providerValues, load, providers]);

  if (loading) {
    return (
      <Localized>
        <div className="overflow-hidden rounded-lg border border-border bg-card shadow-sm">
          <div className="px-4 sm:px-6 lg:px-8 py-4 sm:py-5 border-b border-border">
            <h3 className="text-sm font-semibold text-foreground">Storage configuration</h3>
          </div>
          <div className="p-3 sm:p-4 lg:p-6 text-sm text-muted-foreground">Loading...</div>
        </div>
      </Localized>
    );
  }

  const canEdit = isAuthenticated;
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

  async function confirmEnrollRoot() {
    if (!enrollRole) return;
    setEnrolling(true);
    try {
      await enrollStorageRoot(enrollRole);
      toast.success(t("settings.storageEnrollSuccess"));
      setEnrollRole(null);
    } catch (error) {
      toast.error(error);
    } finally {
      setEnrolling(false);
    }
  }

  return (
    <Localized>
      <div className="overflow-hidden rounded-lg border border-border bg-card shadow-sm">
        <div className="px-4 sm:px-6 lg:px-8 py-4 sm:py-5 border-b border-border flex items-center justify-between gap-2">
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-foreground">Storage configuration</h3>
            <p className="text-xs text-muted-foreground mt-0.5">
              {t("settings.storageConfigDescription")}
            </p>
          </div>
          {cfg && (
            <span className="font-mono text-3xs uppercase tracking-wider px-2 py-1 rounded border text-muted-foreground border-border flex-shrink-0">
              {cfg.storage_provider}
            </span>
          )}
        </div>

        <div className="space-y-5 p-4 sm:p-5 lg:p-6">
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
                      onClick={() => setEnrollRole(role)}
                      disabled={!canEdit || enrolling}
                    >
                      {t("settings.storageEnrollAction")}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
          <StorageProviderPicker
            providers={providers}
            providerId={providerId}
            values={providerValues}
            activeTier={cfg?.storage_tier}
            disabled={!canEdit}
            onProviderChange={(provider) => {
              setProviderId(provider.id);
              setProviderValues(defaultProviderValues(provider));
            }}
            onValueChange={(name, value) =>
              setProviderValues((current) => ({ ...current, [name]: value }))
            }
          />
          <p className="text-3xs text-muted-foreground">
            Provider changes require an application restart. Storage risk acknowledgement remains
            environment-only.
          </p>

          {/* Save row */}
          {canEdit && (
            <div className="flex items-center gap-3 pt-2">
              <button
                type="button"
                onClick={save}
                disabled={saveState === "saving"}
                className="flex items-center gap-1.5 px-4 py-2 rounded bg-primary text-primary-foreground font-mono text-xs uppercase tracking-wider hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity"
              >
                <Save className="h-3.5 w-3.5" />
                {saveState === "saving" ? "Saving..." : "Save configuration"}
              </button>

              {saveState === "saved" && (
                <span className="text-xs text-green-600 dark:text-green-400">Saved</span>
              )}

              {saveState === "error" && (
                <span className="text-xs text-red-600 dark:text-red-400">
                  {errorMsg || "Error saving"}
                </span>
              )}
            </div>
          )}

          {!canEdit && (
            <p className="text-xs text-muted-foreground italic">Sign in to modify configuration.</p>
          )}
        </div>
        <ConfirmModal
          open={enrollRole !== null}
          onClose={() => {
            if (!enrolling) setEnrollRole(null);
          }}
          onConfirm={() => void confirmEnrollRoot()}
          busy={enrolling}
          title={t("settings.storageEnrollConfirmTitle", {
            role: enrollRole === "data" ? "data" : "thumbnail",
          })}
          description={t("settings.storageEnrollConfirmDescription", {
            role: enrollRole === "data" ? "data" : "thumbnail",
            path: enrollableRoots.find(([role]) => role === enrollRole)?.[1] ?? "configured path",
          })}
          confirmLabel={t("settings.storageEnrollConfirmAction")}
        />
      </div>
    </Localized>
  );
}
