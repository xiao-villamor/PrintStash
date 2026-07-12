"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Bell,
  Boxes,
  Check,
  Database,
  Download,
  Eraser,
  Eye,
  EyeOff,
  Files,
  FolderSync,
  Coins,
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
import { PageHeader } from "@/components/ui/page-header";
import { buttonVariants } from "@/components/ui/button";
import { TabBar } from "@/components/ui/tabs";
import { inputClasses } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { CURRENCY_OPTIONS } from "@/lib/currency";
import { ExternalLibrariesPanel } from "@/components/external-libraries-panel";
import { StorageConfigCard } from "@/components/storage-config-card";
import { MakerWorldConnectCard } from "@/components/makerworld-connect-card";
import { NotificationsPanel } from "@/components/notifications-panel";
import { SpoolmanConnectCard } from "@/components/spoolman-connect-card";
import { BrandMark } from "@/components/brand-mark";
import {
  createApiKey,
  createAdminUser,
  createBackup,
  deactivateAdminUser,
  deleteCollectionPermission,
  downloadBackup,
  downloadModelExport,
  getVaultConfig,
  listBackups,
  listCollectionPermissions,
  listCollections,
  listApiKeys,
  listAdminUsers,
  listTrash,
  purgeExpiredTrash,
  purgeModel,
  resetAdminUserPassword,
  restoreBackup,
  restoreModel,
  revokeApiKey,
  updateCollectionPermission,
  updateAdminUser,
  updateVaultConfig,
} from "@/lib/api";
import type { BackupMeta } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useVaultStats } from "@/lib/queries";
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
import {
  readPrinterCardImagePreference,
  writePrinterCardImagePreference,
} from "@/lib/printer-card-display";
import { CHANGELOG, GITHUB_REPO } from "@/lib/changelog";
import type {
  ApiKeyRead,
  CollectionPermissionRead,
  CollectionRead,
  CollectionRole,
  TrashedModelRead,
  UserRead,
} from "@/types";

interface HealthResponse {
  status: string;
  name: string;
  version: string;
}

type SettingsSection =
  | "overview"
  | "access"
  | "storage"
  | "imports"
  | "libraries"
  | "notifications"
  | "spoolman"
  | "design"
  | "trash"
  | "about";

const SETTINGS_SECTIONS: {
  id: SettingsSection;
  label: string;
  icon: typeof Server;
}[] = [
  { id: "overview", label: "Overview", icon: Server },
  { id: "access", label: "Users & Access", icon: Users },
  { id: "storage", label: "Storage", icon: HardDrive },
  { id: "imports", label: "Imports", icon: Download },
  { id: "libraries", label: "Shared volumes", icon: FolderSync },
  { id: "notifications", label: "Notifications", icon: Bell },
  { id: "spoolman", label: "Spoolman", icon: Boxes },
  { id: "design", label: "Design", icon: Palette },
  { id: "trash", label: "Trash", icon: Trash2 },
  { id: "about", label: "About", icon: Info },
];

// Shared button styles — keep settings actions visually uniform and theme-aware.
const BTN_PRIMARY = cn(buttonVariants({ size: "xs" }), "uppercase tracking-wider");
const BTN_SECONDARY = cn(
  buttonVariants({ variant: "outline", size: "xs" }),
  "uppercase tracking-wider text-muted-foreground",
);
const BTN_ICON = buttonVariants({ variant: "outline", size: "icon-sm" });
const INPUT = cn(inputClasses, "h-auto py-2 rounded");

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

