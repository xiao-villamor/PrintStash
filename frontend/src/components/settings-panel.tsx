"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  Database,
  Download,
  Eraser,
  HardDrive,
  KeyRound,
  Copy,
  Palette,
  RefreshCw,
  RotateCcw,
  Trash2,
  Server,
  Tag,
} from "lucide-react";
import { StorageConfigCard } from "@/components/storage-config-card";
import {
  createApiKey,
  downloadModelExport,
  getVaultConfig,
  getVaultStats,
  listApiKeys,
  listTrash,
  purgeExpiredTrash,
  purgeModel,
  restoreModel,
  revokeApiKey,
  updateVaultConfig,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import {
  DEFAULT_METADATA_PREFERENCES,
  METADATA_FIELDS,
  MetadataPreferences,
  readMetadataPreferences,
  writeMetadataPreferences,
} from "@/lib/metadata-preferences";
import { toast } from "@/lib/toast";
import type { ApiKeyRead, TrashedModelRead, VaultStatsRead } from "@/types";

interface HealthResponse {
  status: string;
  name: string;
  version: string;
}

type SettingsSection = "overview" | "access" | "storage" | "design" | "trash";

const SETTINGS_SECTIONS: {
  id: SettingsSection;
  label: string;
  icon: typeof Server;
}[] = [
  { id: "overview", label: "Overview", icon: Server },
  { id: "access", label: "Access", icon: KeyRound },
  { id: "storage", label: "Storage", icon: HardDrive },
  { id: "design", label: "Design", icon: Palette },
  { id: "trash", label: "Trash", icon: Trash2 },
];

function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null) return "...";
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** exponent;
  return `${value >= 10 || exponent === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[exponent]}`;
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "Never";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(value));
}

