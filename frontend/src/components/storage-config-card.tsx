"use client";

import { useCallback, useEffect, useState } from "react";
import { Cloud, Key, RefreshCw, Save } from "lucide-react";
import { getStorageProviders, getVaultConfig, updateVaultConfig } from "@/lib/api";
import type { StorageProvider, VaultConfigRead, VaultConfigUpdate } from "@/types";
import { useRequireAuth } from "@/lib/use-require-auth";
import { Localized } from "@/components/ui/localized";
import {
  defaultProviderValues,
  StorageProviderPicker,
  type ProviderValues,
} from "@/components/storage-provider-picker";

type SaveState = "idle" | "saving" | "saved" | "error";

export function StorageConfigCard() {
  const { isAuthenticated } = useRequireAuth();
  const [cfg, setCfg] = useState<VaultConfigRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [providers, setProviders] = useState<StorageProvider[]>([]);
  const [providerId, setProviderId] = useState("local");
  const [providerValues, setProviderValues] = useState<ProviderValues>({});
  const [backupDays, setBackupDays] = useState(30);
  const [bkS3Bucket, setBkS3Bucket] = useState("");
  const [bkS3Endpoint, setBkS3Endpoint] = useState("");
  const [bkS3Region, setBkS3Region] = useState("auto");
  const [bkS3AccessKey, setBkS3AccessKey] = useState("");
  const [bkS3SecretKey, setBkS3SecretKey] = useState("");
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [errorMsg, setErrorMsg] = useState("");

  const load = useCallback(async () => {
    try {
      const [c, providerCatalogue] = await Promise.all([getVaultConfig(), getStorageProviders()]);
      setCfg(c);
      setProviders(providerCatalogue);
      setProviderId(c.storage_provider || (c.storage_backend === "s3" ? "s3" : "local"));
      setProviderValues(c.storage_provider_config ?? {});
      setBackupDays(c.backup_retention_days ?? 30);
      setBkS3Bucket(c.backup_s3_bucket);
      setBkS3Endpoint(c.backup_s3_endpoint_url);
      setBkS3Region(c.backup_s3_region || "auto");
      setBkS3AccessKey(c.backup_s3_access_key);
      setBkS3SecretKey(c.backup_s3_secret_key);
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
        backup_retention_days: backupDays,
        backup_s3_bucket: bkS3Bucket || "",
        backup_s3_endpoint_url: bkS3Endpoint || "",
        backup_s3_region: bkS3Region || "",
      };

      if (bkS3AccessKey && !bkS3AccessKey.includes("*")) {
        body.backup_s3_access_key = bkS3AccessKey;
      }
      if (bkS3SecretKey && !bkS3SecretKey.includes("*")) {
        body.backup_s3_secret_key = bkS3SecretKey;
      }

      await updateVaultConfig(body);
      setSaveState("saved");
      await load();

      setTimeout(() => setSaveState("idle"), 2500);
    } catch (e: any) {
      setSaveState("error");
      setErrorMsg(e?.message || "Save failed");
    }
  }, [
    providerId,
    providerValues,
    backupDays,
    bkS3Bucket,
    bkS3Endpoint,
    bkS3Region,
    bkS3AccessKey,
    bkS3SecretKey,
    load,
  ]);

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

  return (
    <Localized>
      <div className="overflow-hidden rounded-lg border border-border bg-card shadow-sm">
        <div className="px-4 sm:px-6 lg:px-8 py-4 sm:py-5 border-b border-border flex items-center justify-between gap-2">
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-foreground">Storage configuration</h3>
            <p className="text-xs text-muted-foreground mt-0.5">
              Provider connection, active guarantees, and backup retention
            </p>
          </div>
          {cfg && (
            <span className="font-mono text-3xs uppercase tracking-wider px-2 py-1 rounded border text-muted-foreground border-border flex-shrink-0">
              {cfg.storage_provider}
            </span>
          )}
        </div>

        <div className="space-y-5 p-4 sm:p-5 lg:p-6">
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

          {/* Backup settings */}
          <div className="space-y-3 rounded-lg bg-muted/40 p-3 sm:p-4">
            <p className="text-xs font-medium text-foreground flex items-center gap-1.5">
              <RefreshCw className="h-3 w-3" /> Backup
            </p>
            <div className="max-w-xs">
              <label className="block text-2xs text-muted-foreground mb-1">Retention (days)</label>
              <input
                type="number"
                disabled={!canEdit}
                min={0}
                max={365}
                value={backupDays}
                onChange={(e) => setBackupDays(Number(e.target.value))}
                className="w-32 px-2.5 py-1.5 text-sm rounded border border-border bg-background text-foreground disabled:opacity-50 font-mono"
              />
              <p className="text-3xs text-muted-foreground mt-0.5">
                Set to 0 to keep backups forever. Old backups are purged after each new backup.
              </p>
            </div>

            <div className="border-t border-border pt-3">
              <p className="text-xs font-medium text-foreground flex items-center gap-1.5 mb-2">
                <Cloud className="h-3 w-3" /> Backup destination (optional)
              </p>
              <p className="text-3xs text-muted-foreground mb-3">
                Backups are always stored locally first. If configured here, they are also uploaded
                to cloud storage for off-site durability.
              </p>

              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="block text-2xs text-muted-foreground mb-1">Bucket</label>
                  <input
                    type="text"
                    disabled={!canEdit}
                    value={bkS3Bucket}
                    onChange={(e) => setBkS3Bucket(e.target.value)}
                    placeholder="my-backup-bucket"
                    className="w-full px-2.5 py-1.5 text-sm rounded border border-border bg-background text-foreground placeholder:text-muted-foreground/40 disabled:opacity-50 font-mono"
                  />
                </div>
                <div>
                  <label className="block text-2xs text-muted-foreground mb-1">Endpoint URL</label>
                  <input
                    type="text"
                    disabled={!canEdit}
                    value={bkS3Endpoint}
                    onChange={(e) => setBkS3Endpoint(e.target.value)}
                    placeholder="https://&lt;id&gt;.r2.cloudflarestorage.com"
                    className="w-full px-2.5 py-1.5 text-sm rounded border border-border bg-background text-foreground placeholder:text-muted-foreground/40 disabled:opacity-50 font-mono"
                  />
                  <p className="text-3xs text-muted-foreground mt-0.5">Leave empty for AWS S3.</p>
                </div>
                <div>
                  <label className="block text-2xs text-muted-foreground mb-1">Region</label>
                  <input
                    type="text"
                    disabled={!canEdit}
                    value={bkS3Region}
                    onChange={(e) => setBkS3Region(e.target.value)}
                    placeholder="auto"
                    className="w-full px-2.5 py-1.5 text-sm rounded border border-border bg-background text-foreground placeholder:text-muted-foreground/40 disabled:opacity-50 font-mono"
                  />
                </div>

                <div className="border-t border-border pt-2 mt-2 sm:col-span-2">
                  <p className="text-xs font-medium text-foreground flex items-center gap-1.5 mb-2">
                    <Key className="h-3 w-3" /> Credentials
                  </p>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div>
                      <label className="block text-2xs text-muted-foreground mb-1">
                        Access key
                      </label>
                      <input
                        type="text"
                        disabled={!canEdit}
                        value={bkS3AccessKey}
                        onChange={(e) => setBkS3AccessKey(e.target.value)}
                        placeholder={
                          cfg?.has_backup_s3_access_key ? "(stored)" : "backup-access-key"
                        }
                        className="w-full px-2.5 py-1.5 text-sm rounded border border-border bg-background text-foreground placeholder:text-muted-foreground/40 disabled:opacity-50 font-mono"
                      />
                    </div>
                    <div>
                      <label className="block text-2xs text-muted-foreground mb-1">
                        Secret key
                      </label>
                      <input
                        type="password"
                        disabled={!canEdit}
                        value={bkS3SecretKey}
                        onChange={(e) => setBkS3SecretKey(e.target.value)}
                        placeholder={
                          cfg?.has_backup_s3_secret_key ? "(stored)" : "backup-secret-key"
                        }
                        className="w-full px-2.5 py-1.5 text-sm rounded border border-border bg-background text-foreground placeholder:text-muted-foreground/40 disabled:opacity-50 font-mono"
                      />
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

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
      </div>
    </Localized>
  );
}
