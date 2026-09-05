"use client";

import { useCallback, useEffect, useState } from "react";
import { BackupRunHistory } from "@/components/backup-run-history";
import {
  Bell,
  Boxes,
  Check,
  CircleArrowUp,
  Clock,
  Cloud,
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
  HeartPulse,
  Info,
  Images,
  KeyRound,
  Copy,
  Loader2,
  Palette,
  Printer,
  Puzzle,
  ShieldCheck,
  RefreshCw,
  RotateCcw,
  Trash2,
  Server,
  Tag,
  UserPlus,
  Users,
  Upload,
} from "lucide-react";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { PageHeader } from "@/components/ui/page-header";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { TabBar } from "@/components/ui/tabs";
import { inputClasses } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Localized, translateUiText } from "@/components/ui/localized";
import { cn } from "@/lib/utils";
import { useRouter, useSearchParams } from "@/lib/navigation";
import { CURRENCY_OPTIONS } from "@/lib/currency";
import { ExternalLibrariesPanel } from "@/components/external-libraries-panel";
import { StorageConfigCard } from "@/components/storage-config-card";
import { RemoteStorageConnections } from "@/components/remote-storage-connections";
import { MakerWorldConnectCard } from "@/components/makerworld-connect-card";
import { ProviderConnectionsPanel } from "@/components/provider-connections-panel";
import { NotificationsPanel } from "@/components/notifications-panel";
import { SpoolmanConnectCard } from "@/components/spoolman-connect-card";
import { OidcSettingsCard } from "@/components/oidc-settings-card";
import { MaintenancePanel } from "@/components/maintenance-panel";
import { BrandMark } from "@/components/brand-mark";
import {
  createApiKey,
  createAdminUser,
  createBackup,
  createGcPlan,
  approveGcPlan,
  abortGcPlan,
  finalizeGcPlan,
  adoptLocalBackup,
  adoptRemoteBackup,
  adoptS3Backup,
  deactivateAdminUser,
  deleteBackup,
  deleteCollectionPermission,
  deletePrinterPermission,
  downloadBackup,
  downloadModelExport,
  downloadLibraryArchive,
  importLibraryArchive,
  rebuildModelThumbnails,
  getHealthDetails,
  getActiveGcPlan,
  getLatestRelease,
  getVaultConfig,
  listBackupSources,
  listUnownedS3Backups,
  listUnownedLocalBackups,
  listUnownedRemoteBackups,
  listCollectionPermissions,
  listCollections,
  listPrinterPermissions,
  listPrinters,
  listApiKeys,
  listAdminUsers,
  listTrash,
  listStorageConnections,
  purgeModel,
  resetAdminUserPassword,
  restoreBackup,
  restoreModel,
  restartPrintStash,
  revokeApiKey,
  updateCollectionPermission,
  updatePrinterPermission,
  updateAdminUser,
  updateVaultConfig,
  updateStorageConnection,
  uploadBackup,
} from "@/lib/api";
import type {
  BackupMeta,
  GcPlan,
  ReleaseStatus,
  UnownedBackupCandidate,
  UnownedRemoteBackupCandidate,
  UnownedS3BackupCandidate,
} from "@/lib/api";
import type { StorageConnection } from "@/types";
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
import { useI18n, type MessageKey } from "@/lib/i18n";
import { storageOperationMessage } from "@/lib/storage-operations";
import {
  usePrinterCardImagePreference,
  writePrinterCardImagePreference,
} from "@/lib/printer-card-display";
import { CHANGELOG, GITHUB_REPO } from "@/lib/changelog";
import {
  readPreviewPreferences,
  writePreviewPreferences,
  type PreviewPreferences,
  type PreviewQuality,
  type ScreenshotScale,
} from "@/lib/preview-preferences";
import { trackImportJob } from "@/lib/task-center";
import { prepareBrowserExtensionSetup } from "@/lib/browser-extension-setup";
import type {
  ApiKeyRead,
  CollectionPermissionRead,
  CollectionRead,
  CollectionRole,
  PrinterPermissionRead,
  PrinterRead,
  PrinterRole,
  StorageCleanupStatus,
  StorageHealthRead,
  StorageOperations,
  HealthResponse,
  TrashPurgeRead,
  TrashedModelRead,
  UserRead,
} from "@/types";

type SettingsSection =
  | "overview"
  | "access"
  | "storage"
  | "backup"
  | "remote-storage"
  | "imports"
  | "maintenance"
  | "libraries"
  | "notifications"
  | "sso"
  | "spoolman"
  | "design"
  | "previews"
  | "trash"
  | "about";

const SETTINGS_SECTIONS: {
  id: SettingsSection;
  labelKey: MessageKey;
  icon: typeof Server;
}[] = [
  { id: "overview", labelKey: "settings.overview", icon: Server },
  { id: "access", labelKey: "settings.access", icon: Users },
  { id: "storage", labelKey: "settings.storage", icon: HardDrive },
  { id: "backup", labelKey: "settings.backup", icon: Database },
  { id: "remote-storage", labelKey: "settings.remoteStorage", icon: Cloud },
  { id: "imports", labelKey: "settings.imports", icon: Download },
  { id: "maintenance", labelKey: "settings.maintenance", icon: HeartPulse },
  { id: "libraries", labelKey: "settings.libraries", icon: FolderSync },
  { id: "notifications", labelKey: "settings.notifications", icon: Bell },
  { id: "sso", labelKey: "settings.sso", icon: ShieldCheck },
  { id: "spoolman", labelKey: "settings.spoolman", icon: Boxes },
  { id: "design", labelKey: "settings.design", icon: Palette },
  { id: "previews", labelKey: "settings.previews", icon: Images },
  { id: "trash", labelKey: "settings.trash", icon: Trash2 },
  { id: "about", labelKey: "settings.about", icon: Info },
];

/** True when the `?section=` value names one of the sections we ship. */
function isSettingsSection(value: string | null): value is SettingsSection {
  return SETTINGS_SECTIONS.some((section) => section.id === value);
}

function settingsSection(value: string | null): SettingsSection {
  return isSettingsSection(value) ? value : "overview";
}

/**
 * Resolve a `<select>` value back to the literal union it came from. The DOM hands
 * back the option's value as a plain string, so matching it against the option list
 * recovers the domain type without asserting. The fallback is only reachable if the
 * rendered `<option>`s ever drift from the list passed here.
 */
function selectedOption<T extends number | string>(
  options: readonly [T, ...T[]],
  value: number | string,
): T {
  return options.find((option) => option === value) ?? options[0];
}

const COLLECTION_ROLES = ["view", "edit", "admin"] as const satisfies readonly CollectionRole[];
const PRINTER_ROLES = [
  "view",
  "print",
  "control",
  "admin",
] as const satisfies readonly PrinterRole[];
const PREVIEW_QUALITIES = [
  "performance",
  "balanced",
  "detail",
] as const satisfies readonly PreviewQuality[];
const SCREENSHOT_SCALES = [1, 2, 3] as const satisfies readonly ScreenshotScale[];
/** Widths the vault offers; a legacy config value outside this list shows as "Custom". */
const MODEL_THUMBNAIL_WIDTHS = [320, 640, 1280] as const;
type ModelThumbnailWidth = (typeof MODEL_THUMBNAIL_WIDTHS)[number];

/**
 * What the trash panel is busy with: the id of the single model being purged, or a
 * label for one of the bulk retention actions.
 */
type TrashOperation = number | "expired" | "settings" | "gc";

/** Only a per-model purge carries an id; the bulk actions carry their label instead. */
function isModelPurge(operation: TrashOperation | null): operation is number {
  return (
    operation !== null && operation !== "expired" && operation !== "settings" && operation !== "gc"
  );
}

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

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function backupSourceKey(backup: BackupMeta): string {
  return (
    backup.source_ref ??
    `${backup.location}:${backup.namespace ?? ""}:${backup.key ?? ""}:${backup.backup_id}`
  );
}

function parseBackupRetentionDays(value: string): number | null {
  if (!/^\d+$/.test(value)) return null;
  const days = Number(value);
  return days <= 365 ? days : null;
}

function backupSourceDescription(backup: BackupMeta, t: ReturnType<typeof useI18n>["t"]): string {
  const source = backup.source_ref ?? "legacy source identity";
  const namespace = backup.namespace ? ` · namespace ${backup.namespace}` : "";
  const hash = backup.archive_sha256 ? ` · SHA-256 ${backup.archive_sha256.slice(0, 16)}…` : "";
  return t("settings.backupExactSource", {
    source: `${backup.location} · ${source}${namespace}${hash}`,
  });
}

function restoreSourceDescription(backup: BackupMeta, t: ReturnType<typeof useI18n>["t"]): string {
  return `${t("settings.backupRestoreWarning")} ${backupSourceDescription(backup, t)}`;
}

function shortOpaque(value: string | null | undefined): string {
  return value ? `${value.slice(0, 16)}…` : "unavailable";
}

function shellQuote(value: string): string {
  return `'${value.replace(/'/g, "'\\''")}'`;
}

function storageHealthFrom(health: HealthResponse | null): StorageHealthRead | null {
  return health?.components?.storage ?? health?.storage ?? null;
}

function cleanupStatusMessage(t: ReturnType<typeof useI18n>["t"], result: TrashPurgeRead): string {
  const status: StorageCleanupStatus = result.storage_cleanup_status ?? "completed";
  const retained = (result.storage_pending ?? 0) + (result.storage_blocked ?? 0);
  switch (status) {
    case "pending":
      return t("settings.trashCleanupPending", { count: String(retained) });
    case "blocked":
      return t("settings.trashCleanupBlocked", { count: String(retained) });
    case "partial":
      return t("settings.trashCleanupPartial");
    default:
      return t("settings.trashCleanupCompleted");
  }
}

function useDeadlineReached(deadline: string | null): boolean {
  const [reachedDeadline, setReachedDeadline] = useState<string | null>(null);

  useEffect(() => {
    if (!deadline) return;
    let timer: number;
    const poll = () => {
      const remaining = Date.parse(deadline) - Date.now();
      if (remaining <= 0) {
        setReachedDeadline(deadline);
        return;
      }
      timer = window.setTimeout(poll, Math.min(remaining, 60_000));
    };
    timer = window.setTimeout(poll, 0);
    return () => window.clearTimeout(timer);
  }, [deadline]);

  return deadline !== null && reachedDeadline === deadline;
}

// Consistent card shell used across every settings section.
function SettingsCard({
  icon: Icon,
  title,
  description,
  action,
  children,
  className,
  stackActionOnMobile = false,
}: {
  icon?: typeof Server;
  title: string;
  description?: string;
  action?: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
  stackActionOnMobile?: boolean;
}) {
  return (
    <div
      role="group"
      aria-label={title}
      className={cn(
        "overflow-hidden rounded-lg border border-border bg-card text-card-foreground shadow-sm",
        className,
      )}
    >
      <div
        className={cn(
          "flex items-start justify-between gap-3 border-b border-border px-4 py-4 sm:px-5",
          stackActionOnMobile && "flex-col sm:flex-row",
        )}
      >
        <div className="flex items-start gap-3 min-w-0">
          {Icon && (
            <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
              <Icon className="h-4 w-4" />
            </div>
          )}
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-foreground">{title}</h3>
            {description && <p className="text-xs text-muted-foreground mt-0.5">{description}</p>}
          </div>
        </div>
        {action && (
          <div className={cn("flex-shrink-0", stackActionOnMobile && "w-full sm:w-auto")}>
            {action}
          </div>
        )}
      </div>
      {children}
    </div>
  );
}

