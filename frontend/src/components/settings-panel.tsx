"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Boxes,
  Database,
  Download,
  Eraser,
  Files,
  FolderTree,
  HardDrive,
  Info,
  KeyRound,
  Copy,
  Loader2,
  Palette,
  Printer,
  ShieldCheck,
  RefreshCw,
  RotateCcw,
  Trash2,
  Server,
  Tag,
  UserPlus,
  Users,
} from "lucide-react";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { StorageConfigCard } from "@/components/storage-config-card";
import {
  createApiKey,
  createAdminUser,
  createBackup,
  deactivateAdminUser,
  deleteCollectionPermission,
  downloadModelExport,
  getVaultConfig,
  getVaultStats,
  listCollectionPermissions,
  listCollections,
  listApiKeys,
  listAdminUsers,
  listTrash,
  purgeExpiredTrash,
  purgeModel,
  resetAdminUserPassword,
  restoreModel,
  revokeApiKey,
  updateCollectionPermission,
  updateAdminUser,
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
import {
  CARD_METRIC_OPTIONS,
  CardMetricId,
  CardMetrics,
  DEFAULT_CARD_METRICS,
  readCardMetrics,
  writeCardMetrics,
} from "@/lib/card-metrics";
import { toast } from "@/lib/toast";
import { CHANGELOG, GITHUB_REPO } from "@/lib/changelog";
import type {
  ApiKeyRead,
  CollectionPermissionRead,
  CollectionRead,
  CollectionRole,
  TrashedModelRead,
  UserRead,
  VaultStatsRead,
} from "@/types";

interface HealthResponse {
  status: string;
  name: string;
  version: string;
}

type SettingsSection = "overview" | "access" | "storage" | "design" | "trash" | "about";

const SETTINGS_SECTIONS: {
  id: SettingsSection;
  label: string;
  icon: typeof Server;
}[] = [
  { id: "overview", label: "Overview", icon: Server },
  { id: "access", label: "Users & Access", icon: Users },
  { id: "storage", label: "Storage", icon: HardDrive },
  { id: "design", label: "Design", icon: Palette },
  { id: "trash", label: "Trash", icon: Trash2 },
  { id: "about", label: "About", icon: Info },
];

// Shared button styles — keep settings actions visually uniform and theme-aware.
const BTN_PRIMARY =
  "inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded bg-[var(--primary)] text-[var(--primary-foreground)] text-xs font-medium uppercase tracking-wider hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed";
const BTN_SECONDARY =
  "inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded border border-border text-muted-foreground hover:bg-muted transition-colors text-xs font-medium uppercase tracking-wider disabled:opacity-50 disabled:cursor-not-allowed";
const BTN_ICON =
  "inline-flex h-9 w-9 items-center justify-center rounded border border-border text-muted-foreground hover:bg-muted transition-colors disabled:opacity-50 disabled:cursor-not-allowed";
const INPUT =
  "w-full px-3 py-2 bg-background border border-border rounded text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent disabled:opacity-50";

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

// Consistent card shell used across every settings section.
function SettingsCard({
  icon: Icon,
  title,
  description,
  action,
  children,
  className,
}: {
  icon?: typeof Server;
  title: string;
  description?: string;
  action?: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`bg-card border border-border rounded ${className ?? ""}`}>
      <div className="px-4 sm:px-5 py-3.5 border-b border-border flex items-start justify-between gap-3">
        <div className="flex items-start gap-3 min-w-0">
          {Icon && (
            <div className="w-8 h-8 rounded bg-muted flex items-center justify-center text-muted-foreground flex-shrink-0">
              <Icon className="h-4 w-4" />
            </div>
          )}
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-foreground">{title}</h3>
            {description && (
              <p className="text-xs text-muted-foreground mt-0.5">{description}</p>
            )}
          </div>
        </div>
        {action && <div className="flex-shrink-0">{action}</div>}
      </div>
      {children}
    </div>
  );
}