export function SettingsPanel() {
  const { user } = useAuth();
  const [activeSection, setActiveSection] = useState<SettingsSection>("overview");
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [stats, setStats] = useState<VaultStatsRead | null>(null);
  const [exporting, setExporting] = useState<"json" | "csv" | null>(null);
  const [apiKeys, setApiKeys] = useState<ApiKeyRead[]>([]);
  const [newApiKey, setNewApiKey] = useState<string | null>(null);
  const [keyName, setKeyName] = useState("Programmatic access");
  const [keyBusy, setKeyBusy] = useState(false);
  const [trashItems, setTrashItems] = useState<TrashedModelRead[]>([]);
  const [trashLoading, setTrashLoading] = useState(false);
  const [trashBusy, setTrashBusy] = useState<number | "expired" | "settings" | null>(null);
  const [trashRetentionDays, setTrashRetentionDays] = useState(30);
  const [metadataPrefs, setMetadataPrefs] = useState<MetadataPreferences>(
    DEFAULT_METADATA_PREFERENCES,
  );

  useEffect(() => {
    fetch("/api/v1/health")
      .then((r) => r.json())
      .then(setHealth)
      .catch(() => {});
    getVaultStats()
      .then(setStats)
      .catch(() => {});
    setMetadataPrefs(readMetadataPreferences());
  }, []);

  useEffect(() => {
    if (!user) {
      setApiKeys([]);
      setNewApiKey(null);
      return;
    }
    listApiKeys()
      .then(setApiKeys)
      .catch(() => {});
  }, [user]);

  const loadTrash = useCallback(async () => {
    if (!user) {
      setTrashItems([]);
      return;
    }
    setTrashLoading(true);
    try {
      const [items, cfg] = await Promise.all([listTrash(), getVaultConfig()]);
      setTrashItems(items);
      setTrashRetentionDays(cfg.trash_retention_days ?? 30);
    } catch (e) {
      toast.error(e);
    } finally {
      setTrashLoading(false);
    }
  }, [user]);

  useEffect(() => {
    if (activeSection === "trash") {
      loadTrash();
    }
  }, [activeSection, loadTrash]);

  async function exportData(format: "json" | "csv") {
    setExporting(format);
    try {
      await downloadModelExport(format);
    } catch (e) {
      toast.error(e);
    } finally {
      setExporting(null);
    }
  }

  async function generateApiKey() {
    setKeyBusy(true);
    try {
      const created = await createApiKey(keyName.trim() || "Programmatic access");
      setNewApiKey(created.api_key);
      setApiKeys((current) => [created, ...current]);
      toast.success("API key created. Copy it now; it will not be shown again.");
    } catch (e) {
      toast.error(e);
    } finally {
      setKeyBusy(false);
    }
  }

  async function deleteApiKey(id: number) {
    setKeyBusy(true);
    try {
      await revokeApiKey(id);
      setApiKeys((current) => current.filter((key) => key.id !== id));
      toast.success("API key revoked.");
    } catch (e) {
      toast.error(e);
    } finally {
      setKeyBusy(false);
    }
  }

  async function copyApiKey() {
    if (!newApiKey) return;
    await navigator.clipboard.writeText(newApiKey);
    toast.success("API key copied.");
  }

  function updateMetadataPreference(
    field: keyof MetadataPreferences,
    visible: boolean,
  ) {
    const next = { ...metadataPrefs, [field]: visible };
    setMetadataPrefs(next);
    writeMetadataPreferences(next);
  }

  function resetMetadataPreferences() {
    setMetadataPrefs(DEFAULT_METADATA_PREFERENCES);
    writeMetadataPreferences(DEFAULT_METADATA_PREFERENCES);
    toast.success("Metadata display reset.");
  }

  async function saveTrashRetention() {
    setTrashBusy("settings");
    try {
      await updateVaultConfig({ trash_retention_days: Math.max(-1, trashRetentionDays) });
      toast.success("Trash retention updated.");
      await loadTrash();
    } catch (e) {
      toast.error(e);
    } finally {
      setTrashBusy(null);
    }
  }

  async function restoreTrashItem(id: number) {
    setTrashBusy(id);
    try {
      await restoreModel(id);
      setTrashItems((current) => current.filter((item) => item.id !== id));
      toast.success("Model restored.");
    } catch (e) {
      toast.error(e);
    } finally {
      setTrashBusy(null);
    }
  }

  async function purgeTrashItem(id: number) {
    if (!window.confirm("Permanently delete this model and its files?")) return;
    setTrashBusy(id);
    try {
      await purgeModel(id);
      setTrashItems((current) => current.filter((item) => item.id !== id));
      toast.success("Model permanently deleted.");
    } catch (e) {
      toast.error(e);
    } finally {
      setTrashBusy(null);
    }
  }

  async function purgeExpiredItems() {
    setTrashBusy("expired");
    try {
      const result = await purgeExpiredTrash();
      toast.success(`${result.purged_count} expired model${result.purged_count === 1 ? "" : "s"} deleted.`);
      await loadTrash();
    } catch (e) {
      toast.error(e);
    } finally {
      setTrashBusy(null);
    }
  }

  const statItems = [
    { label: "Vault version", value: health ? `${health.name} v${health.version}` : "Loading...", desc: "API server status and version", icon: Server },
    { label: "Database", value: health?.status === "ok" ? "Connected" : "Unknown", desc: "SQLite by default, Postgres optional", icon: Database },
    { label: "Storage backend", value: stats ? stats.storage.backend.toUpperCase() : "...", desc: stats?.storage.bucket ?? stats?.storage.prefix ?? "Configured vault storage", icon: HardDrive },
    { label: "Storage used", value: stats ? formatBytes(stats.storage.total_size_bytes) : "...", desc: stats ? `${stats.storage.object_count} stored objects` : "Real usage from storage backend", icon: Activity },
    { label: "Indexed files", value: stats ? `${stats.file_count}` : "...", desc: stats ? `${formatBytes(stats.indexed_size_bytes)} tracked in the database` : "Stored model and G-code files", icon: Database },
    { label: "Collections", value: stats ? `${stats.collection_count}` : "...", desc: "Hierarchical collection tree entries", icon: Tag },
    { label: "Tags", value: stats ? `${stats.tag_count}` : "...", desc: "Flat tag vocabulary size", icon: Tag },
  ];

  const vaultSummaryItems = [
    { label: "Models", value: stats ? `${stats.model_count}` : "...", desc: "Live library entries" },
    { label: "Files", value: stats ? `${stats.file_count}` : "...", desc: `${stats?.source_file_count ?? 0} source / ${stats?.gcode_file_count ?? 0} G-code` },
    { label: "Used", value: stats ? formatBytes(stats.storage.total_size_bytes) : "...", desc: "Actual storage backend usage" },
    { label: "Printers", value: stats ? `${stats.printer_count}` : "...", desc: "Configured devices" },
  ];

  return (
    <div className="w-full space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-foreground tracking-tight">Settings</h2>
        <p className="text-sm text-muted-foreground">Vault configuration and display preferences</p>
      </div>

      <div className="bg-card border border-border rounded">
        <div className="flex flex-wrap gap-1 p-1">
          {SETTINGS_SECTIONS.map((section) => {
            const Icon = section.icon;
            const active = activeSection === section.id;
            return (
              <button
                key={section.id}
                type="button"
                onClick={() => setActiveSection(section.id)}
                className={`inline-flex items-center gap-2 rounded px-3 py-2 text-xs uppercase tracking-wider transition-colors ${
                  active
                    ?  "bg-blue-50 dark:bg-orange-950/50 text-blue-700 dark:text-orange-400 dark:text-orange-400"
                    : "text-muted-foreground hover:bg-muted/50"
                }`}
              >
                <Icon className="h-3.5 w-3.5" />
                {section.label}
              </button>
            );
          })}
        </div>
      </div>

      {activeSection === "overview" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-6 lg:gap-8">
          <div className="lg:col-span-2 bg-card border border-border rounded">
            <div className="px-4 sm:px-6 lg:px-8 py-4 sm:py-5 border-b border-border dark:bg-[var(--surface-container-high)] rounded-t">
              <h3 className="text-sm font-semibold text-foreground">Vault health</h3>
              <p className="text-xs text-muted-foreground mt-0.5">Current library, storage, and printer totals</p>
            </div>
            <div className="grid grid-cols-2 lg:grid-cols-4 divide-x-0 lg:divide-x divide-y lg:divide-y-0 divide-border">
              {vaultSummaryItems.map((item) => (
                <div key={item.label} className="p-4 sm:p-6 bg-card dark:bg-[var(--surface-container)]">
                  <p className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
                    {item.label}
                  </p>
                  <p className="mt-2 text-2xl font-semibold text-foreground">
                    {item.value}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {item.desc}
                  </p>
                </div>
              ))}
            </div>
          </div>

          <div className="space-y-4 sm:space-y-6">
            <div className="bg-card border border-border rounded">
              <div className="px-4 sm:px-6 lg:px-8 py-4 sm:py-5 border-b border-border flex items-center gap-2 sm:gap-3">
                <div className="w-9 h-9 rounded bg-muted flex items-center justify-center text-muted-foreground flex-shrink-0">
                  <Download className="h-4 w-4" />
                </div>
                <div>
                  <h3 className="text-sm font-semibold text-foreground">
                    Data export
                  </h3>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    Metadata only, no raw STL/3MF/G-code files
                  </p>
                </div>
              </div>
              <div className="p-3 sm:p-4 lg:p-6 space-y-4">
                <p className="text-sm text-muted-foreground leading-relaxed">
                  Download your searchable library context for spreadsheets, audits, migrations, or local AI prompts.
                </p>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => exportData("json")}
                    disabled={exporting !== null}
                    className="inline-flex items-center gap-1.5 px-3 py-2 rounded border border-border text-muted-foreground hover:bg-muted/50 transition-colors text-xs uppercase tracking-wider disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    <Download className="h-3.5 w-3.5" />
                    {exporting === "json" ? "Exporting" : "JSON"}
                  </button>
                  <button
                    type="button"
                    onClick={() => exportData("csv")}
                    disabled={exporting !== null}
                    className="inline-flex items-center gap-1.5 px-3 py-2 rounded border border-border text-muted-foreground hover:bg-muted/50 transition-colors text-xs uppercase tracking-wider disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    <Download className="h-3.5 w-3.5" />
                    {exporting === "csv" ? "Exporting" : "CSV"}
                  </button>
                </div>
              </div>
            </div>

            <div className="bg-card border border-border rounded">
              <div className="px-4 sm:px-6 py-3 border-b border-border">
                <h3 className="text-sm font-semibold text-foreground">About</h3>
              </div>
              <div className="p-3 sm:p-4">
                <p className="text-xs sm:text-sm text-muted-foreground leading-relaxed">
                  <strong className="text-foreground">PrintStash</strong> keeps source meshes and sliced jobs searchable with extracted print metadata, deduplication, and a REST API.
                </p>
              </div>
            </div>
          </div>

          <div className="bg-card border border-border rounded">
            <div className="px-4 sm:px-6 lg:px-8 py-4 sm:py-5 border-b border-border dark:bg-[var(--surface-container-high)] rounded-t">
              <h3 className="text-sm font-semibold text-foreground">Vault status</h3>
              <p className="text-xs text-muted-foreground mt-0.5">System overview and configuration</p>
            </div>
            <div className="p-3 sm:p-4 lg:p-6">
              {statItems.map((item) => (
                <div key={item.label} className="flex items-center gap-3 sm:gap-4 py-3 border-b border-border last:border-b-0">
                  <div className="w-9 h-9 rounded dark:bg-[var(--surface-container-high)] bg-muted flex items-center justify-center text-muted-foreground flex-shrink-0">
                    <item.icon className="h-4 w-4" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-foreground">{item.label}</p>
                    <p className="text-xs text-muted-foreground truncate">{item.desc}</p>
                  </div>
                  <span className="font-mono text-xs sm:text-sm text-foreground text-right">{item.value}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {activeSection === "access" && (
        <div className="max-w-3xl">
          <div className="bg-card border border-border rounded">
            <div className="px-4 sm:px-6 lg:px-8 py-4 sm:py-5 border-b border-border flex items-center gap-2 sm:gap-3">
              <div className="w-9 h-9 rounded bg-muted flex items-center justify-center text-muted-foreground flex-shrink-0">
                <KeyRound className="h-4 w-4" />
              </div>
              <div>
                <h3 className="text-sm font-semibold text-foreground">
                  API keys
                </h3>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Create credentials for scripts and integrations, then exchange them for a JWT at login.
                </p>
              </div>
            </div>
            <div className="p-3 sm:p-4 lg:p-6 space-y-4">
              {!user ? (
                <p className="text-sm text-muted-foreground">
                  Sign in to create API keys.
                </p>
              ) : (
                <>
                  <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
                    <input
                      value={keyName}
                      onChange={(event) => setKeyName(event.target.value)}
                      className="w-full px-3 py-2 bg-card border border-border rounded text-sm text-foreground focus:outline-none focus:border-blue-600 dark:focus:border-blue-500 dark:border-orange-500"
                      maxLength={128}
                    />
                    <button
                      type="button"
                      onClick={generateApiKey}
                      disabled={keyBusy}
                      className="inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded bg-blue-600 dark:bg-orange-600 text-white hover:opacity-90 transition-opacity text-xs uppercase tracking-wider disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      <KeyRound className="h-3.5 w-3.5" />
                      Generate
                    </button>
                  </div>

                  {newApiKey && (
                    <div className="border border-blue-200 dark:border-orange-800 dark:border-orange-800 bg-blue-50 dark:bg-orange-950/30 rounded p-3 space-y-2">
                      <p className="text-xs text-muted-foreground">
                        Copy this key now. It will only be shown once.
                      </p>
                      <div className="flex items-center gap-2">
                        <code className="flex-1 min-w-0 overflow-x-auto whitespace-nowrap rounded bg-muted px-3 py-2 text-xs text-foreground">
                          {newApiKey}
                        </code>
                        <button
                          type="button"
                          onClick={copyApiKey}
                          className="inline-flex h-9 w-9 items-center justify-center rounded border border-border text-muted-foreground hover:bg-muted/50"
                          title="Copy API key"
                        >
                          <Copy className="h-4 w-4" />
                        </button>
                      </div>
                    </div>
                  )}

                  <div className="space-y-2">
                    {apiKeys.length === 0 ? (
                      <p className="text-sm text-muted-foreground">
                        No active API keys.
                      </p>
                    ) : (
                      apiKeys.map((key) => (
                        <div
                          key={key.id}
                          className="flex items-center gap-3 border border-border rounded px-3 py-2"
                        >
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-sm text-foreground">
                              {key.name}
                            </p>
                            <p className="font-mono text-[11px] text-muted-foreground">
                              {key.prefix}... · {key.last_used_at ? "Used" : "Never used"}
                            </p>
                          </div>
                          <button
                            type="button"
                            onClick={() => deleteApiKey(key.id)}
                            disabled={keyBusy}
                            className="inline-flex h-9 w-9 items-center justify-center rounded border border-border text-red-500 hover:bg-red-500/10 disabled:opacity-50"
                            title="Revoke API key"
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>
                        </div>
                      ))
                    )}
                  </div>

                  <div className="rounded border border-border bg-background p-3">
                    <p className="text-xs text-muted-foreground leading-relaxed">
                      Use your username with this API key on <code className="font-mono">/api/v1/auth/login</code>. The response is the same JWT Bearer token used by the app, so later requests only need the normal <code className="font-mono">Authorization</code> header.
                    </p>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {activeSection === "storage" && (
        <div className="max-w-3xl">
          <StorageConfigCard />
        </div>
      )}

      {activeSection === "design" && (
        <div className="max-w-5xl">
          <div className="bg-card border border-border rounded">
            <div className="px-4 sm:px-6 lg:px-8 py-4 sm:py-5 border-b border-border flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
              <div className="min-w-0">
                <h3 className="text-sm font-semibold text-foreground">
                  Model metadata
                </h3>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Choose which metadata fields appear on model detail pages.
                </p>
              </div>
              <button
                type="button"
                onClick={resetMetadataPreferences}
                className="inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded border border-border text-muted-foreground hover:bg-muted/50 transition-colors text-xs uppercase tracking-wider"
              >
                <RotateCcw className="h-3.5 w-3.5" />
                Reset
              </button>
            </div>
            <div className="grid gap-2 p-3 sm:grid-cols-2 sm:p-4 lg:grid-cols-3 lg:p-6">
              {METADATA_FIELDS.map((field) => (
                <label
                  key={field.id}
                  className="flex items-center justify-between gap-3 rounded border border-border bg-background px-3 py-2.5"
                >
                  <span className="text-sm text-foreground">
                    {field.label}
                  </span>
                  <input
                    type="checkbox"
                    checked={metadataPrefs[field.id]}
                    onChange={(event) => updateMetadataPreference(
                      field.id,
                      event.target.checked,
                    )}
                    className="h-4 w-4 accent-[var(--primary)]"
                  />
                </label>
              ))}
            </div>
          </div>
        </div>
      )}

      {activeSection === "trash" && (
        <div className="max-w-5xl space-y-4 sm:space-y-6">
          <div className="bg-card border border-border rounded">
            <div className="px-4 sm:px-6 lg:px-8 py-4 sm:py-5 border-b border-border flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
              <div className="min-w-0">
                <h3 className="text-sm font-semibold text-foreground">
                  Trash retention
                </h3>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Soft-deleted models stay restorable until the retention window expires.
                </p>
              </div>
              <button
                type="button"
                onClick={loadTrash}
                disabled={trashLoading}
                className="inline-flex h-9 w-9 items-center justify-center rounded border border-border text-muted-foreground hover:bg-muted/50 disabled:opacity-50"
                title="Refresh trash"
              >
                <RefreshCw className={`h-4 w-4 ${trashLoading ? "animate-spin" : ""}`} />
              </button>
            </div>
            <div className="p-3 sm:p-4 lg:p-6 grid gap-3 sm:grid-cols-[160px_auto_auto] sm:items-end">
              <label className="block">
                <span className="block text-[11px] text-muted-foreground mb-1">
                  Days
                </span>
                <input
                  type="number"
                  min={-1}
                  value={trashRetentionDays}
                  onChange={(event) => setTrashRetentionDays(Number(event.target.value))}
                  disabled={!user || trashBusy === "settings"}
                  className="w-full px-2.5 py-2 text-sm rounded border border-border bg-background text-foreground disabled:opacity-50"
                />
              </label>
              <button
                type="button"
                onClick={saveTrashRetention}
                disabled={!user || trashBusy === "settings"}
                className="inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded bg-blue-600 dark:bg-orange-600 text-white hover:opacity-90 transition-opacity text-xs uppercase tracking-wider disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <Trash2 className="h-3.5 w-3.5" />
                {trashBusy === "settings" ? "Saving" : "Save retention"}
              </button>
              <button
                type="button"
                onClick={purgeExpiredItems}
                disabled={!user || trashBusy === "expired" || trashRetentionDays < 0}
                className="inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded border border-border text-muted-foreground hover:bg-muted/50 transition-colors text-xs uppercase tracking-wider disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <Eraser className="h-3.5 w-3.5" />
                {trashBusy === "expired" ? "Purging" : "Purge expired"}
              </button>
            </div>
          </div>

          <div className="bg-card border border-border rounded">
            <div className="px-4 sm:px-6 lg:px-8 py-4 sm:py-5 border-b border-border">
              <h3 className="text-sm font-semibold text-foreground">
                Deleted models
              </h3>
              <p className="text-xs text-muted-foreground mt-0.5">
                Restore models or remove them permanently from storage.
              </p>
            </div>
            <div className="divide-y divide-slate-100">
              {!user ? (
                <p className="p-4 sm:p-6 text-sm text-muted-foreground">
                  Sign in to manage the trash.
                </p>
              ) : trashLoading ? (
                <p className="p-4 sm:p-6 text-sm text-muted-foreground">
                  Loading...
                </p>
              ) : trashItems.length === 0 ? (
                <p className="p-4 sm:p-6 text-sm text-muted-foreground">
                  Trash is empty.
                </p>
              ) : (
                trashItems.map((item) => (
                  <div
                    key={item.id}
                    className="grid gap-3 p-4 sm:p-5 lg:grid-cols-[1fr_auto] lg:items-center"
                  >
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="truncate text-sm font-medium text-foreground">
                          {item.name}
                        </p>
                        <span className="font-mono text-[10px] uppercase tracking-wider px-2 py-0.5 rounded border border-border text-muted-foreground">
                          {item.file_count} files
                        </span>
                        <span className="font-mono text-[10px] uppercase tracking-wider px-2 py-0.5 rounded border border-border text-muted-foreground">
                          {formatBytes(item.size_bytes)}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">
                        Deleted {formatDate(item.deleted_at)} · Expires {formatDate(item.expires_at)}
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-2 lg:justify-end">
                      <button
                        type="button"
                        onClick={() => restoreTrashItem(item.id)}
                        disabled={trashBusy !== null}
                        className="inline-flex items-center gap-1.5 px-3 py-2 rounded border border-border text-muted-foreground hover:bg-muted/50 transition-colors text-xs uppercase tracking-wider disabled:opacity-50"
                      >
                        <RotateCcw className="h-3.5 w-3.5" />
                        Restore
                      </button>
                      <button
                        type="button"
                        onClick={() => purgeTrashItem(item.id)}
                        disabled={trashBusy !== null}
                        className="inline-flex items-center gap-1.5 px-3 py-2 rounded border border-red-500/30 text-red-500 hover:bg-red-500/10 transition-colors text-xs uppercase tracking-wider disabled:opacity-50"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                        Delete
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}

    </div>
  );
}