export function SettingsPanel() {
  const { user } = useAuth();
  const { locale, t } = useI18n();
  const router = useRouter();
  const searchParams = useSearchParams();
  const latestRelease = CHANGELOG[0];
  // `?section=` is the single source of truth for the open section, so it is read
  // during render; mirroring it into state needed an effect to re-sync on every
  // deep link, back button, and replace.
  const activeSection = settingsSection(searchParams.get("section"));
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [releaseStatus, setReleaseStatus] = useState<ReleaseStatus | null>(null);
  const [releaseChecking, setReleaseChecking] = useState(false);
  // Vault totals refresh automatically when models change (model writes
  // invalidate queryKeys.vaultStats), so no manual refetch on this screen.
  const stats = useVaultStats().data ?? null;
  const [exporting, setExporting] = useState<"json" | "csv" | null>(null);
  const [archiveBusy, setArchiveBusy] = useState<"export" | "import" | null>(null);
  const [loadedApiKeys, setApiKeys] = useState<ApiKeyRead[]>([]);
  // A signed-out visitor has no keys to list, so that is derived rather than cleared
  // from an effect on sign-out.
  const apiKeys = user ? loadedApiKeys : [];
  const [users, setUsers] = useState<UserRead[]>([]);
  const [usersBusy, setUsersBusy] = useState<number | "create" | null>(null);
  const [newUsername, setNewUsername] = useState("");
  const [newUserEmail, setNewUserEmail] = useState("");
  const [newUserPassword, setNewUserPassword] = useState("");
  const [passwordDrafts, setPasswordDrafts] = useState<Record<number, string>>({});
  const [accessCollections, setAccessCollections] = useState<CollectionRead[]>([]);
  const [collectionPermissions, setCollectionPermissions] = useState<CollectionPermissionRead[]>(
    [],
  );
  const [accessUserId, setAccessUserId] = useState<number | "">("");
  const [accessCollectionId, setAccessCollectionId] = useState<number | "">("");
  const [accessRole, setAccessRole] = useState<CollectionRole>("view");
  const [accessBusy, setAccessBusy] = useState<"load" | "save" | string | null>(null);
  const [accessPrinters, setAccessPrinters] = useState<PrinterRead[]>([]);
  const [printerPermissions, setPrinterPermissions] = useState<PrinterPermissionRead[]>([]);
  const [printerAccessUserId, setPrinterAccessUserId] = useState<number | "">("");
  const [accessPrinterId, setAccessPrinterId] = useState<number | "">("");
  const [printerAccessRole, setPrinterAccessRole] = useState<PrinterRole>("view");
  const [printerAccessBusy, setPrinterAccessBusy] = useState<"load" | "save" | string | null>(null);
  const [mintedApiKey, setNewApiKey] = useState<string | null>(null);
  // Likewise the one-time secret: signing out hides it without a reset effect.
  const newApiKey = user ? mintedApiKey : null;
  const [keyName, setKeyName] = useState("Programmatic access");
  const [keyBusy, setKeyBusy] = useState(false);
  const [extensionSetupReady, setExtensionSetupReady] = useState(false);
  const [trashItems, setTrashItems] = useState<TrashedModelRead[]>([]);
  const [trashLoading, setTrashLoading] = useState(false);
  const [trashPurgeResult, setTrashPurgeResult] = useState<TrashPurgeRead | null>(null);
  const [gcPlan, setGcPlan] = useState<GcPlan | null>(null);
  const gcQuarantineReady = useDeadlineReached(
    gcPlan?.state === "quarantined" ? (gcPlan.quarantine_until ?? null) : null,
  );
  const [gcDigestConfirmation, setGcDigestConfirmation] = useState("");
  const [trashBusy, setTrashBusy] = useState<TrashOperation | null>(null);
  const [trashRetentionDays, setTrashRetentionDays] = useState(30);
  const [autoMarkKnownGood, setAutoMarkKnownGood] = useState(true);
  const [autoMarkBusy, setAutoMarkBusy] = useState(false);
  const [currency, setCurrency] = useState("USD");
  const [currencyBusy, setCurrencyBusy] = useState(false);
  // Each reader falls back to its defaults when there is no `window`, so these are
  // safe as lazy initialisers on the server as well as in the browser.
  const [previewPreferences, setPreviewPreferences] = useState(readPreviewPreferences);
  const [modelThumbnailWidth, setModelThumbnailWidth] = useState(640);
  const [previewBusy, setPreviewBusy] = useState<"quality" | "rebuild" | null>(null);
  const [purgeTarget, setPurgeTarget] = useState<number | null>(null);
  const [purgeExpiredOpen, setPurgeExpiredOpen] = useState(false);
  const [trashStorageTier, setTrashStorageTier] = useState("verified");
  const [trashOperations, setTrashOperations] = useState<StorageOperations>();
  const [backingUp, setBackingUp] = useState(false);
  const [backupRunRefresh, setBackupRunRefresh] = useState(0);
  const [backups, setBackups] = useState<BackupMeta[]>([]);
  const [unownedBackups, setUnownedBackups] = useState<UnownedBackupCandidate[]>([]);
  const [unownedS3Backups, setUnownedS3Backups] = useState<UnownedS3BackupCandidate[]>([]);
  const [unownedRemoteBackups, setUnownedRemoteBackups] = useState<UnownedRemoteBackupCandidate[]>(
    [],
  );
  const [backupsLoading, setBackupsLoading] = useState(false);
  const [backupRetentionDays, setBackupRetentionDays] = useState("30");
  const parsedBackupRetentionDays = parseBackupRetentionDays(backupRetentionDays);
  const [backupRetentionBusy, setBackupRetentionBusy] = useState(false);
  const [backupConnections, setBackupConnections] = useState<StorageConnection[]>([]);
  const [automaticBackupsEnabled, setAutomaticBackupsEnabled] = useState(false);
  const [automaticBackupTimeUtc, setAutomaticBackupTimeUtc] = useState("02:00");
  const [manualLocalBackupEnabled, setManualLocalBackupEnabled] = useState(true);
  const [automaticLocalBackupEnabled, setAutomaticLocalBackupEnabled] = useState(true);
  const [backupPolicyBusy, setBackupPolicyBusy] = useState(false);
  const [restoreTarget, setRestoreTarget] = useState<BackupMeta | null>(null);
  const [deleteBackupTarget, setDeleteBackupTarget] = useState<BackupMeta | null>(null);
  const [adoptTarget, setAdoptTarget] = useState<UnownedBackupCandidate | null>(null);
  const [adoptS3Target, setAdoptS3Target] = useState<UnownedS3BackupCandidate | null>(null);
  const [adoptRemoteTarget, setAdoptRemoteTarget] = useState<UnownedRemoteBackupCandidate | null>(
    null,
  );
  const [adoptingBackup, setAdoptingBackup] = useState(false);
  const [adoptingS3Backup, setAdoptingS3Backup] = useState(false);
  const [adoptingRemoteBackup, setAdoptingRemoteBackup] = useState(false);
  const [uploadingBackup, setUploadingBackup] = useState(false);
  const [restoringBackup, setRestoringBackup] = useState(false);
  const [deletingBackup, setDeletingBackup] = useState<string | null>(null);

  const [downloadingBackup, setDownloadingBackup] = useState<string | null>(null);
  const [metadataPrefs, setMetadataPrefs] = useState(readMetadataPreferences);
  const [cardMetrics, setCardMetrics] = useState(readCardMetrics);
  const showPrinterCardImage = usePrinterCardImagePreference();
  const [printerImageWarningOpen, setPrinterImageWarningOpen] = useState(false);
  const [restartConfirmOpen, setRestartConfirmOpen] = useState(false);
  const [restartBusy, setRestartBusy] = useState(false);
  const visibleSettingsSections = SETTINGS_SECTIONS.filter(
    (section) => !["sso", "maintenance"].includes(section.id) || user?.is_superuser,
  );

  function changeSection(section: SettingsSection) {
    const params = new URLSearchParams(searchParams.toString());
    if (section === "overview") params.delete("section");
    else params.set("section", section);
    const query = params.toString();
    router.replace(query ? `/settings?${query}` : "/settings", { scroll: false });
  }

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

  const refreshPrinterAccess = useCallback(async () => {
    if (!user?.is_superuser) return;
    setPrinterAccessBusy("load");
    try {
      const printers = await listPrinters(undefined, { fresh: true });
      const permissionGroups = await Promise.all(
        printers.map((printer) => listPrinterPermissions(printer.id)),
      );
      setAccessPrinters(printers);
      setPrinterPermissions(permissionGroups.flat());
    } catch (e) {
      toast.error(e);
    } finally {
      setPrinterAccessBusy(null);
    }
  }, [user]);

  useEffect(() => {
    if (!user?.is_superuser) return;
    getHealthDetails<HealthResponse>()
      .then(setHealth)
      .catch(() => {});
  }, [user]);

  const storageHealth = storageHealthFrom(health);

  const checkForUpdates = useCallback(
    async (refresh = false) => {
      if (!user?.is_superuser) return;
      setReleaseChecking(true);
      try {
        setReleaseStatus(await getLatestRelease(refresh));
      } catch {
        setReleaseStatus(null);
      } finally {
        setReleaseChecking(false);
      }
    },
    [user],
  );

  useEffect(() => {
    // `checkForUpdates` awaits the release feed; the only synchronous write is the flag
    // that says a request is in flight, which is what this effect exists to start.
    // oxlint-disable-next-line react/set-state-in-effect -- the release itself arrives asynchronously
    void checkForUpdates(false);
  }, [checkForUpdates]);

  useEffect(() => {
    if (!user) return;
    listApiKeys()
      .then(setApiKeys)
      .catch(() => {});
    if (user.is_superuser) {
      // Each refresher awaits its admin endpoint before writing results; the rule follows
      // the call but not the `await` inside it, and reads the in-flight flags as cascades.
      // oxlint-disable-next-line react/set-state-in-effect -- results are applied after the fetch resolves
      refreshUsers().catch(() => {});
      refreshCollectionAccess().catch(() => {});
      refreshPrinterAccess().catch(() => {});
    }
  }, [user, refreshUsers, refreshCollectionAccess, refreshPrinterAccess]);

  const loadTrash = useCallback(async () => {
    if (!user) {
      setTrashItems([]);
      return;
    }
    setTrashLoading(true);
    try {
      const [items, cfg, activePlan] = await Promise.all([
        listTrash(),
        getVaultConfig(),
        user.is_superuser ? getActiveGcPlan() : Promise.resolve(null),
      ]);
      setTrashItems(items);
      setTrashRetentionDays(cfg.trash_retention_days ?? 30);
      setTrashStorageTier(cfg.storage_tier ?? "unguarded");
      setTrashOperations(cfg.storage_operations);
      setGcPlan(activePlan);
    } catch (e) {
      toast.error(e);
    } finally {
      setTrashLoading(false);
    }
  }, [user]);

  useEffect(() => {
    if (activeSection === "trash") {
      // Opening the Trash section is what starts the listing fetch; `loadTrash` writes the
      // items only after awaiting the API, and synchronously sets just its loading flag.
      // oxlint-disable-next-line react/set-state-in-effect -- fetch-on-open of an external listing
      loadTrash();
    }
  }, [activeSection, loadTrash]);

  const loadBackups = useCallback(
    async (preserve?: BackupMeta) => {
      if (!user?.is_superuser) {
        setBackups([]);
        return;
      }
      setBackupsLoading(true);
      try {
        const [owned, unowned, unownedS3, unownedRemote, config, connections] =
          await Promise.allSettled([
            listBackupSources(),
            listUnownedLocalBackups(),
            listUnownedS3Backups(),
            listUnownedRemoteBackups(),
            getVaultConfig(),
            listStorageConnections(),
          ]);
        if (owned.status === "rejected") throw owned.reason;
        const refreshedBackups = owned.value;
        setBackups(
          preserve &&
            !refreshedBackups.some((item) => backupSourceKey(item) === backupSourceKey(preserve))
            ? [preserve, ...refreshedBackups]
            : refreshedBackups,
        );
        // The discovery endpoint is additive. Older servers may return 404, in
        // which case owned backups remain fully usable and the candidate panel
        // simply stays empty.
        setUnownedBackups(unowned.status === "fulfilled" ? unowned.value : []);
        setUnownedS3Backups(unownedS3.status === "fulfilled" ? unownedS3.value : []);
        setUnownedRemoteBackups(unownedRemote.status === "fulfilled" ? unownedRemote.value : []);
        if (config.status === "fulfilled") {
          setBackupRetentionDays(String(config.value.backup_retention_days ?? 30));
          setAutomaticBackupsEnabled(config.value.automatic_backups_enabled ?? false);
          setAutomaticBackupTimeUtc(config.value.automatic_backup_time_utc ?? "02:00");
          setManualLocalBackupEnabled(config.value.manual_local_backup_enabled ?? true);
          setAutomaticLocalBackupEnabled(config.value.automatic_local_backup_enabled ?? true);
        }
        setBackupConnections(
          connections.status === "fulfilled"
            ? connections.value.filter(
                (connection) => connection.purpose === "backup" || connection.purpose === "both",
              )
            : [],
        );
      } catch (e) {
        toast.error(e);
      } finally {
        setBackupsLoading(false);
      }
    },
    [user],
  );

  async function confirmAdoptBackup() {
    if (!adoptTarget) return;
    const target = adoptTarget;
    setAdoptingBackup(true);
    try {
      await adoptLocalBackup(target.filename);
      toast.success(t("settings.backupLegacyAdopted", { filename: target.filename }));
      setAdoptTarget(null);
      await loadBackups();
    } catch (e) {
      toast.error(e);
    } finally {
      setAdoptingBackup(false);
    }
  }

  async function confirmAdoptS3Backup() {
    if (!adoptS3Target) return;
    const target = adoptS3Target;
    if (!target.source_ref || !target.archive_sha256) {
      toast.error(t("settings.backupLegacySourceUnavailable"));
      return;
    }
    setAdoptingS3Backup(true);
    try {
      await adoptS3Backup(target.key, target.source_ref, target.archive_sha256);
      toast.success(t("settings.backupLegacyAdopted", { filename: target.key }));
      setAdoptS3Target(null);
      await loadBackups();
    } catch (e) {
      toast.error(e);
    } finally {
      setAdoptingS3Backup(false);
    }
  }

  async function confirmAdoptRemoteBackup() {
    if (!adoptRemoteTarget) return;
    const target = adoptRemoteTarget;
    if (!target.source_ref || !target.archive_sha256) {
      toast.error(t("settings.backupLegacySourceUnavailable"));
      return;
    }
    setAdoptingRemoteBackup(true);
    try {
      await adoptRemoteBackup(
        target.connection_id,
        target.key,
        target.source_ref,
        target.archive_sha256,
      );
      toast.success(t("settings.backupLegacyAdopted", { filename: target.key }));
      setAdoptRemoteTarget(null);
      await loadBackups();
    } catch (e) {
      toast.error(e);
    } finally {
      setAdoptingRemoteBackup(false);
    }
  }

  useEffect(() => {
    if (activeSection === "backup") {
      // Same shape as the trash listing above: opening Backup starts the listing fetch,
      // and only the loading flag is written before the await.
      // oxlint-disable-next-line react/set-state-in-effect -- fetch-on-open of an external listing
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

  useEffect(() => {
    if (activeSection !== "previews" || !user?.is_superuser) return;
    let cancelled = false;
    getVaultConfig()
      .then((cfg) => {
        if (cancelled) return;
        setModelThumbnailWidth(cfg.model_thumbnail_width);
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
      toast.success(next ? "Auto-mark known good enabled." : "Auto-mark known good disabled.");
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

  function savePreviewPreference(patch: Partial<PreviewPreferences>) {
    const next = { ...previewPreferences, ...patch };
    setPreviewPreferences(next);
    writePreviewPreferences(next);
    toast.success("Preview settings saved for this browser.");
  }

  async function saveModelThumbnailWidth(next: ModelThumbnailWidth) {
    const previous = modelThumbnailWidth;
    setModelThumbnailWidth(next);
    setPreviewBusy("quality");
    try {
      await updateVaultConfig({ model_thumbnail_width: next });
      toast.success("Model image quality updated for new previews.");
    } catch (e) {
      setModelThumbnailWidth(previous);
      toast.error(e);
    } finally {
      setPreviewBusy(null);
    }
  }

  async function recreateModelImages() {
    setPreviewBusy("rebuild");
    try {
      const response = await rebuildModelThumbnails();
      trackImportJob(response.job_id, "Recreate Model preview images");
      toast.success("Model preview recreation queued. Follow it in Tasks.");
    } catch (e) {
      toast.error(e);
    } finally {
      setPreviewBusy(null);
    }
  }

  async function handleBackupNow() {
    setBackingUp(true);
    try {
      const meta = await createBackup();
      const mb = (meta.size_bytes / 1024 / 1024).toFixed(1);
      await loadBackups(meta);
      if (meta.outcome === "partial") {
        toast.warning(t("settings.backupPartialNotice"));
      } else {
        toast.success(`Backup created — ${meta.file_count} files, ${mb} MB`);
      }
    } catch (e) {
      toast.error(e);
    } finally {
      setBackingUp(false);
      setBackupRunRefresh((value) => value + 1);
    }
  }

  async function handleBackupUpload(file: File) {
    setUploadingBackup(true);
    try {
      const meta = await uploadBackup(file);
      setBackups((current) => [
        meta,
        ...current.filter((item) => backupSourceKey(item) !== backupSourceKey(meta)),
      ]);
      toast.success(`Backup uploaded — ${meta.file_count} files`);
    } catch (e) {
      toast.error(e);
    } finally {
      setUploadingBackup(false);
    }
  }

  async function saveBackupRetention() {
    if (parsedBackupRetentionDays === null) return;
    setBackupRetentionBusy(true);
    try {
      await updateVaultConfig({ backup_retention_days: parsedBackupRetentionDays });
      toast.success(t("settings.backupRetentionSaved"));
    } catch (e) {
      toast.error(e);
    } finally {
      setBackupRetentionBusy(false);
    }
  }

  function setBackupConnectionSelection(
    connectionId: number,
    field: "manual_backup_enabled" | "automatic_backup_enabled",
    value: boolean,
  ) {
    setBackupConnections((current) =>
      current.map((connection) =>
        connection.id === connectionId ? { ...connection, [field]: value } : connection,
      ),
    );
  }

  async function saveBackupPolicy() {
    const manualDestinationSelected =
      manualLocalBackupEnabled ||
      backupConnections.some(
        (connection) => connection.enabled && connection.manual_backup_enabled,
      );
    if (!manualDestinationSelected) {
      toast.error(t("settings.backupManualDestinationRequired"));
      return;
    }
    const automaticDestinationSelected =
      automaticLocalBackupEnabled ||
      backupConnections.some(
        (connection) => connection.enabled && connection.automatic_backup_enabled,
      );
    if (automaticBackupsEnabled && !automaticDestinationSelected) {
      toast.error(t("settings.backupAutomaticDestinationRequired"));
      return;
    }
    setBackupPolicyBusy(true);
    try {
      const [config, ...connections] = await Promise.all([
        updateVaultConfig({
          automatic_backups_enabled: automaticBackupsEnabled,
          automatic_backup_time_utc: automaticBackupTimeUtc,
          manual_local_backup_enabled: manualLocalBackupEnabled,
          automatic_local_backup_enabled: automaticLocalBackupEnabled,
        }),
        ...backupConnections.map((connection) =>
          updateStorageConnection(connection.id, {
            manual_backup_enabled: connection.manual_backup_enabled,
            automatic_backup_enabled: connection.automatic_backup_enabled,
          }),
        ),
      ]);
      setAutomaticBackupsEnabled(config.automatic_backups_enabled);
      setAutomaticBackupTimeUtc(config.automatic_backup_time_utc);
      setManualLocalBackupEnabled(config.manual_local_backup_enabled);
      setAutomaticLocalBackupEnabled(config.automatic_local_backup_enabled);
      setBackupConnections(connections);
      toast.success(t("settings.backupPolicySaved"));
    } catch (e) {
      toast.error(e);
    } finally {
      setBackupPolicyBusy(false);
    }
  }

  async function confirmRestoreBackup() {
    if (!restoreTarget) return;
    const target = restoreTarget;
    setRestoringBackup(true);
    try {
      const result = await restoreBackup(target.backup_id, target.source_ref);
      toast.success(`Backup restored — ${result.restored_files} files`);
      setRestoreTarget(null);
      window.setTimeout(() => window.location.reload(), 800);
    } catch (e) {
      toast.error(e);
    } finally {
      setRestoringBackup(false);
    }
  }

  async function handleDownloadBackup(backup: BackupMeta) {
    const sourceRef = backupSourceKey(backup);
    setDownloadingBackup(sourceRef);
    try {
      await downloadBackup(backup.backup_id, backup.source_ref);
      toast.success("Backup download started.");
    } catch (e) {
      toast.error(e);
    } finally {
      setDownloadingBackup(null);
    }
  }

  async function confirmDeleteBackup() {
    if (!deleteBackupTarget) return;
    const target = deleteBackupTarget;
    const sourceKey = backupSourceKey(target);
    setDeletingBackup(sourceKey);
    try {
      await deleteBackup(target.backup_id, target.source_ref);
      setBackups((current) => current.filter((item) => backupSourceKey(item) !== sourceKey));
      const deletedKey = target.key;
      const deletedFilename = deletedKey?.split("/").at(-1);
      setUnownedBackups((current) =>
        current.filter((candidate) => candidate.filename !== deletedFilename),
      );
      setUnownedS3Backups((current) => current.filter((candidate) => candidate.key !== deletedKey));
      setUnownedRemoteBackups((current) =>
        current.filter((candidate) => candidate.key !== deletedKey),
      );
      setDeleteBackupTarget(null);
      toast.success(t("settings.backupDeleteSuccess"));
    } catch (e) {
      toast.error(e);
    } finally {
      setDeletingBackup(null);
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

  async function exportArchive() {
    setArchiveBusy("export");
    try {
      await downloadLibraryArchive();
    } catch (e) {
      toast.error(e);
    } finally {
      setArchiveBusy(null);
    }
  }

  async function importArchive(file: File) {
    setArchiveBusy("import");
    try {
      const result = await importLibraryArchive(file);
      toast.success(`Library import queued (${result.job_id.slice(0, 8)}). Follow it in activity.`);
    } catch (e) {
      toast.error(e);
    } finally {
      setArchiveBusy(null);
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

  async function setupBrowserExtension() {
    if (!user) return;
    setKeyBusy(true);
    try {
      const created = await createApiKey("Browser extension");
      setNewApiKey(created.api_key);
      setApiKeys((current) => [created, ...current]);
      prepareBrowserExtensionSetup(window.location.origin, user.username, created.api_key);
      setExtensionSetupReady(true);
      toast.success("Extension setup prepared. Open the browser extension on this tab.");
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

  async function savePrinterAccess() {
    if (!printerAccessUserId || !accessPrinterId) return;
    setPrinterAccessBusy("save");
    try {
      await updatePrinterPermission(
        Number(accessPrinterId),
        Number(printerAccessUserId),
        printerAccessRole,
      );
      await refreshPrinterAccess();
      toast.success("Printer access saved.");
    } catch (e) {
      toast.error(e);
    } finally {
      setPrinterAccessBusy(null);
    }
  }

  async function removePrinterAccess(printerId: number, userId: number) {
    setPrinterAccessBusy(`${printerId}:${userId}`);
    try {
      await deletePrinterPermission(printerId, userId);
      setPrinterPermissions((current) =>
        current.filter((row) => row.printer_id !== printerId || row.user_id !== userId),
      );
      toast.success("Printer access removed.");
    } catch (e) {
      toast.error(e);
    } finally {
      setPrinterAccessBusy(null);
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

  async function confirmRestart() {
    setRestartBusy(true);
    try {
      await restartPrintStash();
      setRestartConfirmOpen(false);
      toast.success(t("settings.restartSuccess"));
    } catch (e) {
      toast.error(e);
    } finally {
      setRestartBusy(false);
    }
  }

  function updateMetadataPreference(field: keyof MetadataPreferences, visible: boolean) {
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
    const next: MetadataPreferences = { ...metadataPrefs };
    for (const field of METADATA_FIELDS) next[field.id] = visible;
    setMetadataPrefs(next);
    writeMetadataPreferences(next);
  }

  function updateCardMetric(slot: 0 | 1 | 2, id: CardMetricId) {
    const next: CardMetrics = [cardMetrics[0], cardMetrics[1], cardMetrics[2]];
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
      const result = await purgeModel(
        id,
        trashOperations?.catalog_purge.confirmation_required ?? trashStorageTier !== "verified",
      );
      setTrashItems((current) => current.filter((item) => item.id !== id));
      setTrashPurgeResult(result);
      if (result.storage_cleanup_status === "completed") {
        toast.success(cleanupStatusMessage(t, result));
      } else {
        toast.warning(cleanupStatusMessage(t, result));
      }
    } catch (e) {
      toast.error(e);
    } finally {
      setTrashBusy(null);
    }
  }

  async function createExpiredGcPreview() {
    setPurgeExpiredOpen(false);
    setTrashBusy("gc");
    try {
      const plan = await createGcPlan();
      setGcPlan(plan);
      setGcDigestConfirmation("");
      toast.success(
        `Preview created for ${plan.resource_count} expired resource${plan.resource_count === 1 ? "" : "s"}. Nothing was deleted.`,
      );
    } catch (e) {
      toast.error(e);
    } finally {
      setTrashBusy(null);
    }
  }

  async function approveExpiredGcPlan() {
    if (!gcPlan) return;
    setTrashBusy("gc");
    try {
      const approved = await approveGcPlan(gcPlan.id, gcDigestConfirmation);
      setGcPlan(approved);
      setGcDigestConfirmation("");
      toast.success("Backup verified. The plan is now in its recovery quarantine.");
    } catch (e) {
      toast.error(e);
    } finally {
      setTrashBusy(null);
    }
  }

  async function abortExpiredGcPlan() {
    if (!gcPlan) return;
    setTrashBusy("gc");
    try {
      const aborted = await abortGcPlan(gcPlan.id);
      setGcPlan(aborted);
      setGcDigestConfirmation("");
      toast.success("GC plan aborted. Every candidate remains in the trash.");
    } catch (e) {
      toast.error(e);
    } finally {
      setTrashBusy(null);
    }
  }

  async function finalizeExpiredGcPlan() {
    if (!gcPlan) return;
    setTrashBusy("gc");
    try {
      const finalized = await finalizeGcPlan(gcPlan.id);
      setGcPlan(finalized);
      toast.success("GC plan finalized after all safety evidence was reverified.");
      await loadTrash();
    } catch (e) {
      toast.error(e);
    } finally {
      setTrashBusy(null);
    }
  }

  // KPI tiles — the headline numbers, no overlap with the system list below.
  const kpiItems = [
    {
      label: "Models",
      value: stats ? `${stats.model_count}` : "...",
      desc: "Live library entries",
      icon: Boxes,
    },
    {
      label: "Files",
      value: stats ? `${stats.file_count}` : "...",
      desc: `${stats?.source_file_count ?? 0} source · ${stats?.gcode_file_count ?? 0} G-code`,
      icon: Files,
    },
    {
      label: "Storage used",
      value: stats ? formatBytes(stats.storage.total_size_bytes) : "...",
      desc: stats ? `${stats.storage.object_count} stored objects` : "Backend usage",
      icon: HardDrive,
    },
    {
      label: "Printers",
      value: stats ? `${stats.printer_count}` : "...",
      desc: "Configured devices",
      icon: Printer,
    },
  ];

  // System detail rows — configuration facts, distinct from the KPI tiles.
  const systemItems = [
    {
      label: "Vault version",
      value: health ? `${health.name} v${health.version}` : "Loading...",
      desc: "API server status and version",
      icon: Server,
    },
    {
      label: "Database",
      value: health?.components?.database
        ? health.components.database.ok
          ? "Connected"
          : "Unavailable"
        : health?.status === "ok"
          ? "Connected"
          : "Unknown",
      desc: "SQLite by default, Postgres optional",
      icon: Database,
    },
    {
      label: "Storage backend",
      value: stats ? stats.storage.backend.toUpperCase() : "...",
      desc: stats?.storage.bucket ?? stats?.storage.prefix ?? "Configured vault storage",
      icon: HardDrive,
    },
    {
      label: "Indexed files",
      value: stats ? formatBytes(stats.indexed_size_bytes) : "...",
      desc: "Tracked in the database",
      icon: Files,
    },
    {
      label: "Collections",
      value: stats ? `${stats.collection_count}` : "...",
      desc: "Hierarchical tree entries",
      icon: FolderTree,
    },
    {
      label: "Tags",
      value: stats ? `${stats.tag_count}` : "...",
      desc: "Flat tag vocabulary size",
      icon: Tag,
    },
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
  const selectedPrinterPermissions = printerAccessUserId
    ? printerPermissions.filter((row) => row.user_id === Number(printerAccessUserId))
    : [];
  const printerById = new Map(accessPrinters.map((row) => [row.id, row]));
  const activePrinterAccessUser = printerAccessUserId
    ? users.find((row) => row.id === Number(printerAccessUserId))
    : null;

  return (
    <Localized>
      <div className="w-full space-y-6">
        {user?.is_superuser && (
          <Button variant="outline" onClick={() => router.push("/getting-started")}>
            {t("setup.resume")}
          </Button>
        )}
        <ConfirmModal
          open={restartConfirmOpen}
          onClose={() => {
            if (!restartBusy) setRestartConfirmOpen(false);
          }}
          onConfirm={confirmRestart}
          busy={restartBusy}
          title={t("settings.restartTitle")}
          description={t("settings.restartDescription")}
          confirmLabel={t("settings.restartConfirm")}
        />
        <ConfirmModal
          open={purgeTarget !== null}
          onClose={() => setPurgeTarget(null)}
          onConfirm={confirmPurge}
          busy={isModelPurge(trashBusy)}
          title={
            (trashOperations?.physical_delete.allowed ?? trashStorageTier === "verified")
              ? "Permanently delete?"
              : t("storage.catalogConfirmation")
          }
          description={
            (trashOperations?.physical_delete.allowed ?? trashStorageTier === "verified")
              ? "This will delete the model and all its files immediately. This cannot be undone."
              : t("storage.catalogOnly")
          }
          confirmLabel={
            (trashOperations?.physical_delete.allowed ?? trashStorageTier === "verified")
              ? "Delete forever"
              : t("storage.catalogConfirmAction")
          }
        />
        <ConfirmModal
          open={purgeExpiredOpen}
          onClose={() => setPurgeExpiredOpen(false)}
          onConfirm={createExpiredGcPreview}
          busy={trashBusy === "gc"}
          title="Create a safe GC preview?"
          description="This only records a bounded candidate plan. It does not delete catalog rows or storage bytes. Approval later requires the exact digest, verified storage, and a recent independent backup."
          confirmLabel="Create preview"
        />
        <ConfirmModal
          open={restoreTarget !== null}
          onClose={() => setRestoreTarget(null)}
          onConfirm={confirmRestoreBackup}
          busy={restoringBackup}
          title="Restore backup?"
          description={
            restoreTarget
              ? restoreSourceDescription(restoreTarget, t)
              : t("settings.backupRestoreWarning")
          }
          confirmLabel="Restore"
        />
        <ConfirmModal
          open={deleteBackupTarget !== null}
          onClose={() => {
            if (deletingBackup === null) setDeleteBackupTarget(null);
          }}
          onConfirm={confirmDeleteBackup}
          busy={deletingBackup !== null}
          title={t("settings.backupDeleteConfirmTitle")}
          description={
            deleteBackupTarget
              ? t("settings.backupDeleteConfirmDescription", {
                  source: backupSourceDescription(deleteBackupTarget, t),
                })
              : ""
          }
          confirmLabel={t("settings.backupDeleteAction")}
        />
        <ConfirmModal
          open={adoptTarget !== null}
          onClose={() => {
            if (!adoptingBackup) setAdoptTarget(null);
          }}
          onConfirm={confirmAdoptBackup}
          busy={adoptingBackup}
          title={t("settings.backupLegacyConfirmTitle")}
          description={
            adoptTarget
              ? t("settings.backupLegacyConfirmDescription", {
                  filename: adoptTarget.filename,
                  files: String(adoptTarget.file_count),
                  size: formatBytes(adoptTarget.size_bytes),
                })
              : ""
          }
          confirmLabel={t("settings.backupLegacyAdoptAction")}
        />
        <ConfirmModal
          open={adoptS3Target !== null}
          onClose={() => {
            if (!adoptingS3Backup) setAdoptS3Target(null);
          }}
          onConfirm={confirmAdoptS3Backup}
          busy={adoptingS3Backup}
          title={t("settings.backupS3ConfirmTitle")}
          description={
            adoptS3Target
              ? t("settings.backupS3ConfirmDescription", {
                  key: adoptS3Target.key,
                  namespace: adoptS3Target.namespace ?? "unavailable",
                  hash: adoptS3Target.archive_sha256?.slice(0, 16) ?? "unavailable",
                })
              : ""
          }
          confirmLabel={t("settings.backupLegacyAdoptAction")}
        />
        <ConfirmModal
          open={adoptRemoteTarget !== null}
          onClose={() => {
            if (!adoptingRemoteBackup) setAdoptRemoteTarget(null);
          }}
          onConfirm={confirmAdoptRemoteBackup}
          busy={adoptingRemoteBackup}
          title={t("settings.backupRemoteConfirmTitle")}
          description={
            adoptRemoteTarget
              ? t("settings.backupRemoteConfirmDescription", {
                  key: adoptRemoteTarget.key,
                  connection: adoptRemoteTarget.connection_name,
                  hash: adoptRemoteTarget.archive_sha256?.slice(0, 16) ?? "unavailable",
                })
              : ""
          }
          confirmLabel={t("settings.backupLegacyAdoptAction")}
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

        <PageHeader title={t("settings.title")} description={t("settings.description")} />

        <div className="border-b border-border pb-3 lg:hidden">
          <TabBar
            tabs={visibleSettingsSections.map((section) => {
              const Icon = section.icon;
              return {
                key: section.id,
                label: (
                  <>
                    <Icon className="h-4 w-4" />
                    {t(section.labelKey)}
                  </>
                ),
              };
            })}
            active={activeSection}
            onChange={changeSection}
            className="gap-1 overflow-x-auto"
            tabClassName="inline-flex shrink-0 items-center gap-2 whitespace-nowrap rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-[color,background-color,transform] duration-press active:scale-[0.99] hover:bg-popover-hover hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
            activeTabClassName="bg-accent text-accent-foreground"
            showIndicator={false}
          />
        </div>

        <div className="lg:grid lg:grid-cols-[13rem_minmax(0,1fr)] lg:items-start lg:gap-6">
          <nav
            aria-label="Settings sections"
            className="sticky top-0 hidden rounded-lg border border-border bg-card p-2 shadow-sm lg:block"
          >
            {visibleSettingsSections.map((section) => {
              const Icon = section.icon;
              const isActive = section.id === activeSection;
              return (
                <button
                  key={section.id}
                  type="button"
                  aria-current={isActive ? "page" : undefined}
                  onClick={() => changeSection(section.id)}
                  className={cn(
                    "flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left text-sm font-medium transition-[color,background-color,transform] duration-press active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
                    isActive
                      ? "bg-accent text-accent-foreground"
                      : "text-muted-foreground hover:bg-popover-hover hover:text-foreground",
                  )}
                >
                  <Icon className="h-4 w-4 shrink-0" />
                  <span>{t(section.labelKey)}</span>
                </button>
              );
            })}
          </nav>

          <main className="min-w-0">
            {releaseStatus?.update_available && releaseStatus.latest_version && (
              <div
                role="status"
                aria-live="polite"
                className="mb-6 flex flex-col gap-4 rounded-lg border border-warning/30 bg-warning/10 p-4 sm:flex-row sm:items-center"
              >
                <div className="flex min-w-0 flex-1 items-start gap-3">
                  <CircleArrowUp className="mt-0.5 h-5 w-5 shrink-0 text-warning" />
                  <div>
                    <p className="text-sm font-semibold text-foreground">
                      PrintStash v{releaseStatus.latest_version} is available
                    </p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      This vault is running v{releaseStatus.current_version}. Review release notes
                      before updating your self-hosted installation.
                    </p>
                  </div>
                </div>
                <a
                  href={
                    releaseStatus.release_url ?? `https://github.com/${GITHUB_REPO}/releases/latest`
                  }
                  target="_blank"
                  rel="noreferrer noopener"
                  className={BTN_SECONDARY}
                >
                  View release
                </a>
              </div>
            )}

            {activeSection === "overview" && (
              <div className="space-y-6 animate-panel-in">
                {storageHealth && !storageHealth.ok && (
                  <div
                    role="alert"
                    className="flex items-start gap-3 rounded-lg border border-warning/30 bg-warning/10 p-4"
                  >
                    <HardDrive className="mt-0.5 h-5 w-5 shrink-0 text-warning" aria-hidden />
                    <div>
                      <p className="text-sm font-semibold text-foreground">
                        {t("settings.storageUnavailableTitle")}
                      </p>
                      <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                        {t("settings.storageUnavailableDescription")}
                      </p>
                    </div>
                  </div>
                )}
                {/* KPI tiles */}
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                  {kpiItems.map((item) => {
                    const Icon = item.icon;
                    return (
                      <div
                        key={item.label}
                        className="bg-card border border-border rounded p-4 sm:p-5"
                      >
                        <div className="flex items-center justify-between">
                          <p className="font-mono text-2xs uppercase tracking-wider text-muted-foreground">
                            {item.label}
                          </p>
                          <Icon className="h-4 w-4 text-muted-foreground/50" />
                        </div>
                        <p className="mt-2 text-2xl font-semibold text-foreground truncate">
                          {item.value}
                        </p>
                        <p className="mt-1 text-xs text-muted-foreground truncate">{item.desc}</p>
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
                    action={
                      user?.is_superuser && health?.capabilities?.restart ? (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={() => setRestartConfirmOpen(true)}
                        >
                          <RotateCcw className="h-3.5 w-3.5" aria-hidden />
                          {t("settings.restartAction")}
                        </Button>
                      ) : undefined
                    }
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

                  <SettingsCard
                    icon={Download}
                    title="Library migration"
                    description="Portable archive with models, metadata, print history, and original artifacts"
                  >
                    <div className="p-4 sm:p-5 space-y-4">
                      <p className="text-sm text-muted-foreground leading-relaxed">
                        Export a versioned archive for migration to another PrintStash installation.
                        Accounts, credentials, settings, and trash are excluded.
                      </p>
                      <div className="flex flex-wrap gap-2">
                        <button
                          type="button"
                          onClick={() => void exportArchive()}
                          disabled={archiveBusy !== null}
                          className={BTN_SECONDARY}
                        >
                          <Download className="h-3.5 w-3.5" />{" "}
                          {archiveBusy === "export" ? "Exporting" : "Export full library"}
                        </button>
                        {user?.is_superuser && (
                          <label
                            className={`${BTN_SECONDARY} ${archiveBusy !== null ? "pointer-events-none opacity-50" : "cursor-pointer"}`}
                          >
                            <Download className="h-3.5 w-3.5 rotate-180" />{" "}
                            {archiveBusy === "import" ? "Importing" : "Import archive"}
                            <input
                              type="file"
                              accept=".zip,application/zip"
                              className="sr-only"
                              disabled={archiveBusy !== null}
                              onChange={(event) => {
                                const file = event.target.files?.[0];
                                if (file) void importArchive(file);
                                event.target.value = "";
                              }}
                            />
                          </label>
                        )}
                      </div>
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
                        Download your searchable library context for spreadsheets, audits,
                        migrations, or local AI prompts.
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
                          <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                            Username
                          </span>
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
                          <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                            Email
                          </span>
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
                          <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                            Initial password
                          </span>
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
                          disabled={
                            usersBusy === "create" ||
                            !newUsername.trim() ||
                            newUserPassword.trim().length < 8
                          }
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
                                    onClick={() =>
                                      patchUser(row.id, { is_superuser: !row.is_superuser })
                                    }
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
                                  disabled={
                                    usersBusy === row.id ||
                                    (passwordDrafts[row.id]?.trim().length ?? 0) < 8
                                  }
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
                        <RefreshCw
                          className={`h-4 w-4 ${accessBusy === "load" ? "animate-spin" : ""}`}
                        />
                      </button>
                    }
                  >
                    <div className="p-4 sm:p-5 space-y-4">
                      <div className="grid gap-2 lg:grid-cols-[1fr_1.4fr_auto_auto]">
                        <label className="block space-y-1">
                          <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                            User
                          </span>
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
                          <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                            Collection
                          </span>
                          <select
                            value={accessCollectionId}
                            onChange={(event) =>
                              setAccessCollectionId(
                                event.target.value ? Number(event.target.value) : "",
                              )
                            }
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
                          <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                            Role
                          </span>
                          <select
                            value={accessRole}
                            onChange={(event) =>
                              setAccessRole(selectedOption(COLLECTION_ROLES, event.target.value))
                            }
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
                          {accessBusy === "save" ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <ShieldCheck className="h-3.5 w-3.5" />
                          )}
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
                                  onClick={() =>
                                    removeCollectionAccess(row.collection_id, row.user_id)
                                  }
                                  disabled={accessBusy === busyKey}
                                  className="rounded p-1 text-red-600 hover:bg-red-500/10 disabled:opacity-50"
                                  title="Remove collection access"
                                >
                                  {accessBusy === busyKey ? (
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                  ) : (
                                    <Trash2 className="h-4 w-4" />
                                  )}
                                </button>
                              </div>
                            );
                          })
                        )}
                      </div>
                    </div>
                  </SettingsCard>
                )}

                {user?.is_superuser && (
                  <SettingsCard
                    icon={Printer}
                    title="Printer access"
                    description="Grant access per printer. Roles build from view to print, machine control, and administration."
                    action={
                      <button
                        type="button"
                        onClick={refreshPrinterAccess}
                        disabled={printerAccessBusy === "load"}
                        className={BTN_ICON}
                        title="Refresh printer access"
                      >
                        <RefreshCw
                          className={`h-4 w-4 ${printerAccessBusy === "load" ? "animate-spin" : ""}`}
                        />
                      </button>
                    }
                  >
                    <div className="space-y-4 p-4 sm:p-5">
                      <div className="grid gap-2 lg:grid-cols-[1fr_1.4fr_auto_auto]">
                        <label className="block space-y-1">
                          <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                            User
                          </span>
                          <select
                            value={printerAccessUserId}
                            onChange={(event) => {
                              setPrinterAccessUserId(
                                event.target.value ? Number(event.target.value) : "",
                              );
                              setAccessPrinterId("");
                            }}
                            className={INPUT}
                            disabled={printerAccessBusy === "load"}
                          >
                            <option value="">Choose printer user</option>
                            {nonSuperUsers.map((row) => (
                              <option key={row.id} value={row.id}>
                                {row.username}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className="block space-y-1">
                          <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                            Printer
                          </span>
                          <select
                            value={accessPrinterId}
                            onChange={(event) => {
                              const printerId = event.target.value
                                ? Number(event.target.value)
                                : "";
                              setAccessPrinterId(printerId);
                              const existing = printerPermissions.find(
                                (row) =>
                                  row.printer_id === printerId &&
                                  row.user_id === Number(printerAccessUserId),
                              );
                              setPrinterAccessRole(existing?.role ?? "view");
                            }}
                            className={INPUT}
                            disabled={!printerAccessUserId || printerAccessBusy === "load"}
                          >
                            <option value="">Select printer</option>
                            {accessPrinters.map((row) => (
                              <option key={row.id} value={row.id}>
                                {row.name}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className="block space-y-1">
                          <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                            Role
                          </span>
                          <select
                            value={printerAccessRole}
                            onChange={(event) =>
                              setPrinterAccessRole(
                                selectedOption(PRINTER_ROLES, event.target.value),
                              )
                            }
                            className={INPUT}
                            disabled={
                              !printerAccessUserId ||
                              !accessPrinterId ||
                              printerAccessBusy === "load"
                            }
                          >
                            <option value="view">View</option>
                            <option value="print">Print</option>
                            <option value="control">Control</option>
                            <option value="admin">Admin</option>
                          </select>
                        </label>
                        <button
                          type="button"
                          onClick={savePrinterAccess}
                          disabled={
                            !printerAccessUserId || !accessPrinterId || printerAccessBusy === "save"
                          }
                          className={`${BTN_PRIMARY} self-end`}
                        >
                          {printerAccessBusy === "save" ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <ShieldCheck className="h-3.5 w-3.5" />
                          )}
                          Save
                        </button>
                      </div>

                      <p className="text-xs text-muted-foreground">
                        View: status and history · Print: send and start jobs · Control: pause,
                        cancel, temperatures, homing, emergency stop · Admin: settings, files,
                        routing, and maintenance
                      </p>

                      <div className="overflow-hidden rounded border border-border">
                        <div className="grid grid-cols-[1fr_auto_auto] gap-3 border-b border-border bg-muted/40 px-3 py-2 font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                          <span>Printer</span>
                          <span>Role</span>
                          <span>Remove</span>
                        </div>
                        {!printerAccessUserId ? (
                          <p className="px-3 py-4 text-sm text-muted-foreground">
                            Select a user to review printer grants.
                          </p>
                        ) : selectedPrinterPermissions.length === 0 ? (
                          <p className="px-3 py-4 text-sm text-muted-foreground">
                            {activePrinterAccessUser?.username ?? "User"} has no direct printer
                            access.
                          </p>
                        ) : (
                          selectedPrinterPermissions.map((row) => {
                            const printer = printerById.get(row.printer_id);
                            const busyKey = `${row.printer_id}:${row.user_id}`;
                            return (
                              <div
                                key={busyKey}
                                className="grid grid-cols-[1fr_auto_auto] items-center gap-3 border-b border-border px-3 py-2 last:border-b-0"
                              >
                                <div className="min-w-0">
                                  <p className="truncate text-sm text-foreground">
                                    {printer?.name ?? `Printer #${row.printer_id}`}
                                  </p>
                                  <p className="text-xs text-muted-foreground">
                                    {printer?.group || "Ungrouped"}
                                  </p>
                                </div>
                                <span className="rounded bg-muted px-2 py-1 font-mono text-3xs uppercase text-muted-foreground">
                                  {row.role}
                                </span>
                                <button
                                  type="button"
                                  onClick={() => removePrinterAccess(row.printer_id, row.user_id)}
                                  disabled={printerAccessBusy === busyKey}
                                  className="rounded p-1 text-destructive hover:bg-destructive/10 disabled:opacity-50"
                                  title="Remove printer access"
                                >
                                  {printerAccessBusy === busyKey ? (
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                  ) : (
                                    <Trash2 className="h-4 w-4" />
                                  )}
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
                      <p className="text-sm text-muted-foreground">Sign in to create API keys.</p>
                    ) : (
                      <>
                        <div className="rounded border border-border bg-muted/40 p-3">
                          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                            <div className="min-w-0">
                              <p className="text-sm font-medium text-foreground">
                                Browser importer
                              </p>
                              <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
                                Create a dedicated key and prepare this vault in the browser
                                extension.
                              </p>
                            </div>
                            {extensionSetupReady ? (
                              <Badge
                                variant="success"
                                className="h-8 gap-1.5 border border-success/30 bg-success/10 px-3 font-mono text-success uppercase tracking-wider"
                                role="status"
                              >
                                <Check className="h-3.5 w-3.5" aria-hidden />
                                Setup prepared
                              </Badge>
                            ) : (
                              <Button
                                type="button"
                                size="xs"
                                onClick={setupBrowserExtension}
                                loading={keyBusy}
                                className="font-mono uppercase tracking-wider"
                              >
                                <Puzzle className="h-3.5 w-3.5" />
                                Set up extension
                              </Button>
                            )}
                          </div>
                          {extensionSetupReady && (
                            <p className="mt-3 text-xs font-medium text-muted-foreground">
                              Open the PrintStash extension on this tab to finish the verified
                              connection.
                            </p>
                          )}
                        </div>

                        <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
                          <label className="block space-y-1">
                            <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                              Key name
                            </span>
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
                            <p className="text-sm text-muted-foreground">No active API keys.</p>
                          ) : (
                            apiKeys.map((key) => (
                              <div
                                key={key.id}
                                className="flex items-center gap-3 border border-border rounded px-3 py-2"
                              >
                                <div className="min-w-0 flex-1">
                                  <p className="truncate text-sm text-foreground">{key.name}</p>
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
                            Use your username with this API key on{" "}
                            <code className="font-mono">/api/v1/auth/login</code>. The hook
                            exchanges it for a JWT Bearer token, then uploads with the normal{" "}
                            <code className="font-mono">Authorization</code> header.
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
                <StorageConfigCard storageHealth={storageHealth} />
              </div>
            )}

            {activeSection === "backup" && (
              <div className="space-y-6 animate-panel-in">
                <SettingsCard
                  icon={RefreshCw}
                  title={t("settings.backupRetentionTitle")}
                  description={t("settings.backupRetentionDescription")}
                  action={
                    <button
                      type="button"
                      onClick={saveBackupRetention}
                      disabled={
                        !user?.is_superuser ||
                        backupRetentionBusy ||
                        parsedBackupRetentionDays === null
                      }
                      className={BTN_PRIMARY}
                    >
                      {backupRetentionBusy ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Check className="h-3.5 w-3.5" />
                      )}
                      {t("settings.backupRetentionSave")}
                    </button>
                  }
                >
                  <div className="p-4 sm:p-5">
                    <label className="block max-w-xs text-xs text-muted-foreground">
                      {t("settings.backupRetentionLabel")}
                      <input
                        type="number"
                        min={0}
                        max={365}
                        value={backupRetentionDays}
                        disabled={!user?.is_superuser || backupRetentionBusy}
                        onChange={(event) => setBackupRetentionDays(event.target.value)}
                        aria-invalid={parsedBackupRetentionDays === null}
                        aria-describedby={
                          parsedBackupRetentionDays === null ? "backup-retention-error" : undefined
                        }
                        className={cn(inputClasses, "mt-1.5 w-32 font-mono")}
                      />
                    </label>
                    {parsedBackupRetentionDays === null && (
                      <p
                        id="backup-retention-error"
                        role="alert"
                        className="mt-2 text-xs text-destructive"
                      >
                        {t("settings.backupRetentionError")}
                      </p>
                    )}
                  </div>
                </SettingsCard>
                <SettingsCard
                  icon={Clock}
                  title={t("settings.backupPolicyTitle")}
                  description={t("settings.backupPolicyDescription")}
                  stackActionOnMobile
                  action={
                    <button
                      type="button"
                      onClick={saveBackupPolicy}
                      disabled={!user?.is_superuser || backupPolicyBusy || backupsLoading}
                      className={cn(BTN_PRIMARY, "w-full sm:w-auto")}
                    >
                      {backupPolicyBusy ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Check className="h-3.5 w-3.5" />
                      )}
                      {t("settings.backupPolicySave")}
                    </button>
                  }
                >
                  <div className="space-y-5 p-4 sm:p-5">
                    <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_12rem] sm:items-end">
                      <label className="flex items-center justify-between gap-4 rounded-md border border-border bg-muted/40 p-3">
                        <span>
                          <span className="block text-sm font-medium text-foreground">
                            {t("settings.backupAutomaticEnable")}
                          </span>
                          <span className="block text-xs text-muted-foreground">
                            {t("settings.backupAutomaticEnableDescription")}
                          </span>
                        </span>
                        <Checkbox
                          checked={automaticBackupsEnabled}
                          onChange={setAutomaticBackupsEnabled}
                          ariaLabel={t("settings.backupAutomaticEnable")}
                          disabled={!user?.is_superuser || backupPolicyBusy || backupsLoading}
                        />
                      </label>
                      <label className="text-xs text-muted-foreground">
                        {t("settings.backupAutomaticTime")}
                        <input
                          type="time"
                          value={automaticBackupTimeUtc}
                          onChange={(event) => setAutomaticBackupTimeUtc(event.target.value)}
                          disabled={
                            !user?.is_superuser ||
                            backupPolicyBusy ||
                            !automaticBackupsEnabled ||
                            backupsLoading
                          }
                          className={cn(inputClasses, "mt-1.5 w-full font-mono")}
                        />
                      </label>
                    </div>

                    <div className="overflow-hidden rounded-md border border-border">
                      <div className="grid grid-cols-[minmax(0,1fr)_5.5rem_5.5rem] gap-2 border-b bg-muted/30 px-3 py-2 text-2xs font-semibold uppercase tracking-wide text-muted-foreground">
                        <span>{t("settings.backupDestination")}</span>
                        <span className="text-center">{t("settings.backupManualColumn")}</span>
                        <span className="text-center">{t("settings.backupAutomaticColumn")}</span>
                      </div>
                      <div className="divide-y divide-border">
                        <div className="grid grid-cols-[minmax(0,1fr)_5.5rem_5.5rem] items-center gap-2 px-3 py-3">
                          <div className="min-w-0">
                            <p className="text-sm font-medium text-foreground">
                              {t("settings.backupLocalDestination")}
                            </p>
                            <p className="text-xs text-muted-foreground">
                              {t("settings.backupLocalRequired")}
                            </p>
                          </div>
                          <div className="flex justify-center">
                            <Checkbox
                              checked={manualLocalBackupEnabled}
                              onChange={setManualLocalBackupEnabled}
                              ariaLabel={t("settings.backupLocalManual")}
                              disabled={!user?.is_superuser || backupPolicyBusy || backupsLoading}
                            />
                          </div>
                          <div className="flex justify-center">
                            <Checkbox
                              checked={automaticLocalBackupEnabled}
                              onChange={setAutomaticLocalBackupEnabled}
                              ariaLabel={t("settings.backupLocalAutomatic")}
                              disabled={!user?.is_superuser || backupPolicyBusy || backupsLoading}
                            />
                          </div>
                        </div>
                        {backupConnections.map((connection) => (
                          <div
                            key={connection.id}
                            className="grid grid-cols-[minmax(0,1fr)_5.5rem_5.5rem] items-center gap-2 px-3 py-3"
                          >
                            <div className="min-w-0">
                              <p className="truncate text-sm font-medium text-foreground">
                                {connection.name}
                              </p>
                              <p className="text-xs uppercase text-muted-foreground">
                                {connection.kind}
                                {!connection.enabled
                                  ? ` · ${t("settings.backupDestinationPaused")}`
                                  : ""}
                              </p>
                            </div>
                            <div className="flex justify-center">
                              <Checkbox
                                checked={connection.manual_backup_enabled}
                                onChange={(value) =>
                                  setBackupConnectionSelection(
                                    connection.id,
                                    "manual_backup_enabled",
                                    value,
                                  )
                                }
                                ariaLabel={t("settings.backupUseManual", {
                                  name: connection.name,
                                })}
                                disabled={!user?.is_superuser || backupPolicyBusy || backupsLoading}
                              />
                            </div>
                            <div className="flex justify-center">
                              <Checkbox
                                checked={connection.automatic_backup_enabled}
                                onChange={(value) =>
                                  setBackupConnectionSelection(
                                    connection.id,
                                    "automatic_backup_enabled",
                                    value,
                                  )
                                }
                                ariaLabel={t("settings.backupUseAutomatic", {
                                  name: connection.name,
                                })}
                                disabled={!user?.is_superuser || backupPolicyBusy || backupsLoading}
                              />
                            </div>
                          </div>
                        ))}
                        {backupConnections.length === 0 ? (
                          <p className="px-3 py-4 text-sm text-muted-foreground">
                            {t("settings.backupNoRemoteDestinations")}
                          </p>
                        ) : null}
                      </div>
                    </div>
                  </div>
                </SettingsCard>
                <SettingsCard
                  icon={HardDrive}
                  title="Manual backup"
                  description={t("settings.backupManualDescription")}
                  action={
                    <button
                      type="button"
                      onClick={handleBackupNow}
                      disabled={!user?.is_superuser || backingUp}
                      className={BTN_PRIMARY}
                    >
                      {backingUp ? (
                        <>
                          <Loader2 className="h-3.5 w-3.5 animate-spin" /> Backing up…
                        </>
                      ) : (
                        <>
                          <HardDrive className="h-3.5 w-3.5" /> Backup now
                        </>
                      )}
                    </button>
                  }
                >
                  <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between sm:p-5">
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-foreground">
                        {t("settings.backupUploadTitle")}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {t("settings.backupUploadDescription")}
                      </p>
                    </div>
                    <label
                      className={cn(
                        BTN_SECONDARY,
                        "cursor-pointer",
                        uploadingBackup && "pointer-events-none opacity-50",
                      )}
                    >
                      {uploadingBackup ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Upload className="h-3.5 w-3.5" />
                      )}
                      {uploadingBackup
                        ? t("settings.backupUploading")
                        : t("settings.backupUploadAction")}
                      <input
                        type="file"
                        className="sr-only"
                        aria-label={t("settings.backupUploadTitle")}
                        accept=".tar.gz,application/gzip"
                        disabled={!user?.is_superuser || uploadingBackup}
                        onChange={(event) => {
                          const file = event.target.files?.[0];
                          event.target.value = "";
                          if (file) void handleBackupUpload(file);
                        }}
                      />
                    </label>
                  </div>
                </SettingsCard>
                {user?.is_superuser && (
                  <BackupRunHistory
                    refreshKey={backupRunRefresh}
                    onPublished={() => void loadBackups()}
                  />
                )}
                <SettingsCard
                  icon={RotateCcw}
                  title="Restore backup"
                  description="Recover the vault database and stored files from a previous backup."
                  action={
                    <button
                      type="button"
                      onClick={() => void loadBackups()}
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
                      <p className="p-4 sm:p-5 text-sm text-muted-foreground">Loading...</p>
                    ) : backups.length === 0 &&
                      unownedBackups.length === 0 &&
                      unownedS3Backups.length === 0 &&
                      unownedRemoteBackups.length === 0 ? (
                      <p className="p-4 sm:p-5 text-sm text-muted-foreground">No backups found.</p>
                    ) : (
                      <>
                        {unownedBackups.length > 0 && (
                          <div className="space-y-3 border-b border-warning/30 bg-warning/10 p-4 sm:p-5">
                            <div>
                              <p className="text-sm font-semibold text-foreground">
                                {t("settings.backupLegacyTitle")}
                              </p>
                              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                                {t("settings.backupLegacyDescription")}
                              </p>
                            </div>
                            {unownedBackups.map((candidate) => (
                              <div
                                key={candidate.filename}
                                className="grid gap-3 rounded border border-border bg-background/50 p-3 lg:grid-cols-[1fr_auto] lg:items-center"
                              >
                                <div className="min-w-0">
                                  <p className="truncate font-mono text-xs text-foreground">
                                    {candidate.filename}
                                  </p>
                                  <p className="mt-1 text-xs text-muted-foreground">
                                    {candidate.file_count} files ·{" "}
                                    {formatBytes(candidate.size_bytes)} · v{candidate.app_version} ·{" "}
                                    {formatDate(candidate.created_at)}
                                  </p>
                                </div>
                                <button
                                  type="button"
                                  onClick={() => setAdoptTarget(candidate)}
                                  disabled={adoptingBackup || restoringBackup || backingUp}
                                  className={BTN_SECONDARY}
                                >
                                  {t("settings.backupLegacyAdoptAction")}
                                </button>
                              </div>
                            ))}
                          </div>
                        )}
                        {unownedS3Backups.length > 0 && (
                          <div className="space-y-3 border-b border-warning/30 bg-warning/10 p-4 sm:p-5">
                            <div>
                              <p className="text-sm font-semibold text-foreground">
                                {t("settings.backupS3Title")}
                              </p>
                              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                                {t("settings.backupS3Description")}
                              </p>
                            </div>
                            {unownedS3Backups.map((candidate) => (
                              <div
                                key={candidate.source_ref ?? `${candidate.prefix}:${candidate.key}`}
                                className="grid gap-3 rounded border border-border bg-background/50 p-3 lg:grid-cols-[1fr_auto] lg:items-center"
                              >
                                <div className="min-w-0">
                                  <p className="truncate font-mono text-xs text-foreground">
                                    {candidate.key}
                                  </p>
                                  <p className="mt-1 text-xs text-muted-foreground">
                                    {t("settings.backupS3Namespace", {
                                      namespace: candidate.namespace ?? "unavailable",
                                    })}
                                  </p>
                                  <p className="mt-1 text-xs text-muted-foreground">
                                    {t("settings.backupProviderRef", {
                                      provider: shortOpaque(candidate.provider_ref),
                                    })}
                                  </p>
                                  <p className="mt-1 text-xs text-muted-foreground">
                                    {t("settings.backupPrefix", { prefix: candidate.prefix })} ·{" "}
                                    {candidate.file_count} files ·{" "}
                                    {formatBytes(candidate.size_bytes)} · v{candidate.app_version}
                                  </p>
                                  {candidate.candidate_kind && (
                                    <p className="mt-1 text-xs text-muted-foreground">
                                      {t("settings.backupCandidateKind", {
                                        kind: candidate.candidate_kind,
                                      })}
                                    </p>
                                  )}
                                  <p className="mt-1 truncate font-mono text-2xs text-muted-foreground">
                                    {t("settings.backupSha256", {
                                      digest: `${candidate.archive_sha256?.slice(0, 16) ?? "unavailable"}…`,
                                    })}
                                  </p>
                                </div>
                                <button
                                  type="button"
                                  onClick={() => setAdoptS3Target(candidate)}
                                  disabled={
                                    adoptingBackup ||
                                    adoptingS3Backup ||
                                    restoringBackup ||
                                    backingUp ||
                                    !candidate.source_ref ||
                                    !candidate.archive_sha256
                                  }
                                  className={BTN_SECONDARY}
                                >
                                  {t("settings.backupLegacyAdoptAction")}
                                </button>
                              </div>
                            ))}
                          </div>
                        )}
                        {unownedRemoteBackups.length > 0 && (
                          <div className="space-y-3 border-b border-warning/30 bg-warning/10 p-4 sm:p-5">
                            <div>
                              <p className="text-sm font-semibold text-foreground">
                                {t("settings.backupRemoteTitle")}
                              </p>
                              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                                {t("settings.backupRemoteDescription")}
                              </p>
                            </div>
                            {unownedRemoteBackups.map((candidate) => (
                              <div
                                key={`${candidate.connection_id}:${candidate.key}`}
                                className="grid gap-3 rounded border border-border bg-background/50 p-3 lg:grid-cols-[1fr_auto] lg:items-center"
                              >
                                <div className="min-w-0">
                                  <p className="truncate font-mono text-xs text-foreground">
                                    {candidate.key}
                                  </p>
                                  <p className="mt-1 text-xs text-muted-foreground">
                                    {candidate.connection_name} · {candidate.provider.toUpperCase()}{" "}
                                    · {formatBytes(candidate.size_bytes)} · v{candidate.app_version}
                                  </p>
                                  <p className="mt-1 truncate font-mono text-2xs text-muted-foreground">
                                    {t("settings.backupSha256", {
                                      digest: `${candidate.archive_sha256?.slice(0, 16) ?? "unavailable"}…`,
                                    })}
                                  </p>
                                </div>
                                <button
                                  type="button"
                                  onClick={() => setAdoptRemoteTarget(candidate)}
                                  disabled={
                                    adoptingRemoteBackup ||
                                    restoringBackup ||
                                    backingUp ||
                                    uploadingBackup ||
                                    !candidate.source_ref ||
                                    !candidate.archive_sha256
                                  }
                                  className={BTN_SECONDARY}
                                >
                                  {t("settings.backupLegacyAdoptAction")}
                                </button>
                              </div>
                            ))}
                          </div>
                        )}
                        {backups.map((backup) => (
                          <div
                            key={backupSourceKey(backup)}
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
                              <p className="mt-1 truncate font-mono text-2xs text-muted-foreground">
                                {t("settings.backupSourceLocator", {
                                  source: backup.namespace
                                    ? `${backup.namespace} · ${backup.source_ref?.slice(0, 16) ?? "legacy source"}`
                                    : (backup.source_ref?.slice(0, 16) ?? "legacy source"),
                                })}
                              </p>
                              <p className="mt-1 truncate font-mono text-2xs text-muted-foreground">
                                {t("settings.backupProviderRef", {
                                  provider: shortOpaque(backup.provider_ref),
                                })}
                              </p>
                              {backup.key && (
                                <p className="mt-1 truncate font-mono text-2xs text-muted-foreground">
                                  {t("settings.backupExactKey", { key: backup.key })}
                                </p>
                              )}
                              {backup.prefix && (
                                <p className="mt-1 truncate font-mono text-2xs text-muted-foreground">
                                  {t("settings.backupPrefix", { prefix: backup.prefix })}
                                </p>
                              )}
                              {backup.archive_sha256 && (
                                <p className="mt-1 truncate font-mono text-2xs text-muted-foreground">
                                  {t("settings.backupSha256", {
                                    digest: shortOpaque(backup.archive_sha256),
                                  })}
                                </p>
                              )}
                              {backup.candidate_kind && (
                                <p className="mt-1 text-xs text-muted-foreground">
                                  {t("settings.backupCandidateKind", {
                                    kind: backup.candidate_kind,
                                  })}
                                </p>
                              )}
                              <p className="mt-1 text-xs text-muted-foreground">
                                {backup.file_count} files · {formatBytes(backup.size_bytes)} ·{" "}
                                {backup.location}
                              </p>
                              {backup.operations &&
                                !backup.operations.automatic_retention.allowed && (
                                  <p className="mt-2 text-xs text-muted-foreground">
                                    {storageOperationMessage(
                                      backup.operations.automatic_retention.reason,
                                      t,
                                    )}
                                  </p>
                                )}
                              {backup.operations && !backup.operations.physical_delete.allowed && (
                                <p className="mt-1 text-xs text-muted-foreground">
                                  {storageOperationMessage(
                                    backup.operations.physical_delete.reason,
                                    t,
                                  )}
                                </p>
                              )}
                              {backup.operations && (
                                <p className="mt-1 text-xs text-muted-foreground">
                                  {storageOperationMessage(backup.operations.gc_witness.reason, t)}
                                </p>
                              )}
                            </div>
                            <div className="flex flex-wrap gap-2 lg:justify-end">
                              <button
                                type="button"
                                onClick={() => handleDownloadBackup(backup)}
                                disabled={
                                  downloadingBackup !== null ||
                                  restoringBackup ||
                                  backingUp ||
                                  !backup.source_ref
                                }
                                title={
                                  backup.source_ref
                                    ? undefined
                                    : t("settings.backupSourceUnavailable")
                                }
                                className={BTN_SECONDARY}
                              >
                                {downloadingBackup === backupSourceKey(backup) ? (
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
                                  backingUp ||
                                  !backup.source_ref
                                }
                                title={
                                  backup.source_ref
                                    ? undefined
                                    : t("settings.backupSourceUnavailable")
                                }
                                className="inline-flex items-center gap-1.5 px-3 py-2 rounded border border-red-500/30 text-red-500 hover:bg-red-500/10 transition-colors text-xs font-medium uppercase tracking-wider disabled:opacity-50 disabled:cursor-not-allowed"
                              >
                                <RotateCcw className="h-3.5 w-3.5" />
                                Restore
                              </button>
                              <Button
                                type="button"
                                variant="destructive"
                                size="xs"
                                onClick={() => setDeleteBackupTarget(backup)}
                                disabled={
                                  downloadingBackup !== null ||
                                  restoringBackup ||
                                  backingUp ||
                                  deletingBackup !== null ||
                                  backup.operations?.physical_delete.allowed === false ||
                                  !backup.source_ref
                                }
                                title={
                                  backup.source_ref
                                    ? undefined
                                    : t("settings.backupSourceUnavailable")
                                }
                                aria-label={t("settings.backupDeleteAction")}
                                className="uppercase tracking-wider"
                              >
                                {deletingBackup === backupSourceKey(backup) ? (
                                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                ) : (
                                  <Trash2 className="h-3.5 w-3.5" />
                                )}
                                {t("settings.backupDeleteAction")}
                              </Button>
                            </div>
                          </div>
                        ))}
                      </>
                    )}
                  </div>
                </SettingsCard>
              </div>
            )}

            {activeSection === "remote-storage" && (
              <div className="animate-panel-in">
                <RemoteStorageConnections disabled={!user?.is_superuser} />
              </div>
            )}

            {activeSection === "imports" && (
              <div className="space-y-6 animate-panel-in">
                <MakerWorldConnectCard />
                <ProviderConnectionsPanel />
              </div>
            )}

            {activeSection === "maintenance" && user?.is_superuser && <MaintenancePanel />}

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

            {activeSection === "sso" && user?.is_superuser && <OidcSettingsCard />}

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
                        <p className="text-[13px] font-medium text-foreground">
                          Show printer image
                        </p>
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
                        autoMarkKnownGood ? "bg-primary" : "bg-outline-variant"
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
                    <label htmlFor="display-currency" className="text-[13px] text-foreground">
                      Display currency
                    </label>
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
                                onClick={() => updateCardMetric(slot, opt.id)}
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
                    <button
                      type="button"
                      onClick={resetMetadataPreferences}
                      className={BTN_SECONDARY}
                    >
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

            {activeSection === "previews" && (
              <div className="space-y-6 animate-panel-in">
                <SettingsCard
                  icon={Eye}
                  title="Interactive previews"
                  description="Balance sharpness against GPU use in the 3D Model and G-code viewers. This preference is saved in this browser."
                >
                  <div className="grid gap-4 p-4 sm:grid-cols-2 sm:p-5">
                    <label className="block space-y-1">
                      <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                        Preview quality
                      </span>
                      <select
                        aria-label="Preview quality"
                        value={previewPreferences.previewQuality}
                        onChange={(event) =>
                          savePreviewPreference({
                            previewQuality: selectedOption(PREVIEW_QUALITIES, event.target.value),
                          })
                        }
                        className={INPUT}
                      >
                        <option value="performance">Performance · 1×</option>
                        <option value="balanced">Balanced · 1.5×</option>
                        <option value="detail">High detail · 2×</option>
                      </select>
                    </label>
                    <label className="block space-y-1">
                      <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                        Screenshot resolution
                      </span>
                      <select
                        aria-label="Screenshot resolution"
                        value={previewPreferences.screenshotScale}
                        onChange={(event) =>
                          savePreviewPreference({
                            screenshotScale: selectedOption(
                              SCREENSHOT_SCALES,
                              Number(event.target.value),
                            ),
                          })
                        }
                        className={INPUT}
                      >
                        <option value={1}>Standard · 1×</option>
                        <option value={2}>Sharp · 2×</option>
                        <option value={3}>Print-ready · 3×</option>
                      </select>
                    </label>
                  </div>
                </SettingsCard>

                <SettingsCard
                  icon={Images}
                  title="Model preview images"
                  description="Choose the resolution of generated Model card images. Higher settings take longer to render and use more memory and storage."
                >
                  <div className="grid gap-4 p-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end sm:p-5">
                    <label className="block space-y-1">
                      <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                        Model image quality
                      </span>
                      <select
                        aria-label="Model image quality"
                        value={modelThumbnailWidth}
                        onChange={(event) =>
                          saveModelThumbnailWidth(
                            selectedOption(MODEL_THUMBNAIL_WIDTHS, Number(event.target.value)),
                          )
                        }
                        disabled={!user?.is_superuser || previewBusy !== null}
                        className={INPUT}
                      >
                        {modelThumbnailWidth !== 320 &&
                          modelThumbnailWidth !== 640 &&
                          modelThumbnailWidth !== 1280 && (
                            <option value={modelThumbnailWidth}>
                              {translateUiText(locale, "Custom")} · {modelThumbnailWidth} ×{" "}
                              {Math.round((modelThumbnailWidth * 3) / 4)}
                            </option>
                          )}
                        <option value={320}>Compact · 320 × 240</option>
                        <option value={640}>Standard · 640 × 480</option>
                        <option value={1280}>High · 1280 × 960</option>
                      </select>
                    </label>
                    <button
                      type="button"
                      onClick={recreateModelImages}
                      disabled={!user?.is_superuser || previewBusy !== null}
                      className={BTN_PRIMARY}
                    >
                      {previewBusy === "rebuild" ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <RefreshCw className="h-3.5 w-3.5" />
                      )}
                      Recreate all images
                    </button>
                  </div>
                  <div className="border-t border-border px-4 py-3 sm:px-5">
                    <p className="text-xs text-muted-foreground">
                      Quality changes apply to new images. Recreate all images to update existing
                      Models in the background.
                    </p>
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
                      <span className="block text-2xs text-muted-foreground mb-1">Days</span>
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
                      onClick={() => setPurgeExpiredOpen(true)}
                      disabled={
                        !user?.is_superuser ||
                        trashBusy === "gc" ||
                        trashRetentionDays < 0 ||
                        (gcPlan !== null &&
                          ["preview", "quarantined", "finalizing"].includes(gcPlan.state))
                      }
                      className={BTN_SECONDARY}
                    >
                      <Eraser className="h-3.5 w-3.5" />
                      {trashBusy === "gc" ? "Preparing" : "Review expired"}
                    </button>
                  </div>
                  {trashPurgeResult && (
                    <div
                      role="status"
                      aria-live="polite"
                      className={cn(
                        "border-t px-4 py-3 text-xs sm:px-5",
                        (trashPurgeResult.storage_cleanup_status ?? "completed") === "completed"
                          ? "border-success/30 bg-success/10 text-success"
                          : "border-warning/30 bg-warning/10 text-warning",
                      )}
                    >
                      {cleanupStatusMessage(t, trashPurgeResult)}
                    </div>
                  )}
                  {gcPlan && (
                    <div className="border-t border-border bg-muted/20 px-4 py-4 sm:px-5">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                          <p className="text-xs font-semibold text-foreground">
                            GC plan #{gcPlan.id} · {gcPlan.state}
                          </p>
                          <p className="mt-1 text-xs text-muted-foreground">
                            {gcPlan.resource_count} of {gcPlan.candidate_pool_count} expired
                            resources · {gcPlan.key_count} storage keys ·{" "}
                            {formatBytes(gcPlan.size_bytes)}
                          </p>
                        </div>
                        <span className="rounded border border-border px-2 py-1 font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                          {gcPlan.backup_id ? "backup verified" : "no backup bound"}
                        </span>
                      </div>
                      <p className="mt-3 text-2xs text-muted-foreground">Exact plan digest</p>
                      <code className="mt-1 block break-all rounded border border-border bg-background px-2 py-2 text-3xs text-foreground">
                        {gcPlan.digest}
                      </code>
                      {gcPlan.state === "preview" && (
                        <div className="mt-3 space-y-3">
                          <p className="text-xs text-muted-foreground">
                            Approval is fail-closed: paste the exact digest below. The server will
                            also require verified storage and a recent backup on an independent S3
                            provider before starting the quarantine.
                          </p>
                          <input
                            className={INPUT}
                            aria-label="Confirm GC plan digest"
                            placeholder="Paste the 64-character digest"
                            value={gcDigestConfirmation}
                            disabled={trashBusy === "gc"}
                            onChange={(event) => setGcDigestConfirmation(event.target.value.trim())}
                          />
                        </div>
                      )}
                      {gcPlan.state === "quarantined" && gcPlan.quarantine_until && (
                        <p className="mt-3 text-xs text-muted-foreground">
                          Recovery quarantine ends {formatDateTime(gcPlan.quarantine_until)}. The
                          plan and backup are reverified before final deletion.
                        </p>
                      )}
                      {gcPlan.last_error && (
                        <p className="mt-3 text-xs text-destructive">{gcPlan.last_error}</p>
                      )}
                      <div className="mt-3 flex flex-wrap justify-end gap-2">
                        {gcPlan.state === "preview" && (
                          <button
                            type="button"
                            className={BTN_PRIMARY}
                            disabled={trashBusy === "gc" || gcDigestConfirmation !== gcPlan.digest}
                            onClick={approveExpiredGcPlan}
                          >
                            <ShieldCheck className="h-3.5 w-3.5" />
                            Verify backup and quarantine
                          </button>
                        )}
                        {gcPlan.state === "quarantined" && (
                          <button
                            type="button"
                            className={BTN_PRIMARY}
                            disabled={
                              trashBusy === "gc" || !gcPlan.quarantine_until || !gcQuarantineReady
                            }
                            onClick={finalizeExpiredGcPlan}
                          >
                            <Eraser className="h-3.5 w-3.5" />
                            Reverify and finalize
                          </button>
                        )}
                        {["preview", "quarantined"].includes(gcPlan.state) && (
                          <button
                            type="button"
                            className={BTN_SECONDARY}
                            disabled={trashBusy === "gc"}
                            onClick={abortExpiredGcPlan}
                          >
                            Abort plan
                          </button>
                        )}
                      </div>
                    </div>
                  )}
                </SettingsCard>

                <SettingsCard
                  icon={Boxes}
                  title="Deleted models"
                  description="Restore models or remove them permanently from storage."
                >
                  {trashItems.length > 0 && (
                    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border bg-muted/30 px-4 py-3 text-xs text-muted-foreground sm:px-5">
                      <span>{`${trashItems.length} deleted model${trashItems.length === 1 ? "" : "s"}`}</span>
                      <span className="font-mono tabular-nums" aria-label="Trash size">
                        {formatBytes(
                          trashItems.reduce((total, item) => total + item.size_bytes, 0),
                        )}{" "}
                        reclaimable
                      </span>
                    </div>
                  )}
                  <div className="divide-y divide-border">
                    {!user ? (
                      <p className="p-4 sm:p-5 text-sm text-muted-foreground">
                        Sign in to manage the trash.
                      </p>
                    ) : trashLoading ? (
                      <p className="p-4 sm:p-5 text-sm text-muted-foreground">Loading...</p>
                    ) : trashItems.length === 0 ? (
                      <p className="p-4 sm:p-5 text-sm text-muted-foreground">Trash is empty.</p>
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
                              Deleted {formatDate(item.deleted_at)} · Expires{" "}
                              {formatDate(item.expires_at)}
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
                        <h3 className="text-lg font-bold text-foreground tracking-tight">
                          PrintStash
                        </h3>
                        <span className="rounded-full bg-muted px-2 py-0.5 text-3xs font-semibold text-muted-foreground">
                          v{health?.version ?? "0.2.0"}
                        </span>
                      </div>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        Self-hosted asset management for 3D printing workflows.
                      </p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <button
                        type="button"
                        onClick={() => void checkForUpdates(true)}
                        disabled={releaseChecking}
                        className={BTN_SECONDARY}
                      >
                        <RefreshCw
                          className={cn("h-3.5 w-3.5", releaseChecking && "animate-spin")}
                        />
                        {releaseChecking ? "Checking" : "Check for updates"}
                      </button>
                      <a
                        href={`https://github.com/${GITHUB_REPO}`}
                        target="_blank"
                        rel="noreferrer noopener"
                        className="inline-flex items-center gap-1.5 rounded border border-border bg-background px-3 py-2 text-xs font-medium text-foreground hover:bg-muted transition-colors"
                      >
                        <svg
                          viewBox="0 0 24 24"
                          className="h-3.5 w-3.5"
                          fill="currentColor"
                          aria-hidden
                        >
                          <path d="M12 .5C5.7.5.5 5.7.5 12c0 5.1 3.3 9.4 7.9 10.9.6.1.8-.3.8-.6v-2c-3.2.7-3.9-1.5-3.9-1.5-.5-1.3-1.3-1.7-1.3-1.7-1.1-.7.1-.7.1-.7 1.2.1 1.8 1.2 1.8 1.2 1 1.8 2.8 1.3 3.5 1 .1-.8.4-1.3.8-1.6-2.6-.3-5.3-1.3-5.3-5.8 0-1.3.5-2.3 1.2-3.1-.1-.3-.5-1.5.1-3.1 0 0 1-.3 3.3 1.2a11.5 11.5 0 0 1 6 0C17 4.6 18 4.9 18 4.9c.6 1.6.2 2.8.1 3.1.8.8 1.2 1.8 1.2 3.1 0 4.5-2.7 5.5-5.3 5.8.4.4.8 1.1.8 2.2v3.3c0 .3.2.7.8.6 4.6-1.5 7.9-5.8 7.9-10.9C23.5 5.7 18.3.5 12 .5z" />
                        </svg>
                        GitHub
                      </a>
                    </div>
                  </div>
                  {releaseStatus && (
                    <div className="border-t border-border px-4 py-3 text-xs text-muted-foreground sm:px-6">
                      {releaseStatus.status === "up_to_date" &&
                        "Latest published release installed."}
                      {releaseStatus.status === "update_available" &&
                        releaseStatus.latest_version && (
                          <>Update available: v{releaseStatus.latest_version}.</>
                        )}
                      {releaseStatus.status === "unavailable" &&
                        "Release check unavailable. Try again later."}
                    </div>
                  )}
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
                          <span className="text-2xs text-muted-foreground pt-0.5">
                            {latestRelease.date}
                          </span>
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
    </Localized>
  );
}