function shellQuote(value: string): string {
  return `'${value.replace(/'/g, "'\\''")}'`;
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
    <div className={cn("overflow-hidden rounded-lg border border-border bg-card text-card-foreground shadow-sm", className)}>
      <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-4 sm:px-5">
        <div className="flex items-start gap-3 min-w-0">
          {Icon && (
            <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
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
  const latestRelease = CHANGELOG[0];
  const [activeSection, setActiveSection] = useState<SettingsSection>("overview");
  const [health, setHealth] = useState<HealthResponse | null>(null);
  // Vault totals refresh automatically when models change (model writes
  // invalidate queryKeys.vaultStats), so no manual refetch on this screen.
  const stats = useVaultStats().data ?? null;
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
  const [autoMarkKnownGood, setAutoMarkKnownGood] = useState(true);
  const [autoMarkBusy, setAutoMarkBusy] = useState(false);
  const [currency, setCurrency] = useState("USD");
  const [currencyBusy, setCurrencyBusy] = useState(false);
  const [purgeTarget, setPurgeTarget] = useState<number | null>(null);
  const [backingUp, setBackingUp] = useState(false);
  const [backups, setBackups] = useState<BackupMeta[]>([]);
  const [backupsLoading, setBackupsLoading] = useState(false);
  const [restoreTarget, setRestoreTarget] = useState<BackupMeta | null>(null);
  const [restoringBackup, setRestoringBackup] = useState(false);
  const [downloadingBackup, setDownloadingBackup] = useState<string | null>(null);
  const [metadataPrefs, setMetadataPrefs] = useState<MetadataPreferences>(
    DEFAULT_METADATA_PREFERENCES,
  );
  const [cardMetrics, setCardMetrics] = useState<CardMetrics>(DEFAULT_CARD_METRICS);
  const [showPrinterCardImage, setShowPrinterCardImage] = useState(false);
  const [printerImageWarningOpen, setPrinterImageWarningOpen] = useState(false);

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
    setMetadataPrefs(readMetadataPreferences());
    setCardMetrics(readCardMetrics());
    setShowPrinterCardImage(readPrinterCardImagePreference());
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

  const loadBackups = useCallback(async () => {
    if (!user?.is_superuser) {
      setBackups([]);
      return;
    }
    setBackupsLoading(true);
    try {
      setBackups(await listBackups());
    } catch (e) {
      toast.error(e);
    } finally {
      setBackupsLoading(false);
    }
  }, [user]);

  useEffect(() => {
    if (activeSection === "storage") {
      loadBackups();
    }
  }, [activeSection, loadBackups]);

  useEffect(() => {
    if (activeSection !== "design" || !user) return;
    let cancelled = false;
    getVaultConfig()
      .then((cfg) => {
        if (!cancelled) {
          setAutoMarkKnownGood(cfg.auto_mark_known_good ?? true);
          setCurrency(cfg.currency ?? "USD");
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [activeSection, user]);

  async function saveAutoMarkKnownGood(next: boolean) {
    setAutoMarkKnownGood(next);
    setAutoMarkBusy(true);
    try {
      await updateVaultConfig({ auto_mark_known_good: next });
      toast.success(
        next ? "Auto-mark known good enabled." : "Auto-mark known good disabled.",
      );
    } catch (e) {
      setAutoMarkKnownGood(!next);
      toast.error(e);
    } finally {
      setAutoMarkBusy(false);
    }
  }

  async function saveCurrency(next: string) {
    const prev = currency;
    setCurrency(next);
    setCurrencyBusy(true);
    try {
      await updateVaultConfig({ currency: next });
      toast.success(`Currency set to ${next}.`);
    } catch (e) {
      setCurrency(prev);
      toast.error(e);
    } finally {
      setCurrencyBusy(false);
    }
  }

  async function handleBackupNow() {
    setBackingUp(true);
    try {
      const meta = await createBackup();
      const mb = (meta.size_bytes / 1024 / 1024).toFixed(1);
      setBackups((current) => [
        meta,
        ...current.filter((item) => item.backup_id !== meta.backup_id),
      ]);
      toast.success(`Backup created — ${meta.file_count} files, ${mb} MB`);
    } catch (e) {
      toast.error(e);
    } finally {
      setBackingUp(false);
    }
  }

  async function confirmRestoreBackup() {
    if (!restoreTarget) return;
    const target = restoreTarget;
    setRestoringBackup(true);
    try {
      const result = await restoreBackup(target.backup_id);
      toast.success(`Backup restored — ${result.restored_files} files`);
      setRestoreTarget(null);
      window.setTimeout(() => window.location.reload(), 800);
    } catch (e) {
      toast.error(e);
    } finally {
      setRestoringBackup(false);
    }
  }

  async function handleDownloadBackup(backupId: string) {
    setDownloadingBackup(backupId);
    try {
      await downloadBackup(backupId);
      toast.success("Backup download started.");
    } catch (e) {
      toast.error(e);
    } finally {
      setDownloadingBackup(null);
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

  async function copyOrcaCommand() {
    if (!newApiKey || !user) return;
    const baseUrl = window.location.origin;
    const command = [
      "/usr/bin/python3",
      "/path/to/printstash_orca_push.py",
      "--url",
      shellQuote(baseUrl),
      "--username",
      shellQuote(user.username),
      "--api-key",
      shellQuote(newApiKey),
    ].join(" ");
    await navigator.clipboard.writeText(command);
    toast.success("OrcaSlicer command copied.");
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

  function setAllMetadataPreferences(visible: boolean) {
    const next = Object.fromEntries(
      METADATA_FIELDS.map((field) => [field.id, visible]),
    ) as MetadataPreferences;
    setMetadataPrefs(next);
    writeMetadataPreferences(next);
  }

  function updateCardMetric(slot: 0 | 1 | 2, id: CardMetricId) {
    const next: CardMetrics = [...cardMetrics] as CardMetrics;
    next[slot] = id;
    setCardMetrics(next);
    writeCardMetrics(next);
    // Notify other components in this tab. Carry newValue: a storage-sync
    // listener (e.g. dev tools) treats a null newValue as a deletion and would
    // wipe the key we just wrote.
    window.dispatchEvent(
      new StorageEvent("storage", {
        key: "printstash.card.metrics",
        newValue: JSON.stringify(next),
      }),
    );
  }

  function resetCardMetrics() {
    setCardMetrics(DEFAULT_CARD_METRICS);
    writeCardMetrics(DEFAULT_CARD_METRICS);
    window.dispatchEvent(
      new StorageEvent("storage", {
        key: "printstash.card.metrics",
        newValue: JSON.stringify(DEFAULT_CARD_METRICS),
      }),
    );
    toast.success("Card metrics reset.");
  }

  function updatePrinterCardImagePreference(next: boolean) {
    setShowPrinterCardImage(next);
    writePrinterCardImagePreference(next);
    toast.success(next ? "Printer card images enabled." : "Printer card images hidden.");
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
      <ConfirmModal
        open={restoreTarget !== null}
        onClose={() => setRestoreTarget(null)}
        onConfirm={confirmRestoreBackup}
        busy={restoringBackup}
        title="Restore backup?"
        description="This replaces the current database and stored files with the selected backup."
        confirmLabel="Restore"
      />
      <ConfirmModal
        open={printerImageWarningOpen}
        onClose={() => setPrinterImageWarningOpen(false)}
        onConfirm={() => {
          updatePrinterCardImagePreference(true);
          setPrinterImageWarningOpen(false);
        }}
        title="Download third-party printer images?"
        description="Printer artwork will load from OrcaSlicer's GitHub repository. Images may be copyrighted or trademarked by their creators or printer manufacturers and remain subject to their original licenses. PrintStash does not own or redistribute them. Continue only if this use is permitted where you live."
        confirmLabel="Download & enable"
      />

      <PageHeader title="Settings" description="Vault configuration and display preferences" />

      <div className="border-b border-border pb-3 lg:hidden">
        <TabBar
          tabs={SETTINGS_SECTIONS.map((section) => {
            const Icon = section.icon;
            return {
              key: section.id,
              label: (
                <>
                  <Icon className="h-4 w-4" />
                  {section.label}
                </>
              ),
            };
          })}
          active={activeSection}
          onChange={setActiveSection}
          className="gap-1 overflow-x-auto"
          tabClassName="inline-flex shrink-0 items-center gap-2 whitespace-nowrap rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-[color,background-color,transform] duration-press active:scale-[0.99] hover:bg-popover-hover hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
          activeTabClassName="bg-accent text-accent-foreground"
          showIndicator={false}
        />
      </div>

      <div className="lg:grid lg:grid-cols-[13rem_minmax(0,1fr)] lg:items-start lg:gap-6">
        <nav aria-label="Settings sections" className="sticky top-0 hidden rounded-lg border border-border bg-card p-2 shadow-sm lg:block">
          {SETTINGS_SECTIONS.map((section) => {
            const Icon = section.icon;
            const isActive = section.id === activeSection;
            return (
              <button
                key={section.id}
                type="button"
                aria-current={isActive ? "page" : undefined}
                onClick={() => setActiveSection(section.id)}
                className={cn(
                  "flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left text-sm font-medium transition-[color,background-color,transform] duration-press active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
                  isActive
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-popover-hover hover:text-foreground",
                )}
              >
                <Icon className="h-4 w-4 shrink-0" />
                <span>{section.label}</span>
              </button>
            );
          })}
        </nav>

        <main className="min-w-0">

      {activeSection === "overview" && (
        <div className="space-y-6 animate-panel-in">
          {/* KPI tiles */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {kpiItems.map((item) => {
              const Icon = item.icon;
              return (
                <div key={item.label} className="bg-card border border-border rounded p-4 sm:p-5">
                  <div className="flex items-center justify-between">
                    <p className="font-mono text-2xs uppercase tracking-wider text-muted-foreground">
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
        <div className="space-y-6 animate-panel-in">
          {user?.is_superuser && (
            <SettingsCard
              icon={Users}
              title="Users"
              description="Create users, assign vault admins, disable accounts, and reset passwords."
            >
              <div className="p-4 sm:p-5 space-y-4">
                <div className="grid gap-2 lg:grid-cols-[1fr_1fr_1fr_auto]">
                  <label className="block space-y-1">
                    <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">Username</span>
                    <input
                      id="new-user-username"
                      value={newUsername}
                      onChange={(event) => setNewUsername(event.target.value)}
                      className={INPUT}
                      maxLength={128}
                      autoComplete="username"
                    />
                  </label>
                  <label className="block space-y-1">
                    <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">Email</span>
                    <input
                      id="new-user-email"
                      value={newUserEmail}
                      onChange={(event) => setNewUserEmail(event.target.value)}
                      className={INPUT}
                      type="email"
                      maxLength={255}
                      autoComplete="email"
                    />
                  </label>
                  <label className="block space-y-1">
                    <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">Initial password</span>
                    <input
                      id="new-user-password"
                      value={newUserPassword}
                      onChange={(event) => setNewUserPassword(event.target.value)}
                      className={INPUT}
                      type="password"
                      minLength={8}
                      maxLength={256}
                      autoComplete="new-password"
                      aria-describedby="new-user-password-help"
                    />
                  </label>
                  <button
                    type="button"
                    onClick={createUser}
                    disabled={usersBusy === "create" || !newUsername.trim() || newUserPassword.trim().length < 8}
                    className={`${BTN_PRIMARY} self-end`}
                  >
                    <UserPlus className="h-3.5 w-3.5" />
                    Create
                  </button>
                </div>
                <p id="new-user-password-help" className="text-xs text-muted-foreground">
                  Initial password: at least 8 characters.
                </p>

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
                                <span className="inline-flex items-center gap-1 rounded bg-muted px-2 py-0.5 font-mono text-3xs uppercase text-muted-foreground">
                                  <ShieldCheck className="h-3 w-3" />
                                  Admin
                                </span>
                              )}
                              {!row.is_active && (
                                <span className="rounded bg-red-500/10 px-2 py-0.5 font-mono text-3xs uppercase text-red-600">
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
                  <label className="block space-y-1">
                    <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">User</span>
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
                  </label>
                  <label className="block space-y-1">
                    <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">Collection</span>
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
                  </label>
                  <label className="block space-y-1">
                    <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">Role</span>
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
                  </label>
                  <button
                    type="button"
                    onClick={saveCollectionAccess}
                    disabled={!accessUserId || !accessCollectionId || accessBusy === "save"}
                    className={`${BTN_PRIMARY} self-end`}
                  >
                    {accessBusy === "save" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
                    Grant
                  </button>
                </div>

                <div className="rounded border border-border overflow-hidden">
                  <div className="grid grid-cols-[1fr_auto_auto] gap-3 border-b border-border bg-muted/40 px-3 py-2 font-mono text-3xs uppercase tracking-wider text-muted-foreground">
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
                          <span className="rounded bg-muted px-2 py-1 font-mono text-3xs uppercase text-muted-foreground">
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
                    <label className="block space-y-1">
                      <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">Key name</span>
                      <input
                        id="api-key-name"
                        value={keyName}
                        onChange={(event) => setKeyName(event.target.value)}
                        className={INPUT}
                        maxLength={128}
                      />
                    </label>
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
                    <div className="border border-primary/40 bg-primary/10 rounded p-3 space-y-2">
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
                        <button
                          type="button"
                          onClick={copyOrcaCommand}
                          className={BTN_SECONDARY}
                          title="Copy OrcaSlicer post-processing command"
                        >
                          <Copy className="h-3.5 w-3.5" />
                          Orca command
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
                            <p className="font-mono text-2xs text-muted-foreground">
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
                      Use your username with this API key on <code className="font-mono">/api/v1/auth/login</code>. The hook exchanges it for a JWT Bearer token, then uploads with the normal <code className="font-mono">Authorization</code> header.
                    </p>
                  </div>
                </>
              )}
            </div>
          </SettingsCard>
        </div>
      )}

      {activeSection === "storage" && (
        <div className="space-y-6 animate-panel-in">
          <StorageConfigCard />
          <SettingsCard
            icon={HardDrive}
            title="Manual backup"
            description="Create a full backup of the database and all stored files right now."
            action={
              <button
                type="button"
                onClick={handleBackupNow}
                disabled={!user?.is_superuser || backingUp}
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
          <SettingsCard
            icon={RotateCcw}
            title="Restore backup"
            description="Recover the vault database and stored files from a previous backup."
            action={
              <button
                type="button"
                onClick={loadBackups}
                disabled={!user?.is_superuser || backupsLoading}
                className={BTN_ICON}
                title="Refresh backups"
              >
                <RefreshCw className={`h-4 w-4 ${backupsLoading ? "animate-spin" : ""}`} />
              </button>
            }
          >
            <div className="divide-y divide-border">
              {!user?.is_superuser ? (
                <p className="p-4 sm:p-5 text-sm text-muted-foreground">
                  Superuser access is required.
                </p>
              ) : backupsLoading ? (
                <p className="p-4 sm:p-5 text-sm text-muted-foreground">
                  Loading...
                </p>
              ) : backups.length === 0 ? (
                <p className="p-4 sm:p-5 text-sm text-muted-foreground">
                  No backups found.
                </p>
              ) : (
                backups.map((backup) => (
                  <div
                    key={backup.backup_id}
                    className="grid gap-3 p-4 sm:p-5 lg:grid-cols-[1fr_auto] lg:items-center"
                  >
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="truncate text-sm font-medium text-foreground">
                          {formatDate(backup.created_at)}
                        </p>
                        <span className="font-mono text-3xs uppercase tracking-wider px-2 py-0.5 rounded border border-border text-muted-foreground">
                          {backup.location}
                        </span>
                        <span className="font-mono text-3xs uppercase tracking-wider px-2 py-0.5 rounded border border-border text-muted-foreground">
                          v{backup.app_version}
                        </span>
                      </div>
                      <p className="mt-1 truncate font-mono text-2xs text-muted-foreground">
                        {backup.backup_id}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {backup.file_count} files · {formatBytes(backup.size_bytes)} · {backup.storage_backend}
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-2 lg:justify-end">
                      <button
                        type="button"
                        onClick={() => handleDownloadBackup(backup.backup_id)}
                        disabled={
                          downloadingBackup !== null ||
                          restoringBackup ||
                          backingUp
                        }
                        className={BTN_SECONDARY}
                      >
                        {downloadingBackup === backup.backup_id ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <Download className="h-3.5 w-3.5" />
                        )}
                        Download
                      </button>
                      <button
                        type="button"
                        onClick={() => setRestoreTarget(backup)}
                        disabled={
                          downloadingBackup !== null ||
                          restoringBackup ||
                          backingUp
                        }
                        className="inline-flex items-center gap-1.5 px-3 py-2 rounded border border-red-500/30 text-red-500 hover:bg-red-500/10 transition-colors text-xs font-medium uppercase tracking-wider disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        <RotateCcw className="h-3.5 w-3.5" />
                        Restore
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>
          </SettingsCard>
        </div>
      )}

      {activeSection === "imports" && (
        <div className="space-y-6 animate-panel-in">
          <MakerWorldConnectCard />
        </div>
      )}

      {activeSection === "libraries" && (
        <div className="space-y-6 animate-panel-in">
          <ExternalLibrariesPanel canEdit={!!user?.is_superuser} />
        </div>
      )}

      {activeSection === "notifications" && (
        <div className="space-y-6 animate-panel-in">
          <NotificationsPanel canEdit={!!user?.is_superuser} />
        </div>
      )}

      {activeSection === "spoolman" && (
        <div className="space-y-6 animate-panel-in">
          <SpoolmanConnectCard canEdit={!!user?.is_superuser} />
        </div>
      )}

      {activeSection === "design" && (
        <div className="space-y-6 animate-panel-in">
          <SettingsCard
            icon={Printer}
            title="Printer cards"
            description="Choose whether printer cards include a visual. Plain cards remain more compact and information-dense."
          >
            <div className="flex items-center justify-between gap-4 p-4 sm:p-5">
              <div className="flex min-w-0 items-center gap-3">
                <div className="hidden h-14 w-14 shrink-0 items-center justify-center rounded-md bg-muted sm:flex">
                  <img
                    src="/images/printers/generic-fdm.png"
                    alt=""
                    className="h-12 w-12 object-contain"
                  />
                </div>
                <div>
                  <p className="text-[13px] font-medium text-foreground">Show printer image</p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    Adds a brand-neutral printer visual above each card.
                  </p>
                </div>
              </div>
              <button
                type="button"
                role="switch"
                aria-label="Show printer image on printer cards"
                aria-checked={showPrinterCardImage}
                onClick={() => {
                  if (showPrinterCardImage) updatePrinterCardImagePreference(false);
                  else setPrinterImageWarningOpen(true);
                }}
                className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors ${
                  showPrinterCardImage ? "bg-primary" : "bg-outline-variant"
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 rounded-full bg-primary-foreground transition-transform ${
                    showPrinterCardImage ? "translate-x-6" : "translate-x-1"
                  }`}
                />
              </button>
            </div>
          </SettingsCard>

          {/* Print tracking behaviour */}
          <SettingsCard
            icon={Printer}
            title="Print tracking"
            description="Automatically promote a revision to known-good after its first successful print. A manual failed/archived verdict is never overridden."
          >
            <div className="p-4 sm:p-5 flex items-center justify-between gap-4">
              <span className="text-[13px] text-foreground">
                Auto-mark known good on successful print
              </span>
              <button
                type="button"
                role="switch"
                aria-label="Auto-mark known good on successful print"
                aria-checked={autoMarkKnownGood}
                disabled={!user || autoMarkBusy}
                onClick={() => saveAutoMarkKnownGood(!autoMarkKnownGood)}
                className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-50 ${
                  autoMarkKnownGood
                    ? "bg-primary"
                    : "bg-outline-variant"
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                    autoMarkKnownGood ? "translate-x-6" : "translate-x-1"
                  }`}
                />
              </button>
            </div>
          </SettingsCard>

          {/* Currency for cost tracking */}
          <SettingsCard
            icon={Coins}
            title="Currency"
            description="Currency used to display cost figures in statistics and filament pricing."
          >
            <div className="p-4 sm:p-5 flex items-center justify-between gap-4">
              <label htmlFor="display-currency" className="text-[13px] text-foreground">Display currency</label>
              <select
                id="display-currency"
                value={currency}
                onChange={(event) => saveCurrency(event.target.value)}
                disabled={!user || currencyBusy}
                className={`${INPUT} max-w-xs`}
              >
                {CURRENCY_OPTIONS.map((opt) => (
                  <option key={opt.code} value={opt.code}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>
          </SettingsCard>

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
                <div key={slot} className="space-y-2">
                  <p className="text-2xs font-mono uppercase tracking-wider text-primary">
                    Slot {slot + 1}
                  </p>
                  <div className="grid grid-cols-1 gap-1">
                    {CARD_METRIC_OPTIONS.map((opt) => {
                      const isSelected = cardMetrics[slot] === opt.id;
                      const otherSlot = cardMetrics.findIndex(
                        (id, i) => i !== slot && id === opt.id,
                      );
                      const usedInOther = otherSlot !== -1;
                      return (
                        <button
                          key={opt.id}
                          type="button"
                          disabled={usedInOther}
                          aria-pressed={isSelected}
                          onClick={() => updateCardMetric(slot, opt.id as CardMetricId)}
                          className={`group flex items-center gap-2 px-3 py-2 rounded border text-sm transition-colors ${
                            isSelected
                              ? "border-transparent bg-accent text-accent-foreground"
                              : usedInOther
                              ? "border-dashed border-border bg-transparent text-muted-foreground/50 cursor-not-allowed"
                              : "border-border bg-background text-foreground hover:border-primary/50 hover:bg-muted"
                          }`}
                        >
                          <span
                            className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-full border transition-colors ${
                              isSelected
                                ? "border-accent-foreground bg-accent-foreground text-accent"
                                : "border-border text-transparent"
                            }`}
                          >
                            <Check className="h-3 w-3" strokeWidth={3} />
                          </span>
                          <span className="flex-1 text-left">{opt.label}</span>
                          {usedInOther ? (
                            <span className="font-mono text-3xs uppercase tracking-wider text-muted-foreground/60">
                              Slot {otherSlot + 1}
                            </span>
                          ) : (
                            <span
                              className={`font-mono text-3xs uppercase tracking-wider ${
                                isSelected
                                  ? "text-accent-foreground/80"
                                  : "text-muted-foreground"
                              }`}
                            >
                              {opt.abbr}
                            </span>
                          )}
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
            <div className="p-4 sm:p-5 space-y-3">
              <div className="flex items-center justify-between gap-3">
                <p className="text-2xs font-mono uppercase tracking-wider text-muted-foreground">
                  {METADATA_FIELDS.filter((f) => metadataPrefs[f.id]).length} of{" "}
                  {METADATA_FIELDS.length} shown
                </p>
                <div className="flex items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() => setAllMetadataPreferences(true)}
                    className="font-mono text-3xs uppercase tracking-wider text-muted-foreground hover:text-primary transition-colors"
                  >
                    Show all
                  </button>
                  <span className="text-muted-foreground/40">·</span>
                  <button
                    type="button"
                    onClick={() => setAllMetadataPreferences(false)}
                    className="font-mono text-3xs uppercase tracking-wider text-muted-foreground hover:text-primary transition-colors"
                  >
                    Hide all
                  </button>
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                {METADATA_FIELDS.map((field) => {
                  const visible = metadataPrefs[field.id];
                  return (
                    <button
                      key={field.id}
                      type="button"
                      aria-pressed={visible}
                      onClick={() => updateMetadataPreference(field.id, !visible)}
                      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm transition-colors ${
                        visible
                          ? "border-transparent bg-accent text-accent-foreground hover:bg-accent"
                          : "border-dashed border-border bg-transparent text-muted-foreground/60 hover:border-border hover:text-foreground"
                      }`}
                    >
                      {visible ? (
                        <Eye className="h-3.5 w-3.5" />
                      ) : (
                        <EyeOff className="h-3.5 w-3.5" />
                      )}
                      {field.label}
                    </button>
                  );
                })}
              </div>
            </div>
          </SettingsCard>
        </div>
      )}

      {activeSection === "trash" && (
        <div className="space-y-6 animate-panel-in">
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
                <span className="block text-2xs text-muted-foreground mb-1">
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
                        <span className="font-mono text-3xs uppercase tracking-wider px-2 py-0.5 rounded border border-border text-muted-foreground">
                          {item.file_count} files
                        </span>
                        <span className="font-mono text-3xs uppercase tracking-wider px-2 py-0.5 rounded border border-border text-muted-foreground">
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
        <div className="space-y-6 animate-panel-in">
          {/* App identity */}
          <div className="bg-card border border-border rounded">
            <div className="px-4 sm:px-6 py-5 flex flex-col sm:flex-row sm:items-center gap-4">
              <div className="flex h-14 w-14 flex-shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground">
                <BrandMark className="h-10 w-10" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <h3 className="text-lg font-bold text-foreground tracking-tight">PrintStash</h3>
                  <span className="rounded-full bg-muted px-2 py-0.5 text-3xs font-semibold text-muted-foreground">
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
              </div>
            </div>
          </div>

          {/* Changelog */}
          <SettingsCard
            icon={Info}
            title="Latest changes"
            description="What changed in the current release"
          >
            <div className="divide-y divide-border">
              {latestRelease && (
                <div className="px-4 sm:px-6 py-5 grid grid-cols-1 sm:grid-cols-[8rem_1fr] gap-3">
                  <div className="flex items-start gap-2">
                    <span className="rounded bg-primary/10 px-2 py-0.5 text-xs font-semibold text-primary">
                      v{latestRelease.version}
                    </span>
                    <span className="text-2xs text-muted-foreground pt-0.5">{latestRelease.date}</span>
                  </div>
                  <ul className="space-y-1.5">
                    {latestRelease.changes.map((change, i) => (
                      <li key={i} className="flex gap-2 text-xs text-muted-foreground">
                        <span className="mt-1.5 h-1 w-1 flex-shrink-0 rounded-full bg-primary" />
                        <span>{change}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </SettingsCard>
        </div>
      )}
        </main>
      </div>
    </div>
  );
}