export function SettingsPanel() {
  const { user } = useAuth();
  const [activeSection, setActiveSection] = useState<SettingsSection>("overview");
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [stats, setStats] = useState<VaultStatsRead | null>(null);
  const [exporting, setExporting] = useState<"json" | "csv" | null>(null);
  const [apiKeys, setApiKeys] = useState<ApiKeyRead[]>([]);
  const [users, setUsers] = useState<UserRead[]>([]);
  const [usersBusy, setUsersBusy] = useState<number | "create" | null>(null);
  const [newUsername, setNewUsername] = useState("");
  const [newUserEmail, setNewUserEmail] = useState("");
  const [newUserPassword, setNewUserPassword] = useState("");
  const [passwordDrafts, setPasswordDrafts] = useState<Record<number, string>>({});
  const [accessCollections, setAccessCollections] = useState<CollectionRead[]>([]);
  const [collectionPermissions, setCollectionPermissions] = useState<CollectionPermissionRead[]>([]);
  const [accessUserId, setAccessUserId] = useState<number | "">("");
  const [accessCollectionId, setAccessCollectionId] = useState<number | "">("");
  const [accessRole, setAccessRole] = useState<CollectionRole>("view");
  const [accessBusy, setAccessBusy] = useState<"load" | "save" | string | null>(null);
  const [newApiKey, setNewApiKey] = useState<string | null>(null);
  const [keyName, setKeyName] = useState("Programmatic access");
  const [keyBusy, setKeyBusy] = useState(false);
  const [trashItems, setTrashItems] = useState<TrashedModelRead[]>([]);
  const [trashLoading, setTrashLoading] = useState(false);
  const [trashBusy, setTrashBusy] = useState<number | "expired" | "settings" | null>(null);
  const [trashRetentionDays, setTrashRetentionDays] = useState(30);
  const [purgeTarget, setPurgeTarget] = useState<number | null>(null);
  const [backingUp, setBackingUp] = useState(false);
  const [metadataPrefs, setMetadataPrefs] = useState<MetadataPreferences>(
    DEFAULT_METADATA_PREFERENCES,
  );
  const [cardMetrics, setCardMetrics] = useState<CardMetrics>(DEFAULT_CARD_METRICS);

  const refreshUsers = useCallback(async () => {
    if (!user?.is_superuser) return;
    setUsers(await listAdminUsers());
  }, [user]);

  const refreshCollectionAccess = useCallback(async () => {
    if (!user?.is_superuser) return;
    setAccessBusy("load");
    try {
      const rows = await listCollections();
      const permissionGroups = await Promise.all(
        rows.map((collection) => listCollectionPermissions(collection.id)),
      );
      setAccessCollections(rows);
      setCollectionPermissions(permissionGroups.flat());
    } catch (e) {
      toast.error(e);
    } finally {
      setAccessBusy(null);
    }
  }, [user]);

  useEffect(() => {
    fetch("/api/v1/health")
      .then((r) => r.json())
      .then(setHealth)
      .catch(() => {});
    getVaultStats()
      .then(setStats)
      .catch(() => {});
    setMetadataPrefs(readMetadataPreferences());
    setCardMetrics(readCardMetrics());
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
    if (user.is_superuser) {
      refreshUsers().catch(() => {});
      refreshCollectionAccess().catch(() => {});
    }
  }, [user, refreshUsers, refreshCollectionAccess]);

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

  async function handleBackupNow() {
    setBackingUp(true);
    try {
      const meta = await createBackup();
      const mb = (meta.size_bytes / 1024 / 1024).toFixed(1);
      toast.success(`Backup created — ${meta.file_count} files, ${mb} MB`);
    } catch (e) {
      toast.error(e);
    } finally {
      setBackingUp(false);
    }
  }

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

  async function saveCollectionAccess() {
    if (!accessUserId || !accessCollectionId) return;
    setAccessBusy("save");
    try {
      await updateCollectionPermission(Number(accessCollectionId), Number(accessUserId), {
        role: accessRole,
      });
      await refreshCollectionAccess();
      toast.success("Collection access saved.");
    } catch (e) {
      toast.error(e);
    } finally {
      setAccessBusy(null);
    }
  }

  async function removeCollectionAccess(collectionId: number, userId: number) {
    setAccessBusy(`${collectionId}:${userId}`);
    try {
      await deleteCollectionPermission(collectionId, userId);
      setCollectionPermissions((current) =>
        current.filter((row) => row.collection_id !== collectionId || row.user_id !== userId),
      );
      toast.success("Collection access removed.");
    } catch (e) {
      toast.error(e);
    } finally {
      setAccessBusy(null);
    }
  }

  async function createUser() {
    const username = newUsername.trim();
    const password = newUserPassword.trim();
    if (!username || !password) return;
    setUsersBusy("create");
    try {
      await createAdminUser({
        username,
        password,
        email: newUserEmail.trim() || null,
      });
      setNewUsername("");
      setNewUserEmail("");
      setNewUserPassword("");
      await refreshUsers();
      toast.success("User created.");
    } catch (e) {
      toast.error(e);
    } finally {
      setUsersBusy(null);
    }
  }

  async function patchUser(id: number, payload: Partial<UserRead>) {
    setUsersBusy(id);
    try {
      await updateAdminUser(id, {
        email: payload.email,
        is_active: payload.is_active,
        is_superuser: payload.is_superuser,
      });
      await refreshUsers();
      toast.success("User updated.");
    } catch (e) {
      toast.error(e);
    } finally {
      setUsersBusy(null);
    }
  }

  async function resetUserPassword(id: number) {
    const password = passwordDrafts[id]?.trim();
    if (!password) return;
    setUsersBusy(id);
    try {
      await resetAdminUserPassword(id, { password });
      setPasswordDrafts((current) => ({ ...current, [id]: "" }));
      toast.success("Password reset.");
    } catch (e) {
      toast.error(e);
    } finally {
      setUsersBusy(null);
    }
  }

  async function deactivateUser(id: number) {
    setUsersBusy(id);
    try {
      await deactivateAdminUser(id);
      await refreshUsers();
      toast.success("User deactivated.");
    } catch (e) {
      toast.error(e);
    } finally {
      setUsersBusy(null);
    }
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

  function updateCardMetric(slot: 0 | 1 | 2, id: CardMetricId) {
    const next: CardMetrics = [...cardMetrics] as CardMetrics;
    next[slot] = id;
    setCardMetrics(next);
    writeCardMetrics(next);
    // Notify other tabs / components
    window.dispatchEvent(new StorageEvent("storage", { key: "printstash.card.metrics" }));
  }

  function resetCardMetrics() {
    setCardMetrics(DEFAULT_CARD_METRICS);
    writeCardMetrics(DEFAULT_CARD_METRICS);
    window.dispatchEvent(new StorageEvent("storage", { key: "printstash.card.metrics" }));
    toast.success("Card metrics reset.");
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
    setPurgeTarget(id);
  }

  async function confirmPurge() {
    if (purgeTarget === null) return;
    const id = purgeTarget;
    setPurgeTarget(null);
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

  // KPI tiles — the headline numbers, no overlap with the system list below.
  const kpiItems = [
    { label: "Models", value: stats ? `${stats.model_count}` : "...", desc: "Live library entries", icon: Boxes },
    { label: "Files", value: stats ? `${stats.file_count}` : "...", desc: `${stats?.source_file_count ?? 0} source · ${stats?.gcode_file_count ?? 0} G-code`, icon: Files },
    { label: "Storage used", value: stats ? formatBytes(stats.storage.total_size_bytes) : "...", desc: stats ? `${stats.storage.object_count} stored objects` : "Backend usage", icon: HardDrive },
    { label: "Printers", value: stats ? `${stats.printer_count}` : "...", desc: "Configured devices", icon: Printer },
  ];

  // System detail rows — configuration facts, distinct from the KPI tiles.
  const systemItems = [
    { label: "Vault version", value: health ? `${health.name} v${health.version}` : "Loading...", desc: "API server status and version", icon: Server },
    { label: "Database", value: health?.status === "ok" ? "Connected" : "Unknown", desc: "SQLite by default, Postgres optional", icon: Database },
    { label: "Storage backend", value: stats ? stats.storage.backend.toUpperCase() : "...", desc: stats?.storage.bucket ?? stats?.storage.prefix ?? "Configured vault storage", icon: HardDrive },
    { label: "Indexed files", value: stats ? formatBytes(stats.indexed_size_bytes) : "...", desc: "Tracked in the database", icon: Files },
    { label: "Collections", value: stats ? `${stats.collection_count}` : "...", desc: "Hierarchical tree entries", icon: FolderTree },
    { label: "Tags", value: stats ? `${stats.tag_count}` : "...", desc: "Flat tag vocabulary size", icon: Tag },
  ];

  const nonSuperUsers = users.filter((row) => !row.is_superuser);
  const activeAccessUser = accessUserId
    ? users.find((row) => row.id === Number(accessUserId))
    : null;
  const selectedUserPermissions = accessUserId
    ? collectionPermissions.filter((row) => row.user_id === Number(accessUserId))
    : [];
  const collectionById = new Map(accessCollections.map((row) => [row.id, row]));
  const selectedUserGrantedCollectionIds = new Set(
    selectedUserPermissions.map((row) => row.collection_id),
  );
  const grantableCollections = accessCollections.filter(
    (row) => !selectedUserGrantedCollectionIds.has(row.id),
  );

  return (
    <div className="w-full space-y-6">
      <ConfirmModal
        open={purgeTarget !== null}
        onClose={() => setPurgeTarget(null)}
        onConfirm={confirmPurge}
        busy={typeof trashBusy === "number"}
        title="Permanently delete?"
        description="This will delete the model and all its files immediately. This cannot be undone."
        confirmLabel="Delete forever"
      />

      <div>
        <h2 className="text-2xl font-bold text-foreground tracking-tight">Settings</h2>
        <p className="text-sm text-muted-foreground">Vault configuration and display preferences</p>
      </div>

      {/* Section tabs — underline indicator, scrolls horizontally on small screens */}
      <div className="border-b border-border">
        <div className="flex gap-1 overflow-x-auto -mb-px">
          {SETTINGS_SECTIONS.map((section) => {
            const Icon = section.icon;
            const active = activeSection === section.id;
            return (
              <button
                key={section.id}
                type="button"
                onClick={() => setActiveSection(section.id)}
                className={`relative inline-flex items-center gap-2 whitespace-nowrap border-b-2 px-3.5 py-2.5 text-sm font-medium transition-colors ${
                  active
                    ? "border-[var(--primary)] text-[var(--primary)]"
                    : "border-transparent text-muted-foreground hover:text-foreground"
                }`}
              >
                <Icon className="h-4 w-4" />
                {section.label}
              </button>
            );
          })}
        </div>
      </div>

      {activeSection === "overview" && (
        <div className="space-y-6">
          {/* KPI tiles */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {kpiItems.map((item) => {
              const Icon = item.icon;
              return (
                <div key={item.label} className="bg-card border border-border rounded p-4 sm:p-5">
                  <div className="flex items-center justify-between">
                    <p className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
                      {item.label}
                    </p>
                    <Icon className="h-4 w-4 text-muted-foreground/50" />
                  </div>
                  <p className="mt-2 text-2xl font-semibold text-foreground truncate">
                    {item.value}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground truncate">
                    {item.desc}
                  </p>
                </div>
              );
            })}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
            {/* System information */}
            <SettingsCard
              icon={Server}
              title="System"
              description="Server status and vault configuration"
            >
              <div className="px-4 sm:px-5">
                {systemItems.map((item) => (
                  <div
                    key={item.label}
                    className="flex items-center gap-4 py-3 border-b border-border last:border-b-0"
                  >
                    <div className="w-9 h-9 rounded bg-muted flex items-center justify-center text-muted-foreground flex-shrink-0">
                      <item.icon className="h-4 w-4" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-foreground">{item.label}</p>
                      <p className="text-xs text-muted-foreground truncate">{item.desc}</p>
                    </div>
                    <span className="font-mono text-xs sm:text-sm text-foreground text-right flex-shrink-0">
                      {item.value}
                    </span>
                  </div>
                ))}
              </div>
            </SettingsCard>

            {/* Data export */}
            <SettingsCard
              icon={Download}
              title="Data export"
              description="Metadata only — no raw STL/3MF/G-code files"
            >
              <div className="p-4 sm:p-5 space-y-4">
                <p className="text-sm text-muted-foreground leading-relaxed">
                  Download your searchable library context for spreadsheets, audits, migrations, or local AI prompts.
                </p>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => exportData("json")}
                    disabled={exporting !== null}
                    className={BTN_SECONDARY}
                  >
                    <Download className="h-3.5 w-3.5" />
                    {exporting === "json" ? "Exporting" : "JSON"}
                  </button>
                  <button
                    type="button"
                    onClick={() => exportData("csv")}
                    disabled={exporting !== null}
                    className={BTN_SECONDARY}
                  >
                    <Download className="h-3.5 w-3.5" />
                    {exporting === "csv" ? "Exporting" : "CSV"}
                  </button>
                </div>
              </div>
            </SettingsCard>
          </div>
        </div>
      )}

      {activeSection === "access" && (
        <div className="space-y-6">
          {user?.is_superuser && (
            <SettingsCard
              icon={Users}
              title="Users"
              description="Create users, assign vault admins, disable accounts, and reset passwords."
            >
              <div className="p-4 sm:p-5 space-y-4">
                <div className="grid gap-2 lg:grid-cols-[1fr_1fr_1fr_auto]">
                  <input
                    value={newUsername}
                    onChange={(event) => setNewUsername(event.target.value)}
                    className={INPUT}
                    maxLength={128}
                    placeholder="Username"
                  />
                  <input
                    value={newUserEmail}
                    onChange={(event) => setNewUserEmail(event.target.value)}
                    className={INPUT}
                    maxLength={255}
                    placeholder="Email"
                  />
                  <input
                    value={newUserPassword}
                    onChange={(event) => setNewUserPassword(event.target.value)}
                    className={INPUT}
                    type="password"
                    maxLength={256}
                    placeholder="Initial password"
                  />
                  <button
                    type="button"
                    onClick={createUser}
                    disabled={usersBusy === "create" || !newUsername.trim() || newUserPassword.trim().length < 8}
                    className={BTN_PRIMARY}
                  >
                    <UserPlus className="h-3.5 w-3.5" />
                    Create
                  </button>
                </div>

                <div className="space-y-2">
                  {users.length === 0 ? (
                    <p className="text-sm text-muted-foreground">No users.</p>
                  ) : (
                    users.map((row) => (
                      <div
                        key={row.id}
                        className="rounded border border-border p-3 space-y-3"
                      >
                        <div className="flex flex-col gap-3 md:flex-row md:items-center">
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2">
                              <p className="truncate text-sm font-medium text-foreground">
                                {row.username}
                              </p>
                              {row.is_superuser && (
                                <span className="inline-flex items-center gap-1 rounded bg-muted px-2 py-0.5 font-mono text-[10px] uppercase text-muted-foreground">
                                  <ShieldCheck className="h-3 w-3" />
                                  Admin
                                </span>
                              )}
                              {!row.is_active && (
                                <span className="rounded bg-red-500/10 px-2 py-0.5 font-mono text-[10px] uppercase text-red-600">
                                  Disabled
                                </span>
                              )}
                            </div>
                            <p className="text-xs text-muted-foreground">
                              {row.email || "No email"} · Created {formatDate(row.created_at)}
                            </p>
                          </div>
                          <div className="flex flex-wrap gap-2">
                            <button
                              type="button"
                              disabled={usersBusy === row.id}
                              onClick={() => patchUser(row.id, { is_superuser: !row.is_superuser })}
                              className={BTN_SECONDARY}
                            >
                              {row.is_superuser ? "Remove admin" : "Make admin"}
                            </button>
                            <button
                              type="button"
                              disabled={usersBusy === row.id}
                              onClick={() =>
                                row.is_active
                                  ? deactivateUser(row.id)
                                  : patchUser(row.id, { is_active: true })
                              }
                              className={BTN_SECONDARY}
                            >
                              {row.is_active ? "Disable" : "Enable"}
                            </button>
                          </div>
                        </div>
                        <div className="grid gap-2 md:grid-cols-[1fr_auto]">
                          <input
                            value={passwordDrafts[row.id] ?? ""}
                            onChange={(event) =>
                              setPasswordDrafts((current) => ({
                                ...current,
                                [row.id]: event.target.value,
                              }))
                            }
                            className={INPUT}
                            type="password"
                            placeholder="New password"
                          />
                          <button
                            type="button"
                            onClick={() => resetUserPassword(row.id)}
                            disabled={usersBusy === row.id || (passwordDrafts[row.id]?.trim().length ?? 0) < 8}
                            className={BTN_SECONDARY}
                          >
                            Reset password
                          </button>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </SettingsCard>
          )}

          {user?.is_superuser && (
            <SettingsCard
              icon={FolderTree}
              title="Collection access"
              description="Assign view, edit, or admin access per user. Child collections inherit parent grants."
              action={
                <button
                  type="button"
                  onClick={refreshCollectionAccess}
                  disabled={accessBusy === "load"}
                  className={BTN_ICON}
                  title="Refresh collection access"
                >
                  <RefreshCw className={`h-4 w-4 ${accessBusy === "load" ? "animate-spin" : ""}`} />
                </button>
              }
            >
              <div className="p-4 sm:p-5 space-y-4">
                <div className="grid gap-2 lg:grid-cols-[1fr_1.4fr_auto_auto]">
                  <select
                    value={accessUserId}
                    onChange={(event) => {
                      setAccessUserId(event.target.value ? Number(event.target.value) : "");
                      setAccessCollectionId("");
                    }}
                    className={INPUT}
                    disabled={accessBusy === "load"}
                  >
                    <option value="">Select user</option>
                    {nonSuperUsers.map((row) => (
                      <option key={row.id} value={row.id}>
                        {row.username}
                      </option>
                    ))}
                  </select>
                  <select
                    value={accessCollectionId}
                    onChange={(event) => setAccessCollectionId(event.target.value ? Number(event.target.value) : "")}
                    className={INPUT}
                    disabled={!accessUserId || accessBusy === "load"}
                  >
                    <option value="">Select collection</option>
                    {grantableCollections.map((row) => (
                      <option key={row.id} value={row.id}>
                        {row.path}
                      </option>
                    ))}
                  </select>
                  <select
                    value={accessRole}
                    onChange={(event) => setAccessRole(event.target.value as CollectionRole)}
                    className={INPUT}
                    disabled={!accessUserId || !accessCollectionId || accessBusy === "load"}
                  >
                    <option value="view">View</option>
                    <option value="edit">Edit</option>
                    <option value="admin">Admin</option>
                  </select>
                  <button
                    type="button"
                    onClick={saveCollectionAccess}
                    disabled={!accessUserId || !accessCollectionId || accessBusy === "save"}
                    className={BTN_PRIMARY}
                  >
                    {accessBusy === "save" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
                    Grant
                  </button>
                </div>

                <div className="rounded border border-border overflow-hidden">
                  <div className="grid grid-cols-[1fr_auto_auto] gap-3 border-b border-border bg-muted/40 px-3 py-2 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                    <span>Collection</span>
                    <span>Role</span>
                    <span>Remove</span>
                  </div>
                  {!accessUserId ? (
                    <p className="px-3 py-4 text-sm text-muted-foreground">
                      Select a user to review collection grants.
                    </p>
                  ) : selectedUserPermissions.length === 0 ? (
                    <p className="px-3 py-4 text-sm text-muted-foreground">
                      {activeAccessUser?.username ?? "User"} has no direct collection access.
                    </p>
                  ) : (
                    selectedUserPermissions.map((row) => {
                      const collection = collectionById.get(row.collection_id);
                      const busyKey = `${row.collection_id}:${row.user_id}`;
                      return (
                        <div
                          key={`${row.collection_id}:${row.user_id}`}
                          className="grid grid-cols-[1fr_auto_auto] items-center gap-3 border-b border-border px-3 py-2 last:border-b-0"
                        >
                          <div className="min-w-0">
                            <p className="truncate text-sm text-foreground">
                              {collection?.path ?? `Collection #${row.collection_id}`}
                            </p>
                            <p className="text-xs text-muted-foreground">
                              {collection?.model_count ?? 0} models
                            </p>
                          </div>
                          <span className="rounded bg-muted px-2 py-1 font-mono text-[10px] uppercase text-muted-foreground">
                            {row.role}
                          </span>
                          <button
                            type="button"
                            onClick={() => removeCollectionAccess(row.collection_id, row.user_id)}
                            disabled={accessBusy === busyKey}
                            className="rounded p-1 text-red-600 hover:bg-red-500/10 disabled:opacity-50"
                            title="Remove collection access"
                          >
                            {accessBusy === busyKey ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                          </button>
                        </div>
                      );
                    })
                  )}
                </div>
              </div>
            </SettingsCard>
          )}

          <SettingsCard
            icon={KeyRound}
            title="API keys"
            description="Create credentials for scripts and integrations, then exchange them for a JWT at login."
          >
            <div className="p-4 sm:p-5 space-y-4">
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
                      className={INPUT}
                      maxLength={128}
                    />
                    <button
                      type="button"
                      onClick={generateApiKey}
                      disabled={keyBusy}
                      className={BTN_PRIMARY}
                    >
                      <KeyRound className="h-3.5 w-3.5" />
                      Generate
                    </button>
                  </div>

                  {newApiKey && (
                    <div className="border border-[var(--primary)]/40 bg-[var(--primary)]/10 rounded p-3 space-y-2">
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
                          className={BTN_ICON}
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

                  <div className="rounded border border-border bg-muted/40 p-3">
                    <p className="text-xs text-muted-foreground leading-relaxed">
                      Use your username with this API key on <code className="font-mono">/api/v1/auth/login</code>. The response is the same JWT Bearer token used by the app, so later requests only need the normal <code className="font-mono">Authorization</code> header.
                    </p>
                  </div>
                </>
              )}
            </div>
          </SettingsCard>
        </div>
      )}

      {activeSection === "storage" && (
        <div className="space-y-6">
          <StorageConfigCard />
          <SettingsCard
            icon={HardDrive}
            title="Manual backup"
            description="Create a full backup of the database and all stored files right now."
            action={
              <button
                type="button"
                onClick={handleBackupNow}
                disabled={backingUp}
                className={BTN_PRIMARY}
              >
                {backingUp ? (
                  <><Loader2 className="h-3.5 w-3.5 animate-spin" /> Backing up…</>
                ) : (
                  <><HardDrive className="h-3.5 w-3.5" /> Backup now</>
                )}
              </button>
            }
          />
        </div>
      )}

      {activeSection === "design" && (
        <div className="space-y-6">
          {/* Card metrics picker */}
          <SettingsCard
            icon={Palette}
            title="Model card metrics"
            description="Choose which 3 stats appear on each model card in the grid."
            action={
              <button type="button" onClick={resetCardMetrics} className={BTN_SECONDARY}>
                <RotateCcw className="h-3.5 w-3.5" />
                Reset
              </button>
            }
          >
            <div className="p-4 sm:p-5 grid gap-4 sm:grid-cols-3">
              {([0, 1, 2] as const).map((slot) => (
                <div key={slot} className="space-y-1.5">
                  <p className="text-[11px] font-mono uppercase tracking-wider text-muted-foreground">
                    Slot {slot + 1}
                  </p>
                  <div className="grid grid-cols-1 gap-1">
                    {CARD_METRIC_OPTIONS.map((opt) => {
                      const isSelected = cardMetrics[slot] === opt.id;
                      const usedInOther = cardMetrics.some((id, i) => i !== slot && id === opt.id);
                      return (
                        <button
                          key={opt.id}
                          type="button"
                          disabled={usedInOther}
                          onClick={() => updateCardMetric(slot, opt.id as CardMetricId)}
                          className={`flex items-center justify-between px-3 py-2 rounded border text-sm transition-colors ${
                            isSelected
                              ? "border-[var(--primary)] bg-[var(--primary)]/10 text-[var(--primary)]"
                              : usedInOther
                              ? "border-border bg-muted/30 text-muted-foreground/40 cursor-not-allowed"
                              : "border-border bg-background text-foreground hover:bg-muted"
                          }`}
                        >
                          <span>{opt.label}</span>
                          <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">{opt.abbr}</span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </SettingsCard>

          <SettingsCard
            icon={Info}
            title="Model metadata"
            description="Choose which metadata fields appear on model detail pages."
            action={
              <button type="button" onClick={resetMetadataPreferences} className={BTN_SECONDARY}>
                <RotateCcw className="h-3.5 w-3.5" />
                Reset
              </button>
            }
          >
            <div className="grid gap-2 p-4 sm:grid-cols-2 sm:p-5 lg:grid-cols-3">
              {METADATA_FIELDS.map((field) => (
                <label
                  key={field.id}
                  className="flex items-center justify-between gap-3 rounded border border-border bg-background px-3 py-2.5 cursor-pointer hover:bg-muted transition-colors"
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
          </SettingsCard>
        </div>
      )}

      {activeSection === "trash" && (
        <div className="space-y-6">
          <SettingsCard
            icon={Trash2}
            title="Trash retention"
            description="Soft-deleted models stay restorable until the retention window expires."
            action={
              <button
                type="button"
                onClick={loadTrash}
                disabled={trashLoading}
                className={BTN_ICON}
                title="Refresh trash"
              >
                <RefreshCw className={`h-4 w-4 ${trashLoading ? "animate-spin" : ""}`} />
              </button>
            }
          >
            <div className="p-4 sm:p-5 grid gap-3 sm:grid-cols-[160px_auto_auto] sm:items-end">
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
                  className={INPUT}
                />
              </label>
              <button
                type="button"
                onClick={saveTrashRetention}
                disabled={!user || trashBusy === "settings"}
                className={BTN_PRIMARY}
              >
                <Trash2 className="h-3.5 w-3.5" />
                {trashBusy === "settings" ? "Saving" : "Save retention"}
              </button>
              <button
                type="button"
                onClick={purgeExpiredItems}
                disabled={!user || trashBusy === "expired" || trashRetentionDays < 0}
                className={BTN_SECONDARY}
              >
                <Eraser className="h-3.5 w-3.5" />
                {trashBusy === "expired" ? "Purging" : "Purge expired"}
              </button>
            </div>
          </SettingsCard>

          <SettingsCard
            icon={Boxes}
            title="Deleted models"
            description="Restore models or remove them permanently from storage."
          >
            <div className="divide-y divide-border">
              {!user ? (
                <p className="p-4 sm:p-5 text-sm text-muted-foreground">
                  Sign in to manage the trash.
                </p>
              ) : trashLoading ? (
                <p className="p-4 sm:p-5 text-sm text-muted-foreground">
                  Loading...
                </p>
              ) : trashItems.length === 0 ? (
                <p className="p-4 sm:p-5 text-sm text-muted-foreground">
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
                        className={BTN_SECONDARY}
                      >
                        <RotateCcw className="h-3.5 w-3.5" />
                        Restore
                      </button>
                      <button
                        type="button"
                        onClick={() => purgeTrashItem(item.id)}
                        disabled={trashBusy !== null}
                        className="inline-flex items-center gap-1.5 px-3 py-2 rounded border border-red-500/30 text-red-500 hover:bg-red-500/10 transition-colors text-xs font-medium uppercase tracking-wider disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                        Delete
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>
          </SettingsCard>
        </div>
      )}

      {activeSection === "about" && (
        <div className="space-y-6">
          {/* App identity */}
          <div className="bg-card border border-border rounded">
            <div className="px-4 sm:px-6 py-5 flex flex-col sm:flex-row sm:items-center gap-4">
              <div className="flex h-14 w-14 flex-shrink-0 items-center justify-center rounded-xl bg-[var(--primary)] text-[var(--primary-foreground)]">
                <svg viewBox="0 0 24 24" className="h-7 w-7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4" />
                </svg>
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <h3 className="text-lg font-bold text-foreground tracking-tight">PrintStash</h3>
                  <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-semibold text-muted-foreground">
                    v{health?.version ?? "0.2.0"}
                  </span>
                </div>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Self-hosted asset management for 3D printing workflows.
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <a
                  href={`https://github.com/${GITHUB_REPO}`}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="inline-flex items-center gap-1.5 rounded border border-border bg-background px-3 py-2 text-xs font-medium text-foreground hover:bg-muted transition-colors"
                >
                  <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="currentColor" aria-hidden>
                    <path d="M12 .5C5.7.5.5 5.7.5 12c0 5.1 3.3 9.4 7.9 10.9.6.1.8-.3.8-.6v-2c-3.2.7-3.9-1.5-3.9-1.5-.5-1.3-1.3-1.7-1.3-1.7-1.1-.7.1-.7.1-.7 1.2.1 1.8 1.2 1.8 1.2 1 1.8 2.8 1.3 3.5 1 .1-.8.4-1.3.8-1.6-2.6-.3-5.3-1.3-5.3-5.8 0-1.3.5-2.3 1.2-3.1-.1-.3-.5-1.5.1-3.1 0 0 1-.3 3.3 1.2a11.5 11.5 0 0 1 6 0C17 4.6 18 4.9 18 4.9c.6 1.6.2 2.8.1 3.1.8.8 1.2 1.8 1.2 3.1 0 4.5-2.7 5.5-5.3 5.8.4.4.8 1.1.8 2.2v3.3c0 .3.2.7.8.6 4.6-1.5 7.9-5.8 7.9-10.9C23.5 5.7 18.3.5 12 .5z" />
                  </svg>
                  GitHub
                </a>
                <a
                  href={`https://github.com/${GITHUB_REPO}/stargazers`}
                  target="_blank"
                  rel="noreferrer noopener"
                  title="Star on GitHub"
                >
                  {/* remote shields.io badge */}
                  <img
                    src={`https://img.shields.io/github/stars/${GITHUB_REPO}?style=flat&logo=github&label=Stars&color=2563eb`}
                    alt="GitHub stars"
                    className="h-[22px]"
                  />
                </a>
              </div>
            </div>
          </div>

          {/* Changelog */}
          <SettingsCard
            icon={Info}
            title="Version history"
            description="What changed in each release"
          >
            <div className="divide-y divide-border">
              {CHANGELOG.map((release) => (
                <div key={release.version} className="px-4 sm:px-6 py-5 grid grid-cols-1 sm:grid-cols-[8rem_1fr] gap-3">
                  <div className="flex items-start gap-2">
                    <span className="rounded bg-[var(--primary)]/10 px-2 py-0.5 text-xs font-semibold text-[var(--primary)]">
                      v{release.version}
                    </span>
                    <span className="text-[11px] text-muted-foreground pt-0.5">{release.date}</span>
                  </div>
                  <ul className="space-y-1.5">
                    {release.changes.map((change, i) => (
                      <li key={i} className="flex gap-2 text-xs text-muted-foreground">
                        <span className="mt-1.5 h-1 w-1 flex-shrink-0 rounded-full bg-[var(--primary)]" />
                        <span>{change}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </SettingsCard>
        </div>
      )}
    </div>
  );
}
