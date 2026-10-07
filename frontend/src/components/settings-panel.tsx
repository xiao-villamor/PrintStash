"use client";

import { GettingStartedReminder } from "@/components/getting-started-reminder";

import { knownUiText } from "@/lib/locale";
import { formatNumber } from "@/lib/format";
import { currentLocale } from "@/lib/locale";
import { uiText } from "@/lib/locale";
import { ApiError, parseApiError } from "@/lib/errors";
import { useUiLocale } from "@/lib/i18n";
import { useQuery } from "@tanstack/react-query";
import {
  backupSourceKey,
  backupSourcesOptions,
  unownedLocalBackupsOptions,
  unownedS3BackupsOptions,
  unownedRemoteBackupsOptions,
} from "@/lib/queries/settings-backup-catalog";
import { useBackupCommand } from "@/lib/queries/settings-backup-commands";
import {
  storageConnectionsOptions,
  useStorageConnectionCommand,
} from "@/lib/queries/settings-storage";
import {
  apiKeysOptions,
  adminUsersOptions,
  useApiKeyCommand,
  useAdminUserCommand,
  type ApiKeyReceipt,
} from "@/lib/queries/settings-account";
import { useVaultConfigCommand } from "@/lib/queries/settings-config";
import { getSessionVersion } from "@/lib/session-transport";
import {
  collectionAccessOptions,
  useCollectionAccessCommand,
  usePrinterAccessCommand,
  usePrinterAccess,
  accessDenied,
} from "@/lib/queries/settings-access";
import { onAuthChange } from "@/lib/auth-store";

import { useCallback, useEffect, useRef, useState } from "react";
import { BackupRunHistory } from "@/components/backup-run-history";
import { CollectionPicker } from "@/components/collection-picker";
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
  Activity,
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
  Search,
  Server,
  Tag,
  UserPlus,
  Users,
  Upload,
} from "lucide-react";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { PageHeader } from "@/components/ui/page-header";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { TabBar } from "@/components/ui/tabs";
import { inputClasses } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Localized } from "@/components/ui/localized";
import { translateUiText } from "@/lib/locale";
import { cn } from "@/lib/utils";
import { useRouter, useSearchParams } from "@/lib/navigation";
import { CURRENCY_OPTIONS } from "@/lib/currency";
import { ExternalLibrariesPanel } from "@/components/external-libraries-panel";
import { StorageInventoryPanel } from "@/components/storage-inventory-panel";
import { ArtifactCacheCard } from "@/components/artifact-cache-card";
import { StorageConfigCard } from "@/components/storage-config-card";
import { ImportCopyWarning } from "@/components/import-copy-warning";
import { VaultMigrationPanel } from "@/components/vault-migration-panel";
import { RemoteStorageConnections } from "@/components/remote-storage-connections";
import { MakerWorldConnectCard } from "@/components/makerworld-connect-card";
import { ProviderConnectionsPanel } from "@/components/provider-connections-panel";
import { NotificationsPanel } from "@/components/notifications-panel";
import { SpoolmanConnectCard } from "@/components/spoolman-connect-card";
import { OidcSettingsCard } from "@/components/oidc-settings-card";
import { AiSearchSettings } from "@/components/ai-search-settings";
import { MaintenancePanel } from "@/components/maintenance-panel";
import { BackgroundWorkPanel } from "@/components/background-work-panel";
import { BrandMark } from "@/components/brand-mark";
import {
  createGcPlan,
  approveGcPlan,
  abortGcPlan,
  finalizeGcPlan,
  downloadModelExport,
  downloadLibraryArchive,
  importLibraryArchive,
  regenerateDerivatives,
  getHealthDetails,
  getActiveGcPlan,
  getLatestRelease,
  getVaultConfig,
  listTrash,
  purgeModel,
  restoreModel,
  restartPrintStash,
  updateVaultConfig,
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
import { usePrinters, useVaultStats, useVaultConfig } from "@/lib/queries";
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
import {
  prepareBrowserExtensionSetup,
  discardBrowserExtensionSetup,
  type BrowserExtensionSetup,
} from "@/lib/browser-extension-setup";
import type {
  CollectionNodeRead,
  CollectionRole,
  PrinterRole,
  StorageCleanupStatus,
  StorageHealthRead,
  StorageOperations,
  HealthResponse,
  TrashPurgeRead,
  TrashedModelRead,
  UserUpdate,
} from "@/types";

type SettingsSection =
  | "overview"
  | "access"
  | "storage"
  | "backup"
  | "remote-storage"
  | "imports"
  | "maintenance"
  | "work"
  | "ai-search"
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
  { id: "ai-search", labelKey: "aiSearch.settingsTitle", icon: Search },
  { id: "maintenance", labelKey: "settings.maintenance", icon: HeartPulse },
  { id: "work", labelKey: "settings.backgroundWork", icon: Activity },
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
  return `${formatNumber(value, { maximumFractionDigits: value >= 10 || exponent === 0 ? 0 : 1 })} ${units[exponent]}`;
}

function formatDate(value: string | null | undefined): string {
  if (!value) return uiText("Never");
  return new Intl.DateTimeFormat(currentLocale(), {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(value));
}

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat(currentLocale(), {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function parseBackupRetentionDays(value: string): number | null {
  if (!/^\d+$/.test(value)) return null;
  const days = Number(value);
  return days <= 365 ? days : null;
}

function backupSourceDescription(backup: BackupMeta, t: ReturnType<typeof useI18n>["t"]): string {
  const source = backup.source_ref ?? "legacy source identity";
  const namespace = backup.namespace
    ? uiText(" · namespace {value1}", { value1: String(backup.namespace) })
    : "";
  const hash = backup.archive_sha256 ? ` · SHA-256 ${backup.archive_sha256.slice(0, 16)}…` : "";
  return t("settings.backupExactSource", {
    source: `${backup.location} · ${source}${namespace}${hash}`,
  });
}

function restoreSourceDescription(backup: BackupMeta, t: ReturnType<typeof useI18n>["t"]): string {
  return `${t("settings.backupRestoreWarning")} ${backupSourceDescription(backup, t)}`;
}

function shortOpaque(value: string | null | undefined): string {
  return value ? `${value.slice(0, 16)}…` : uiText("unavailable");
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
  useUiLocale();
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
  useUiLocale();
  const { user } = useAuth();
  const { locale, t } = useI18n();
  const router = useRouter();
  const searchParams = useSearchParams();
  const latestRelease = CHANGELOG[0];
  // `?section=` is the single source of truth for the open section, so it is read
  // during render; mirroring it into state needed an effect to re-sync on every
  // deep link, back button, and replace.
  const activeSection = settingsSection(searchParams.get("section"));
  const mobileTabsRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const tabs = mobileTabsRef.current?.querySelector<HTMLElement>('[role="tablist"]');
    const selected = tabs?.querySelector<HTMLElement>('[data-active="true"]');
    if (tabs && selected) {
      tabs.scrollLeft = selected.offsetLeft - (tabs.clientWidth - selected.clientWidth) / 2;
    }
  }, [activeSection, user?.is_superuser]);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [releaseStatus, setReleaseStatus] = useState<ReleaseStatus | null>(null);
  const [releaseChecking, setReleaseChecking] = useState(false);
  // Vault totals refresh automatically when models change (model writes
  // invalidate queryKeys.vaultStats), so no manual refetch on this screen.
  const stats = useVaultStats().data ?? null;
  const [exporting, setExporting] = useState<"json" | "csv" | null>(null);
  const [archiveBusy, setArchiveBusy] = useState<"export" | "import" | null>(null);
  const apiKeysQuery = useQuery({
    ...apiKeysOptions(user?.id ?? null),
    enabled: user !== null && activeSection === "access",
  });
  const usersQuery = useQuery({
    ...adminUsersOptions(user?.is_superuser ? user.id : null),
    enabled: !!user?.is_superuser && activeSection === "access",
  });
  const keyCommand = useApiKeyCommand();
  const userCommand = useAdminUserCommand();
  const keysDenied =
    apiKeysQuery.isError && [401, 403, 404].includes(parseApiError(apiKeysQuery.error).status);
  const usersDenied =
    usersQuery.isError && [401, 403, 404].includes(parseApiError(usersQuery.error).status);
  const apiKeys = user && !keysDenied ? (apiKeysQuery.data ?? []) : [];
  const users = user?.is_superuser && !usersDenied ? (usersQuery.data ?? []) : [];
  const usersBusy = userCommand.isPending
    ? userCommand.variables.kind === "create"
      ? "create"
      : userCommand.variables.id
    : null;
  const [createUserDraft, setCreateUserDraft] = useState({ username: "", email: "", password: "" });
  const { username: newUsername, email: newUserEmail, password: newUserPassword } = createUserDraft;
  const [passwordDrafts, setPasswordDrafts] = useState<Record<number, string>>({});
  const [accessCollection, setAccessCollection] = useState<CollectionNodeRead | null>(null);
  const [accessPickerOpen, setAccessPickerOpen] = useState(false);
  const [chosenAccessUserId, setAccessUserId] = useState<number | "">("");
  const accessUserId = users.some((row) => row.id === chosenAccessUserId && !row.is_superuser)
    ? chosenAccessUserId
    : "";
  const [accessRole, setAccessRole] = useState<CollectionRole>("view");
  const collectionCommand = useCollectionAccessCommand();
  const printerCommand = usePrinterAccessCommand();
  const accessBusy = collectionCommand.isPending
    ? collectionCommand.variables.kind === "grant"
      ? "save"
      : `${collectionCommand.variables.collectionId}:${collectionCommand.variables.targetUserId}`
    : null;
  const accessCollectionId = accessCollection?.id ?? null;
  const accessActorId = user?.is_superuser ? user.id : null;
  const collectionPermissionsQuery = useQuery({
    ...collectionAccessOptions(accessActorId, accessCollectionId),
    enabled: accessActorId !== null && accessCollectionId !== null && activeSection === "access",
  });
  const collectionPermissions =
    collectionPermissionsQuery.isError && accessDenied(collectionPermissionsQuery.error)
      ? []
      : (collectionPermissionsQuery.data ?? []);
  const accessPrintersQuery = usePrinters({
    enabled: accessActorId !== null && activeSection === "access",
  });
  const accessPrinters =
    accessPrintersQuery.isError && accessDenied(accessPrintersQuery.error)
      ? []
      : (accessPrintersQuery.data ?? []);
  const [chosenPrinterAccessUserId, setPrinterAccessUserId] = useState<number | "">("");
  const printerAccessUserId = users.some(
    (row) => row.id === chosenPrinterAccessUserId && !row.is_superuser,
  )
    ? chosenPrinterAccessUserId
    : "";
  const [accessPrinterId, setAccessPrinterId] = useState<number | "">("");
  const [printerAccessRole, setPrinterAccessRole] = useState<PrinterRole>("view");
  const printerAccess = usePrinterAccess(
    accessActorId,
    accessPrinters,
    activeSection === "access" &&
      !!printerAccessUserId &&
      !usersQuery.isError &&
      !usersQuery.isPending &&
      !accessPrintersQuery.isError,
  );
  const printerPermissions = printerAccess.flatMap(({ query }) =>
    query.isError && accessDenied(query.error) ? [] : (query.data ?? []),
  );
  const selectedPrinterRead = printerAccess.find(
    ({ printer }) => printer.id === accessPrinterId,
  )?.query;
  const printerAccessBusy = printerCommand.isPending
    ? printerCommand.variables.kind === "grant"
      ? "save"
      : `${printerCommand.variables.printerId}:${printerCommand.variables.targetUserId}`
    : null;
  const [ownedReceipt, setOwnedReceipt] = useState<
    (ApiKeyReceipt & { handoff: BrowserExtensionSetup | null }) | null
  >(null);
  const receipt =
    user &&
    !keysDenied &&
    ownedReceipt?.userId === user.id &&
    ownedReceipt.session === getSessionVersion()
      ? ownedReceipt
      : null;
  const newApiKey = receipt?.secret ?? null;
  const [keyName, setKeyName] = useState("Programmatic access");
  const keyBusy = keyCommand.isPending;
  const extensionSetupReady = receipt?.handoff !== null && receipt?.handoff !== undefined;
  const accountLive = useRef(true);
  const accountIdentity = useRef(user?.id ?? null);
  useEffect(() => {
    accountIdentity.current = user?.id ?? null;
  }, [user?.id]);
  function accountCurrent(session: number) {
    return (
      accountLive.current && session === getSessionVersion() && accountIdentity.current === user?.id
    );
  }
  function dismissReceipt() {
    if (receipt?.handoff) discardBrowserExtensionSetup(receipt.handoff);
    setOwnedReceipt(null);
  }
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
  const remoteConfig = useVaultConfig({
    enabled: !!user?.is_superuser && ["design", "previews", "backup"].includes(activeSection),
    retry: false,
  });
  const configCommand = useVaultConfigCommand();
  const configDenied =
    remoteConfig.isError && [401, 403, 404].includes(parseApiError(remoteConfig.error).status);
  const remoteConfigData = configDenied ? undefined : remoteConfig.data;
  const [autoMarkChoice, setAutoMarkKnownGood] = useState<boolean | null>(null);
  const autoMarkKnownGood = autoMarkChoice ?? remoteConfigData?.auto_mark_known_good ?? false;
  const [autoMarkBusy, setAutoMarkBusy] = useState(false);
  const [currencyChoice, setCurrency] = useState<string | null>(null);
  const currency = currencyChoice ?? remoteConfigData?.currency ?? "";
  const [currencyBusy, setCurrencyBusy] = useState(false);
  // Each reader falls back to its defaults when there is no `window`, so these are
  // safe as lazy initialisers on the server as well as in the browser.
  const [previewPreferences, setPreviewPreferences] = useState(readPreviewPreferences);
  const [thumbnailChoice, setModelThumbnailWidth] = useState<number | null>(null);
  const modelThumbnailWidth = thumbnailChoice ?? remoteConfigData?.model_thumbnail_width ?? 640;
  const [previewBusy, setPreviewBusy] = useState<"quality" | "rebuild" | null>(null);
  const [purgeTarget, setPurgeTarget] = useState<number | null>(null);
  const [purgeExpiredOpen, setPurgeExpiredOpen] = useState(false);
  const [trashStorageTier, setTrashStorageTier] = useState("verified");
  const [trashOperations, setTrashOperations] = useState<StorageOperations>();
  const backupCommand = useBackupCommand();
  const backupConnectionCommand = useStorageConnectionCommand();
  const [backingUp, setBackingUp] = useState(false);
  const backupEnabled = !!user?.is_superuser && activeSection === "backup";
  const ownedBackupRead = useQuery({ ...backupSourcesOptions(), enabled: backupEnabled });
  const localBackupRead = useQuery({ ...unownedLocalBackupsOptions(), enabled: backupEnabled });
  const s3BackupRead = useQuery({ ...unownedS3BackupsOptions(), enabled: backupEnabled });
  const remoteBackupRead = useQuery({ ...unownedRemoteBackupsOptions(), enabled: backupEnabled });
  const backupConnectionRead = useQuery({ ...storageConnectionsOptions(), enabled: backupEnabled });
  const backupsDenied =
    ownedBackupRead.isError &&
    [401, 403, 404].includes(parseApiError(ownedBackupRead.error).status);
  const backups = user?.is_superuser && !backupsDenied ? (ownedBackupRead.data ?? []) : [];
  const unownedBackups =
    user?.is_superuser && !localBackupRead.isError ? (localBackupRead.data ?? []) : [];
  const unownedS3Backups =
    user?.is_superuser && !s3BackupRead.isError ? (s3BackupRead.data ?? []) : [];
  const unownedRemoteBackups =
    user?.is_superuser && !remoteBackupRead.isError ? (remoteBackupRead.data ?? []) : [];
  const backupsLoading = [ownedBackupRead, localBackupRead, s3BackupRead, remoteBackupRead].some(
    (read) => read.isFetching,
  );
  const backupDiscoveryFailed =
    localBackupRead.isError || s3BackupRead.isError || remoteBackupRead.isError;
  const backupConfigUnavailable = !remoteConfigData || remoteConfig.isError;
  // Only deliberate edits live in these drafts; refreshed DTOs stay in Query.
  const [backupRetentionDraft, setBackupRetentionDays] = useState<string | null>(null);
  const backupRetentionDays =
    backupRetentionDraft ?? String(remoteConfigData?.backup_retention_days ?? 30);
  const parsedBackupRetentionDays = parseBackupRetentionDays(backupRetentionDays);
  const [backupRetentionBusy, setBackupRetentionBusy] = useState(false);
  const [backupConnectionDrafts, setBackupConnectionDrafts] = useState<
    Record<
      number,
      Partial<Pick<StorageConnection, "manual_backup_enabled" | "automatic_backup_enabled">>
    >
  >({});
  const backupConnections = !backupConnectionRead.isError
    ? (backupConnectionRead.data ?? [])
        .filter((connection) => connection.purpose === "backup" || connection.purpose === "both")
        .map((connection) => ({ ...connection, ...backupConnectionDrafts[connection.id] }))
    : [];
  const [automaticBackupsDraft, setAutomaticBackupsEnabled] = useState<boolean | null>(null);
  const automaticBackupsEnabled =
    automaticBackupsDraft ?? remoteConfigData?.automatic_backups_enabled ?? false;
  const [automaticBackupTimeDraft, setAutomaticBackupTimeUtc] = useState<string | null>(null);
  const automaticBackupTimeUtc =
    automaticBackupTimeDraft ?? remoteConfigData?.automatic_backup_time_utc ?? "02:00";
  const [manualLocalBackupDraft, setManualLocalBackupEnabled] = useState<boolean | null>(null);
  const manualLocalBackupEnabled =
    manualLocalBackupDraft ?? remoteConfigData?.manual_local_backup_enabled ?? true;
  const [automaticLocalBackupDraft, setAutomaticLocalBackupEnabled] = useState<boolean | null>(
    null,
  );
  const automaticLocalBackupEnabled =
    automaticLocalBackupDraft ?? remoteConfigData?.automatic_local_backup_enabled ?? true;
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
  useEffect(() => {
    accountLive.current = true;
    const release = onAuthChange(() => {
      setOwnedReceipt(null);
      setAccessCollection(null);
      setAccessPickerOpen(false);
      setAccessUserId("");
      setAccessRole("view");
      setPrinterAccessUserId("");
      setAccessPrinterId("");
      setPrinterAccessRole("view");
      setAutoMarkKnownGood(null);
      setCurrency(null);
      setModelThumbnailWidth(null);
      setRestoreTarget(null);
      setDeleteBackupTarget(null);
      setAdoptTarget(null);
      setAdoptS3Target(null);
      setAdoptRemoteTarget(null);
      setBackupRetentionDays(null);
      setAutomaticBackupsEnabled(null);
      setAutomaticBackupTimeUtc(null);
      setManualLocalBackupEnabled(null);
      setAutomaticLocalBackupEnabled(null);
      setBackupConnectionDrafts({});
    });
    return () => {
      accountLive.current = false;
      release();
    };
  }, []);
  const visibleSettingsSections = SETTINGS_SECTIONS.filter(
    (section) =>
      !["sso", "maintenance", "work", "ai-search"].includes(section.id) || user?.is_superuser,
  );

  function changeSection(section: SettingsSection) {
    const params = new URLSearchParams(searchParams.toString());
    if (section === "overview") params.delete("section");
    else params.set("section", section);
    const query = params.toString();
    router.replace(query ? `/settings?${query}` : "/settings");
  }

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
  }, [user, setTrashRetentionDays]);

  useEffect(() => {
    if (activeSection === "trash") {
      // Opening the Trash section is what starts the listing fetch; `loadTrash` writes the
      // items only after awaiting the API, and synchronously sets just its loading flag.
      // oxlint-disable-next-line react/set-state-in-effect -- fetch-on-open of an external listing
      loadTrash();
    }
  }, [activeSection, loadTrash]);

  async function loadBackups() {
    if (!user?.is_superuser) return;
    await Promise.all([
      ownedBackupRead.refetch(),
      localBackupRead.refetch(),
      s3BackupRead.refetch(),
      remoteBackupRead.refetch(),
      remoteConfig.refetch(),
      backupConnectionRead.refetch(),
    ]);
  }

  async function confirmAdoptBackup() {
    if (!adoptTarget) return;
    const target = adoptTarget;
    const session = getSessionVersion();
    setAdoptingBackup(true);
    try {
      await backupCommand.run({ kind: "adopt-local", session, target });
      if (!accountCurrent(session)) return;
      toast.success(t("settings.backupLegacyAdopted", { filename: target.filename }));
      setAdoptTarget(null);
    } catch (error) {
      if (accountCurrent(session)) toast.error(error);
    } finally {
      if (accountCurrent(session)) setAdoptingBackup(false);
    }
  }

  async function confirmAdoptS3Backup() {
    if (!adoptS3Target) return;
    const target = adoptS3Target;
    const session = getSessionVersion();
    setAdoptingS3Backup(true);
    try {
      await backupCommand.run({ kind: "adopt-s3", session, target });
      if (!accountCurrent(session)) return;
      toast.success(t("settings.backupLegacyAdopted", { filename: target.key }));
      setAdoptS3Target(null);
    } catch (error) {
      if (accountCurrent(session)) toast.error(error);
    } finally {
      if (accountCurrent(session)) setAdoptingS3Backup(false);
    }
  }

  async function confirmAdoptRemoteBackup() {
    if (!adoptRemoteTarget) return;
    const target = adoptRemoteTarget;
    const session = getSessionVersion();
    setAdoptingRemoteBackup(true);
    try {
      await backupCommand.run({ kind: "adopt-remote", session, target });
      if (!accountCurrent(session)) return;
      toast.success(t("settings.backupLegacyAdopted", { filename: target.key }));
      setAdoptRemoteTarget(null);
    } catch (error) {
      if (accountCurrent(session)) toast.error(error);
    } finally {
      if (accountCurrent(session)) setAdoptingRemoteBackup(false);
    }
  }

  async function saveAutoMarkKnownGood(next: boolean) {
    if (!user?.is_superuser || !remoteConfigData || remoteConfig.isError || configCommand.isPending)
      return;
    const session = getSessionVersion();
    setAutoMarkKnownGood(next);
    setAutoMarkBusy(true);
    try {
      await configCommand.mutateAsync({ session, payload: { auto_mark_known_good: next } });
      if (!accountCurrent(session)) return;
      setAutoMarkKnownGood(null);
      toast.success(
        next ? uiText("Auto-mark known good enabled.") : uiText("Auto-mark known good disabled."),
      );
    } catch (error) {
      if (accountCurrent(session)) {
        setAutoMarkKnownGood(null);
        toast.error(error);
      }
    } finally {
      if (accountCurrent(session)) setAutoMarkBusy(false);
    }
  }
  async function saveCurrency(next: string) {
    if (!user?.is_superuser || !remoteConfigData || remoteConfig.isError || configCommand.isPending)
      return;
    const session = getSessionVersion();
    setCurrency(next);
    setCurrencyBusy(true);
    try {
      await configCommand.mutateAsync({ session, payload: { currency: next } });
      if (!accountCurrent(session)) return;
      setCurrency(null);
      toast.success(uiText("Currency set to {value1}.", { value1: next }));
    } catch (error) {
      if (accountCurrent(session)) {
        setCurrency(null);
        toast.error(error);
      }
    } finally {
      if (accountCurrent(session)) setCurrencyBusy(false);
    }
  }

  function savePreviewPreference(patch: Partial<PreviewPreferences>) {
    const next = { ...previewPreferences, ...patch };
    setPreviewPreferences(next);
    writePreviewPreferences(next);
    toast.success(uiText("Preview settings saved for this browser."));
  }

  async function saveModelThumbnailWidth(next: ModelThumbnailWidth) {
    if (!user?.is_superuser || !remoteConfigData || remoteConfig.isError || configCommand.isPending)
      return;
    const session = getSessionVersion();
    setModelThumbnailWidth(next);
    setPreviewBusy("quality");
    try {
      await configCommand.mutateAsync({ session, payload: { model_thumbnail_width: next } });
      if (!accountCurrent(session)) return;
      setModelThumbnailWidth(null);
      toast.success(uiText("Model image quality updated for new previews."));
    } catch (error) {
      if (accountCurrent(session)) {
        setModelThumbnailWidth(null);
        toast.error(error);
      }
    } finally {
      if (accountCurrent(session)) setPreviewBusy(null);
    }
  }

  async function recreateModelImages() {
    setPreviewBusy("rebuild");
    try {
      // Every current preview stays visible until its replacement is ready.
      await regenerateDerivatives("thumbnail", "all");
      toast.success(uiText("Model preview recreation queued. Follow it in Background work."));
    } catch (e) {
      toast.error(e);
    } finally {
      setPreviewBusy(null);
    }
  }

  async function handleBackupNow() {
    const session = getSessionVersion();
    setBackingUp(true);
    try {
      const receipt = await backupCommand.run({ kind: "create", session });
      if (!accountCurrent(session)) return;
      if (receipt.kind !== "saved") throw new Error("Expected a created backup receipt");
      const meta = receipt.backup;
      const mb = formatNumber(meta.size_bytes / 1024 / 1024, {
        maximumFractionDigits: 1,
        minimumFractionDigits: 1,
      });
      if (meta.outcome === "partial") toast.warning(t("settings.backupPartialNotice"));
      else
        toast.success(
          uiText("Backup created — {value1} files, {value2} MB", {
            value1: String(meta.file_count),
            value2: mb,
          }),
        );
    } catch (error) {
      if (accountCurrent(session)) toast.error(error);
    } finally {
      if (accountCurrent(session)) setBackingUp(false);
    }
  }

  async function handleBackupUpload(file: File) {
    const session = getSessionVersion();
    setUploadingBackup(true);
    try {
      const receipt = await backupCommand.run({ kind: "upload", session, file });
      if (!accountCurrent(session)) return;
      if (receipt.kind !== "saved") throw new Error("Expected an uploaded backup receipt");
      toast.success(
        uiText("Backup uploaded — {value1} files", { value1: String(receipt.backup.file_count) }),
      );
    } catch (error) {
      if (accountCurrent(session)) toast.error(error);
    } finally {
      if (accountCurrent(session)) setUploadingBackup(false);
    }
  }

  async function saveBackupRetention() {
    if (parsedBackupRetentionDays === null || backupConfigUnavailable || configCommand.isPending)
      return;
    const session = getSessionVersion();
    const sent = backupRetentionDraft;
    setBackupRetentionBusy(true);
    try {
      await configCommand.mutateAsync({
        session,
        payload: { backup_retention_days: parsedBackupRetentionDays },
      });
      if (!accountCurrent(session)) return;
      setBackupRetentionDays((current) => (current === sent ? null : current));
      toast.success(t("settings.backupRetentionSaved"));
    } catch (error) {
      if (accountCurrent(session)) toast.error(error);
    } finally {
      if (accountCurrent(session)) setBackupRetentionBusy(false);
    }
  }

  function setBackupConnectionSelection(
    connectionId: number,
    field: "manual_backup_enabled" | "automatic_backup_enabled",
    value: boolean,
  ) {
    setBackupConnectionDrafts((current) => ({
      ...current,
      [connectionId]: { ...current[connectionId], [field]: value },
    }));
  }

  async function saveBackupPolicy() {
    if (
      !user?.is_superuser ||
      backupConfigUnavailable ||
      backupConnectionRead.isError ||
      !backupConnectionRead.data ||
      configCommand.isPending ||
      backupConnectionCommand.isPending ||
      backupPolicyBusy
    )
      return;
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
    const session = getSessionVersion();
    const sent = {
      enabled: automaticBackupsDraft,
      time: automaticBackupTimeDraft,
      manual: manualLocalBackupDraft,
      automatic: automaticLocalBackupDraft,
      connections: backupConnectionDrafts,
    };
    setBackupPolicyBusy(true);
    try {
      // These are separate server transactions. Publish every acknowledged part
      // through its owner before moving on; a later failure cannot undo it.
      await configCommand.mutateAsync({
        session,
        payload: {
          automatic_backups_enabled: automaticBackupsEnabled,
          automatic_backup_time_utc: automaticBackupTimeUtc,
          manual_local_backup_enabled: manualLocalBackupEnabled,
          automatic_local_backup_enabled: automaticLocalBackupEnabled,
        },
      });
      if (!accountCurrent(session)) return;
      setAutomaticBackupsEnabled((current) => (current === sent.enabled ? null : current));
      setAutomaticBackupTimeUtc((current) => (current === sent.time ? null : current));
      setManualLocalBackupEnabled((current) => (current === sent.manual ? null : current));
      setAutomaticLocalBackupEnabled((current) => (current === sent.automatic ? null : current));
      for (const connection of backupConnections) {
        await backupConnectionCommand.mutateAsync({
          kind: "update",
          session,
          id: connection.id,
          payload: {
            manual_backup_enabled: connection.manual_backup_enabled,
            automatic_backup_enabled: connection.automatic_backup_enabled,
          },
        });
        if (!accountCurrent(session)) return;
        setBackupConnectionDrafts((current) => {
          if (current[connection.id] !== sent.connections[connection.id]) return current;
          const next = { ...current };
          delete next[connection.id];
          return next;
        });
      }
      toast.success(t("settings.backupPolicySaved"));
    } catch (error) {
      if (accountCurrent(session)) toast.error(error);
    } finally {
      if (accountCurrent(session)) setBackupPolicyBusy(false);
    }
  }

  async function confirmRestoreBackup() {
    if (!restoreTarget) return;
    const session = getSessionVersion();
    setRestoringBackup(true);
    try {
      const receipt = await backupCommand.run({ kind: "restore", session, target: restoreTarget });
      if (!accountCurrent(session)) return;
      if (receipt.kind !== "restored") throw new Error("Expected a restored backup receipt");
      toast.success(
        uiText("Backup restored — {value1} files", {
          value1: String(receipt.result.restored_files),
        }),
      );
      setRestoreTarget(null);
      window.setTimeout(() => {
        if (accountCurrent(session)) window.location.reload();
      }, 800);
    } catch (error) {
      if (accountCurrent(session)) toast.error(error);
    } finally {
      if (accountCurrent(session)) setRestoringBackup(false);
    }
  }

  async function handleDownloadBackup(backup: BackupMeta) {
    const session = getSessionVersion();
    setDownloadingBackup(backupSourceKey(backup));
    try {
      await backupCommand.run({ kind: "download", session, target: backup });
      if (accountCurrent(session)) toast.success(uiText("Backup download started."));
    } catch (error) {
      if (accountCurrent(session)) toast.error(error);
    } finally {
      if (accountCurrent(session)) setDownloadingBackup(null);
    }
  }

  async function confirmDeleteBackup() {
    if (!deleteBackupTarget) return;
    const session = getSessionVersion();
    setDeletingBackup(backupSourceKey(deleteBackupTarget));
    try {
      await backupCommand.run({ kind: "delete", session, target: deleteBackupTarget });
      if (!accountCurrent(session)) return;
      setDeleteBackupTarget(null);
      toast.success(t("settings.backupDeleteSuccess"));
    } catch (error) {
      if (accountCurrent(session)) toast.error(error);
    } finally {
      if (accountCurrent(session)) setDeletingBackup(null);
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
      await downloadLibraryArchive(2);
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
      toast.success(
        uiText("Library import queued ({value1}). Follow it in activity.", {
          value1: String(result.job_id.slice(0, 8)),
        }),
      );
    } catch (e) {
      toast.error(e);
    } finally {
      setArchiveBusy(null);
    }
  }

  async function issueApiKey(extension: boolean) {
    if (!user || keyBusy || receipt || apiKeysQuery.isError || apiKeysQuery.isPending) return;
    const session = getSessionVersion();
    const username = user.username;
    let handoffPrepared = false;
    try {
      await keyCommand.mutateAsync({
        kind: "create",
        userId: user.id,
        session,
        name: extension ? "Browser extension" : keyName.trim() || "Programmatic access",
        receiveReceipt: (result) => {
          if (!accountCurrent(session)) return;
          setOwnedReceipt({ ...result, handoff: null });
          if (extension) {
            try {
              const handoff = prepareBrowserExtensionSetup(
                window.location.origin,
                username,
                result.secret,
              );
              handoffPrepared = true;
              setOwnedReceipt({ ...result, handoff });
            } catch {
              toast.error(t("settings.accountHandoffFailed"));
            }
          }
        },
      });
      if (!accountCurrent(session)) return;
      toast.success(
        handoffPrepared
          ? uiText("Extension setup prepared. Open the browser extension on this tab.")
          : uiText("API key created. Copy it now; it will not be shown again."),
      );
    } catch (error) {
      if (accountCurrent(session)) toast.error(error);
    }
  }
  function generateApiKey() {
    return issueApiKey(false);
  }
  function setupBrowserExtension() {
    return issueApiKey(true);
  }
  async function deleteApiKey(id: number) {
    if (!user || keyBusy || apiKeysQuery.isError || apiKeysQuery.isPending) return;
    const session = getSessionVersion();
    try {
      await keyCommand.mutateAsync({ kind: "revoke", id, userId: user.id, session });
      if (!accountCurrent(session)) return;
      if (receipt?.keyId === id) dismissReceipt();
      toast.success(uiText("API key revoked."));
    } catch (error) {
      if (accountCurrent(session)) toast.error(error);
    }
  }

  async function copyApiKey() {
    if (!newApiKey) return;
    await navigator.clipboard.writeText(newApiKey);
    toast.success(uiText("API key copied."));
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
    toast.success(uiText("OrcaSlicer command copied."));
  }

  async function saveCollectionAccess() {
    if (
      !user?.is_superuser ||
      usersQuery.isError ||
      usersQuery.isPending ||
      collectionPermissionsQuery.isError ||
      collectionPermissionsQuery.isPending ||
      accessBusy !== null ||
      !accessUserId ||
      !accessCollection
    )
      return;
    const session = getSessionVersion();
    try {
      await collectionCommand.mutateAsync({
        kind: "grant",
        actorId: user.id,
        session,
        collectionId: accessCollection.id,
        targetUserId: accessUserId,
        role: accessRole,
      });
      if (accountCurrent(session)) toast.success(uiText("Collection access saved."));
    } catch (error) {
      if (accountCurrent(session)) toast.error(error);
    }
  }
  async function removeCollectionAccess(collectionId: number, targetUserId: number) {
    if (
      !user?.is_superuser ||
      usersQuery.isError ||
      usersQuery.isPending ||
      collectionPermissionsQuery.isError ||
      collectionPermissionsQuery.isPending ||
      accessBusy !== null
    )
      return;
    const session = getSessionVersion();
    try {
      await collectionCommand.mutateAsync({
        kind: "revoke",
        actorId: user.id,
        session,
        collectionId,
        targetUserId,
      });
      if (accountCurrent(session)) toast.success(uiText("Collection access removed."));
    } catch (error) {
      if (accountCurrent(session)) toast.error(error);
    }
  }
  async function savePrinterAccess() {
    if (
      !user?.is_superuser ||
      usersQuery.isError ||
      usersQuery.isPending ||
      accessPrintersQuery.isError ||
      selectedPrinterRead?.isError ||
      !selectedPrinterRead?.isSuccess ||
      printerAccessBusy !== null ||
      !printerAccessUserId ||
      !accessPrinterId
    )
      return;
    const session = getSessionVersion();
    try {
      await printerCommand.mutateAsync({
        kind: "grant",
        actorId: user.id,
        session,
        printerId: accessPrinterId,
        targetUserId: printerAccessUserId,
        role: printerAccessRole,
      });
      if (accountCurrent(session)) toast.success(uiText("Printer access saved."));
    } catch (error) {
      if (accountCurrent(session)) toast.error(error);
    }
  }
  async function removePrinterAccess(printerId: number, targetUserId: number) {
    const read = printerAccess.find(({ printer }) => printer.id === printerId)?.query;
    if (
      !user?.is_superuser ||
      usersQuery.isError ||
      usersQuery.isPending ||
      accessPrintersQuery.isError ||
      read?.isError ||
      !read?.isSuccess ||
      printerAccessBusy !== null
    )
      return;
    const session = getSessionVersion();
    try {
      await printerCommand.mutateAsync({
        kind: "revoke",
        actorId: user.id,
        session,
        printerId,
        targetUserId,
      });
      if (accountCurrent(session)) toast.success(uiText("Printer access removed."));
    } catch (error) {
      if (accountCurrent(session)) toast.error(error);
    }
  }
  async function refreshPrinterAccess() {
    const session = getSessionVersion();
    await accessPrintersQuery.refetch();
    if (!accountCurrent(session)) return;
    await Promise.all(printerAccess.map(({ query }) => query.refetch()));
  }

  async function createUser() {
    if (!user?.is_superuser || usersBusy !== null || usersQuery.isError || usersQuery.isPending)
      return;
    const captured = createUserDraft;
    const username = captured.username.trim();
    const password = captured.password.trim();
    if (!username || password.length < 8) return;
    const session = getSessionVersion();
    try {
      await userCommand.mutateAsync({
        kind: "create",
        userId: user.id,
        session,
        payload: { username, password, email: captured.email.trim() || null },
      });
      if (!accountCurrent(session)) return;
      setCreateUserDraft((current) =>
        current === captured ? { username: "", email: "", password: "" } : current,
      );
      toast.success(uiText("User created."));
    } catch (error) {
      if (accountCurrent(session)) toast.error(error);
    }
  }
  async function patchUser(id: number, payload: UserUpdate) {
    if (!user?.is_superuser || usersBusy !== null || usersQuery.isError || usersQuery.isPending)
      return;
    const session = getSessionVersion();
    try {
      await userCommand.mutateAsync({ kind: "update", id, payload, userId: user.id, session });
      if (accountCurrent(session)) toast.success(uiText("User updated."));
    } catch (error) {
      if (accountCurrent(session)) toast.error(error);
    }
  }
  async function resetUserPassword(id: number) {
    if (!user?.is_superuser || usersBusy !== null || usersQuery.isError || usersQuery.isPending)
      return;
    const captured = passwordDrafts[id];
    const password = captured?.trim();
    if (!password || password.length < 8) return;
    const session = getSessionVersion();
    try {
      await userCommand.mutateAsync({ kind: "password", id, password, userId: user.id, session });
      if (!accountCurrent(session)) return;
      setPasswordDrafts((current) =>
        current[id] === captured ? { ...current, [id]: "" } : current,
      );
      toast.success(uiText("Password reset."));
    } catch (error) {
      if (accountCurrent(session)) toast.error(error);
    }
  }
  async function deactivateUser(id: number) {
    if (!user?.is_superuser || usersBusy !== null || usersQuery.isError || usersQuery.isPending)
      return;
    const session = getSessionVersion();
    try {
      await userCommand.mutateAsync({ kind: "deactivate", id, userId: user.id, session });
      if (accountCurrent(session)) toast.success(uiText("User deactivated."));
    } catch (error) {
      if (accountCurrent(session)) toast.error(error);
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
    toast.success(uiText("Metadata display reset."));
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
    toast.success(uiText("Card metrics reset."));
  }

  function updatePrinterCardImagePreference(next: boolean) {
    writePrinterCardImagePreference(next);
    toast.success(
      next ? uiText("Printer card images enabled.") : uiText("Printer card images hidden."),
    );
  }

  async function saveTrashRetention() {
    setTrashBusy("settings");
    try {
      await updateVaultConfig({ trash_retention_days: Math.max(-1, trashRetentionDays) });
      toast.success(uiText("Trash retention updated."));
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
      toast.success(uiText("Model restored."));
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
        uiText("gc.previewCreated", {
          value1: String(plan.resource_count),
          count: Number(plan.resource_count),
        }),
      );
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.code === "gc_plan_active") {
        try {
          const activePlan = await getActiveGcPlan();
          if (activePlan === null) throw e;
          setGcPlan(activePlan);
          setGcDigestConfirmation("");
        } catch (readError) {
          toast.error(readError);
        }
      } else {
        toast.error(e);
      }
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
      toast.success(uiText("Backup verified. The plan is now in its recovery quarantine."));
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
      toast.success(uiText("GC plan aborted. Every candidate remains in the trash."));
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
      toast.success(uiText("GC plan finalized after all safety evidence was reverified."));
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
      get label() {
        return uiText("Models");
      },
      value: stats ? `${stats.model_count}` : "...",
      desc: "Live library entries",
      icon: Boxes,
    },
    {
      get label() {
        return uiText("Files");
      },
      value: stats ? `${stats.file_count}` : "...",
      desc: uiText("{value1} source · {value2} G-code", {
        value1: String(stats?.source_file_count ?? 0),
        value2: String(stats?.gcode_file_count ?? 0),
      }),
      icon: Files,
    },
    {
      get label() {
        return uiText("Storage used");
      },
      value: stats ? formatBytes(stats.storage.total_size_bytes) : "...",
      desc: stats
        ? uiText("{value1} stored objects", { value1: String(stats.storage.object_count) })
        : "Backend usage",
      icon: HardDrive,
    },
    {
      get label() {
        return uiText("Printers");
      },
      value: stats ? `${stats.printer_count}` : "...",
      desc: "Configured devices",
      icon: Printer,
    },
  ];

  // System detail rows — configuration facts, distinct from the KPI tiles.
  const systemItems = [
    {
      get label() {
        return uiText("Vault version");
      },
      value: health ? `${health.name} v${health.version}` : uiText("Loading..."),
      desc: uiText("API server status and version"),
      icon: Server,
    },
    {
      get label() {
        return uiText("Database");
      },
      value: health?.components?.database
        ? health.components.database.ok
          ? uiText("Connected")
          : uiText("Unavailable")
        : health?.status === "ok"
          ? uiText("Connected")
          : uiText("Unknown"),
      desc: uiText("SQLite by default, Postgres optional"),
      icon: Database,
    },
    {
      get label() {
        return uiText("Storage backend");
      },
      value: stats ? stats.storage.backend.toUpperCase() : "...",
      desc: stats?.storage.bucket ?? stats?.storage.prefix ?? uiText("Configured vault storage"),
      icon: HardDrive,
    },
    {
      get label() {
        return uiText("Indexed files");
      },
      value: stats ? formatBytes(stats.indexed_size_bytes) : "...",
      desc: "Tracked in the database",
      icon: Files,
    },
    {
      get label() {
        return uiText("Collections");
      },
      value: stats ? `${stats.collection_count}` : "...",
      desc: "Hierarchical tree entries",
      icon: FolderTree,
    },
    {
      get label() {
        return uiText("Tags");
      },
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
        <GettingStartedReminder />
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
              ? uiText("Permanently delete?")
              : t("storage.catalogConfirmation")
          }
          description={
            (trashOperations?.physical_delete.allowed ?? trashStorageTier === "verified")
              ? uiText(
                  "This will delete the model and all its files immediately. This cannot be undone.",
                )
              : t("storage.catalogOnly")
          }
          confirmLabel={
            (trashOperations?.physical_delete.allowed ?? trashStorageTier === "verified")
              ? uiText("Delete forever")
              : t("storage.catalogConfirmAction")
          }
        />
        <ConfirmModal
          open={purgeExpiredOpen}
          onClose={() => setPurgeExpiredOpen(false)}
          onConfirm={createExpiredGcPreview}
          busy={trashBusy === "gc"}
          title={uiText("Create a safe GC preview?")}
          description={uiText(
            "This only records a bounded candidate plan. It does not delete catalog rows or storage bytes. Approval later requires the exact digest, verified storage, and a recent independent backup.",
          )}
          confirmLabel={uiText("Create preview")}
        />
        <ConfirmModal
          open={restoreTarget !== null && !backupsDenied && !!user?.is_superuser}
          onClose={() => setRestoreTarget(null)}
          onConfirm={confirmRestoreBackup}
          busy={restoringBackup}
          title={uiText("Restore backup?")}
          description={
            restoreTarget
              ? restoreSourceDescription(restoreTarget, t)
              : t("settings.backupRestoreWarning")
          }
          confirmLabel={uiText("Restore")}
        />
        <ConfirmModal
          open={deleteBackupTarget !== null && !backupsDenied && !!user?.is_superuser}
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
                  namespace: adoptS3Target.namespace ?? uiText("unavailable"),
                  hash: adoptS3Target.archive_sha256?.slice(0, 16) ?? uiText("unavailable"),
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
                  hash: adoptRemoteTarget.archive_sha256?.slice(0, 16) ?? uiText("unavailable"),
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
          title={uiText("Download third-party printer images?")}
          description={uiText(
            "Printer artwork will load from OrcaSlicer's GitHub repository. Images may be copyrighted or trademarked by their creators or printer manufacturers and remain subject to their original licenses. PrintStash does not own or redistribute them. Continue only if this use is permitted where you live.",
          )}
          confirmLabel={uiText("Download & enable")}
        />

        <PageHeader title={t("settings.title")} description={t("settings.description")} />

        <div ref={mobileTabsRef} className="border-b border-border pb-3 lg:hidden">
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
            aria-label={uiText("Settings sections")}
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
                      {uiText("PrintStash v{value1} is available", {
                        value1: String(releaseStatus.latest_version ?? ""),
                      })}
                    </p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {uiText(
                        "This vault is running v{value1}. Review release notes before updating your self-hosted installation.",
                        { value1: String(releaseStatus.current_version ?? "") },
                      )}
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
                  {uiText("View release")}
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
                <ImportCopyWarning storageHealth={storageHealth} />
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
                    title={uiText("System")}
                    description={uiText("Server status and vault configuration")}
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
                    title={uiText("Library migration")}
                    description={uiText(
                      "Portable archive with models, metadata, print history, and original artifacts",
                    )}
                  >
                    <div className="p-4 sm:p-5 space-y-4">
                      <p className="text-sm text-muted-foreground leading-relaxed">
                        {uiText(
                          "Export a versioned archive for migration to another PrintStash installation. Accounts, credentials, settings, and trash are excluded.",
                        )}
                      </p>
                      <div className="flex flex-wrap items-end gap-2">
                        <button
                          type="button"
                          onClick={() => void exportArchive()}
                          disabled={archiveBusy !== null}
                          className={BTN_SECONDARY}
                        >
                          <Download className="h-3.5 w-3.5" />{" "}
                          {archiveBusy === "export"
                            ? uiText("Exporting")
                            : uiText("Export full library")}
                        </button>
                        {user?.is_superuser && (
                          <label
                            className={`${BTN_SECONDARY} ${archiveBusy !== null ? "pointer-events-none opacity-50" : "cursor-pointer"}`}
                          >
                            <Download className="h-3.5 w-3.5 rotate-180" />{" "}
                            {archiveBusy === "import"
                              ? uiText("Importing")
                              : uiText("Import archive")}
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
                    title={uiText("Data export")}
                    description={uiText("Metadata only — no raw STL/3MF/G-code files")}
                  >
                    <div className="p-4 sm:p-5 space-y-4">
                      <p className="text-sm text-muted-foreground leading-relaxed">
                        {uiText(
                          "Download your searchable library context for spreadsheets, audits, migrations, or local AI prompts.",
                        )}
                      </p>
                      <div className="flex flex-wrap gap-2">
                        <button
                          type="button"
                          onClick={() => exportData("json")}
                          disabled={exporting !== null}
                          className={BTN_SECONDARY}
                        >
                          <Download className="h-3.5 w-3.5" />
                          {exporting === "json" ? uiText("Exporting") : "JSON"}
                        </button>
                        <button
                          type="button"
                          onClick={() => exportData("csv")}
                          disabled={exporting !== null}
                          className={BTN_SECONDARY}
                        >
                          <Download className="h-3.5 w-3.5" />
                          {exporting === "csv" ? uiText("Exporting") : uiText("CSV")}
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
                    title={uiText("Users")}
                    description={uiText(
                      "Create users, assign vault admins, disable accounts, and reset passwords.",
                    )}
                  >
                    <div className="p-4 sm:p-5 space-y-4">
                      <div className="grid gap-2 lg:grid-cols-[1fr_1fr_1fr_auto]">
                        <label className="block space-y-1">
                          <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                            {uiText("Username")}
                          </span>
                          <input
                            id="new-user-username"
                            value={newUsername}
                            onChange={(event) =>
                              setCreateUserDraft((current) => ({
                                ...current,
                                username: event.target.value,
                              }))
                            }
                            className={INPUT}
                            maxLength={128}
                            autoComplete="username"
                          />
                        </label>
                        <label className="block space-y-1">
                          <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                            {uiText("Email")}
                          </span>
                          <input
                            id="new-user-email"
                            value={newUserEmail}
                            onChange={(event) =>
                              setCreateUserDraft((current) => ({
                                ...current,
                                email: event.target.value,
                              }))
                            }
                            className={INPUT}
                            type="email"
                            maxLength={255}
                            autoComplete="email"
                          />
                        </label>
                        <label className="block space-y-1">
                          <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                            {uiText("Initial password")}
                          </span>
                          <input
                            id="new-user-password"
                            value={newUserPassword}
                            onChange={(event) =>
                              setCreateUserDraft((current) => ({
                                ...current,
                                password: event.target.value,
                              }))
                            }
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
                            usersBusy !== null ||
                            usersQuery.isError ||
                            usersQuery.isPending ||
                            !newUsername.trim() ||
                            newUserPassword.trim().length < 8
                          }
                          className={`${BTN_PRIMARY} self-end`}
                        >
                          <UserPlus className="h-3.5 w-3.5" />
                          {uiText("Create")}
                        </button>
                      </div>
                      <p id="new-user-password-help" className="text-xs text-muted-foreground">
                        {uiText("Initial password: at least 8 characters.")}
                      </p>

                      <div className="space-y-2">
                        {usersQuery.isError && (
                          <div role="alert" className="flex items-center gap-2 text-sm">
                            <p>{t("settings.accountUsersFailed")}</p>
                            <Button
                              size="xs"
                              variant="outline"
                              onClick={() => void usersQuery.refetch()}
                            >
                              {t("Retry")}
                            </Button>
                          </div>
                        )}
                        {usersQuery.isPending ? (
                          <p role="status">{t("Loading…")}</p>
                        ) : usersQuery.isError && users.length === 0 ? null : users.length === 0 ? (
                          <p className="text-sm text-muted-foreground">{uiText("No users.")}</p>
                        ) : (
                          users.map((row) => (
                            <div
                              key={row.id}
                              role="group"
                              aria-label={`${uiText("User")}: ${row.username}`}
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
                                        {uiText("Admin")}
                                      </span>
                                    )}
                                    {!row.is_active && (
                                      <span className="rounded bg-red-500/10 px-2 py-0.5 font-mono text-3xs uppercase text-red-600">
                                        {uiText("Disabled")}
                                      </span>
                                    )}
                                  </div>
                                  <p className="text-xs text-muted-foreground">
                                    {row.email || uiText("No email")}
                                    {uiText(" · Created ")}
                                    {formatDate(row.created_at)}
                                  </p>
                                </div>
                                <div className="flex flex-wrap gap-2">
                                  <button
                                    type="button"
                                    disabled={
                                      usersBusy !== null ||
                                      usersQuery.isError ||
                                      usersQuery.isPending
                                    }
                                    onClick={() =>
                                      patchUser(row.id, { is_superuser: !row.is_superuser })
                                    }
                                    className={BTN_SECONDARY}
                                  >
                                    {row.is_superuser
                                      ? uiText("Remove admin")
                                      : uiText("Make admin")}
                                  </button>
                                  <button
                                    type="button"
                                    disabled={
                                      usersBusy !== null ||
                                      usersQuery.isError ||
                                      usersQuery.isPending
                                    }
                                    onClick={() =>
                                      row.is_active
                                        ? deactivateUser(row.id)
                                        : patchUser(row.id, { is_active: true })
                                    }
                                    className={BTN_SECONDARY}
                                  >
                                    {row.is_active ? uiText("Disable") : uiText("Enable")}
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
                                  placeholder={uiText("New password")}
                                />
                                <button
                                  type="button"
                                  onClick={() => resetUserPassword(row.id)}
                                  disabled={
                                    usersBusy !== null ||
                                    usersQuery.isError ||
                                    usersQuery.isPending ||
                                    (passwordDrafts[row.id]?.trim().length ?? 0) < 8
                                  }
                                  className={BTN_SECONDARY}
                                >
                                  {uiText("Reset password")}
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
                    title={uiText("Collection access")}
                    description={uiText(
                      "Assign view, edit, or admin access per user. Child collections inherit parent grants.",
                    )}
                    action={
                      <button
                        type="button"
                        onClick={() => void collectionPermissionsQuery.refetch()}
                        disabled={!accessCollection || collectionPermissionsQuery.isFetching}
                        className={BTN_ICON}
                        title={uiText("Refresh collection access")}
                      >
                        <RefreshCw
                          className={`h-4 w-4 ${collectionPermissionsQuery.isFetching ? "animate-spin" : ""}`}
                        />
                      </button>
                    }
                  >
                    <div className="p-4 sm:p-5 space-y-4">
                      <div className="grid gap-2 lg:grid-cols-[1fr_1.4fr_auto_auto]">
                        <label className="block space-y-1">
                          <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                            {uiText("User")}
                          </span>
                          <select
                            value={accessUserId}
                            disabled={usersQuery.isError || usersQuery.isPending}
                            onChange={(event) => {
                              setAccessUserId(event.target.value ? Number(event.target.value) : "");
                            }}
                            className={INPUT}
                          >
                            <option value="">{uiText("Select user")}</option>
                            {nonSuperUsers.map((row) => (
                              <option key={row.id} value={row.id}>
                                {row.username}
                              </option>
                            ))}
                          </select>
                        </label>
                        <div className="block space-y-1">
                          <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                            {uiText("Collection")}
                          </span>
                          <DropdownMenu
                            open={accessPickerOpen}
                            onOpenChange={setAccessPickerOpen}
                            role="dialog"
                            align="start"
                            contentClassName="w-80 max-w-[90vw] p-2"
                            trigger={
                              <button
                                type="button"
                                data-menu-trigger
                                onClick={() => setAccessPickerOpen((open) => !open)}
                                aria-haspopup="dialog"
                                aria-expanded={accessPickerOpen}
                                className={`${INPUT} w-full text-left`}
                              >
                                {accessCollection?.display_path ?? uiText("Select collection")}
                              </button>
                            }
                          >
                            <CollectionPicker
                              minRole="view"
                              selectedPath={accessCollection?.path ?? null}
                              emptyLabel={uiText("No collections found.")}
                              onSelect={(collection) => {
                                setAccessCollection(collection);
                                setAccessPickerOpen(false);
                              }}
                            />
                          </DropdownMenu>
                        </div>
                        <label className="block space-y-1">
                          <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                            {uiText("Role")}
                          </span>
                          <select
                            value={accessRole}
                            onChange={(event) =>
                              setAccessRole(selectedOption(COLLECTION_ROLES, event.target.value))
                            }
                            className={INPUT}
                            disabled={
                              usersQuery.isError ||
                              usersQuery.isPending ||
                              !accessUserId ||
                              !accessCollection
                            }
                          >
                            <option value="view">{uiText("View")}</option>
                            <option value="edit">{uiText("Edit")}</option>
                            <option value="admin">{uiText("Admin")}</option>
                          </select>
                        </label>
                        <button
                          type="button"
                          onClick={saveCollectionAccess}
                          disabled={
                            usersQuery.isError ||
                            usersQuery.isPending ||
                            !accessUserId ||
                            !accessCollection ||
                            accessBusy !== null ||
                            collectionPermissionsQuery.isPending ||
                            collectionPermissionsQuery.isError
                          }
                          className={`${BTN_PRIMARY} self-end`}
                        >
                          {accessBusy === "save" ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <ShieldCheck className="h-3.5 w-3.5" />
                          )}
                          {uiText("Grant")}
                        </button>
                      </div>

                      <div className="rounded border border-border overflow-hidden">
                        <div className="grid grid-cols-[1fr_auto_auto] gap-3 border-b border-border bg-muted/40 px-3 py-2 font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                          <span>{uiText("Collection")}</span>
                          <span>{uiText("Role")}</span>
                          <span>{uiText("Remove")}</span>
                        </div>
                        {!accessCollection ? (
                          <p className="px-3 py-4 text-sm text-muted-foreground">
                            {uiText("Select a collection to review grants.")}
                          </p>
                        ) : collectionPermissionsQuery.isError ? (
                          <div role="alert" className="flex items-center gap-2 px-3 py-4 text-sm">
                            <span>{uiText("Collection access could not be loaded.")}</span>
                            <Button
                              size="xs"
                              variant="outline"
                              onClick={() => void collectionPermissionsQuery.refetch()}
                            >
                              {uiText("Retry")}
                            </Button>
                          </div>
                        ) : collectionPermissionsQuery.isPending ? (
                          <p role="status" className="px-3 py-4 text-sm text-muted-foreground">
                            {uiText("Loading…")}
                          </p>
                        ) : !accessUserId ? (
                          <p className="px-3 py-4 text-sm text-muted-foreground">
                            {uiText("Select a user to review collection grants.")}
                          </p>
                        ) : selectedUserPermissions.length === 0 ? (
                          <p className="px-3 py-4 text-sm text-muted-foreground">
                            {activeAccessUser?.username ?? uiText("User")}
                            {uiText(" has no direct collection access.")}
                          </p>
                        ) : (
                          selectedUserPermissions.map((row) => {
                            const busyKey = `${row.collection_id}:${row.user_id}`;
                            return (
                              <div
                                key={`${row.collection_id}:${row.user_id}`}
                                className="grid grid-cols-[1fr_auto_auto] items-center gap-3 border-b border-border px-3 py-2 last:border-b-0"
                              >
                                <div className="min-w-0">
                                  <p className="truncate text-sm text-foreground">
                                    {accessCollection?.display_path ??
                                      uiText("Collection #{value1}", {
                                        value1: String(row.collection_id),
                                      })}
                                  </p>
                                  <p className="text-xs text-muted-foreground">
                                    {accessCollection?.model_count ?? 0}
                                    {uiText(" models")}
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
                                  disabled={
                                    usersQuery.isError ||
                                    usersQuery.isPending ||
                                    accessBusy !== null ||
                                    collectionPermissionsQuery.isError ||
                                    collectionPermissionsQuery.isPending
                                  }
                                  className="rounded p-1 text-red-600 hover:bg-red-500/10 disabled:opacity-50"
                                  title={uiText("Remove collection access")}
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
                    title={uiText("Printer access")}
                    description={uiText(
                      "Grant access per printer. Roles build from view to print, machine control, and administration.",
                    )}
                    action={
                      <button
                        type="button"
                        onClick={() => void refreshPrinterAccess()}
                        disabled={
                          accessPrintersQuery.isFetching ||
                          printerAccess.some(({ query }) => query.isFetching)
                        }
                        className={BTN_ICON}
                        title={uiText("Refresh printer access")}
                      >
                        <RefreshCw
                          className={`h-4 w-4 ${accessPrintersQuery.isFetching || printerAccess.some(({ query }) => query.isFetching) ? "animate-spin" : ""}`}
                        />
                      </button>
                    }
                  >
                    <div className="space-y-4 p-4 sm:p-5">
                      <div className="grid gap-2 lg:grid-cols-[1fr_1.4fr_auto_auto]">
                        <label className="block space-y-1">
                          <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                            {uiText("User")}
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
                            disabled={usersQuery.isError || usersQuery.isPending}
                          >
                            <option value="">{uiText("Choose printer user")}</option>
                            {nonSuperUsers.map((row) => (
                              <option key={row.id} value={row.id}>
                                {row.username}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className="block space-y-1">
                          <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                            {uiText("Printer")}
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
                            disabled={
                              usersQuery.isError ||
                              usersQuery.isPending ||
                              !printerAccessUserId ||
                              accessPrintersQuery.isPending ||
                              accessPrintersQuery.isError
                            }
                          >
                            <option value="">{uiText("Select printer")}</option>
                            {accessPrinters.map((row) => (
                              <option key={row.id} value={row.id}>
                                {row.name}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className="block space-y-1">
                          <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                            {uiText("Role")}
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
                              usersQuery.isError ||
                              usersQuery.isPending ||
                              !printerAccessUserId ||
                              !accessPrinterId ||
                              !selectedPrinterRead?.isSuccess ||
                              selectedPrinterRead.isError ||
                              accessPrintersQuery.isPending ||
                              accessPrintersQuery.isError
                            }
                          >
                            <option value="view">{uiText("View")}</option>
                            <option value="print">{uiText("Print")}</option>
                            <option value="control">{uiText("Control")}</option>
                            <option value="admin">{uiText("Admin")}</option>
                          </select>
                        </label>
                        <button
                          type="button"
                          onClick={savePrinterAccess}
                          disabled={
                            usersQuery.isError ||
                            usersQuery.isPending ||
                            !printerAccessUserId ||
                            !accessPrinterId ||
                            printerAccessBusy !== null ||
                            accessPrintersQuery.isError ||
                            !selectedPrinterRead?.isSuccess ||
                            selectedPrinterRead.isError
                          }
                          className={`${BTN_PRIMARY} self-end`}
                        >
                          {printerAccessBusy === "save" ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <ShieldCheck className="h-3.5 w-3.5" />
                          )}
                          {uiText("Save")}
                        </button>
                      </div>

                      {accessPrintersQuery.isError && (
                        <div role="alert" className="flex items-center gap-2 text-sm">
                          <p>{t("settings.accessPrintersFailed")}</p>
                          <Button
                            size="xs"
                            variant="outline"
                            onClick={() => void accessPrintersQuery.refetch()}
                          >
                            {t("Retry")}
                          </Button>
                        </div>
                      )}
                      {accessPrintersQuery.isPending && <p role="status">{t("Loading…")}</p>}
                      {printerAccessUserId &&
                        printerAccess.map(({ printer, query }) =>
                          query.isError ? (
                            <div
                              key={printer.id}
                              role="alert"
                              className="flex items-center gap-2 text-sm"
                            >
                              <p>{t("settings.accessPrinterFailed", { name: printer.name })}</p>
                              <Button
                                size="xs"
                                variant="outline"
                                onClick={() => void query.refetch()}
                              >
                                {t("Retry")}
                              </Button>
                            </div>
                          ) : query.isPending ? (
                            <p key={printer.id} role="status">
                              {t("settings.accessPrinterLoading", { name: printer.name })}
                            </p>
                          ) : null,
                        )}

                      <p className="text-xs text-muted-foreground">
                        {uiText(
                          "View: status and history · Print: send and start jobs · Control: pause, cancel, temperatures, homing, emergency stop · Admin: settings, files, routing, and maintenance",
                        )}
                      </p>

                      <div className="overflow-hidden rounded border border-border">
                        <div className="grid grid-cols-[1fr_auto_auto] gap-3 border-b border-border bg-muted/40 px-3 py-2 font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                          <span>{uiText("Printer")}</span>
                          <span>{uiText("Role")}</span>
                          <span>{uiText("Remove")}</span>
                        </div>
                        {!printerAccessUserId ? (
                          <p className="px-3 py-4 text-sm text-muted-foreground">
                            {uiText("Select a user to review printer grants.")}
                          </p>
                        ) : accessPrintersQuery.isError ||
                          accessPrintersQuery.isPending ||
                          (printerAccess.some(({ query }) => query.isError || query.isPending) &&
                            selectedPrinterPermissions.length ===
                              0) ? null : selectedPrinterPermissions.length === 0 ? (
                          <p className="px-3 py-4 text-sm text-muted-foreground">
                            {activePrinterAccessUser?.username ?? uiText("User")}
                            {uiText(" has no direct printer access.")}
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
                                    {printer?.name ??
                                      uiText("Printer #{value1}", {
                                        value1: String(row.printer_id),
                                      })}
                                  </p>
                                  <p className="text-xs text-muted-foreground">
                                    {printer?.group || uiText("Ungrouped")}
                                  </p>
                                </div>
                                <span className="rounded bg-muted px-2 py-1 font-mono text-3xs uppercase text-muted-foreground">
                                  {row.role}
                                </span>
                                <button
                                  type="button"
                                  onClick={() => removePrinterAccess(row.printer_id, row.user_id)}
                                  disabled={
                                    usersQuery.isError ||
                                    usersQuery.isPending ||
                                    printerAccessBusy !== null ||
                                    accessPrintersQuery.isError ||
                                    printerAccess.find(
                                      ({ printer }) => printer.id === row.printer_id,
                                    )?.query.isError
                                  }
                                  className="rounded p-1 text-destructive hover:bg-destructive/10 disabled:opacity-50"
                                  title={uiText("Remove printer access")}
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
                  title={uiText("API keys")}
                  description={uiText(
                    "Create credentials for scripts and integrations, then exchange them for a JWT at login.",
                  )}
                >
                  <div className="p-4 sm:p-5 space-y-4">
                    {!user ? (
                      <p className="text-sm text-muted-foreground">
                        {uiText("Sign in to create API keys.")}
                      </p>
                    ) : (
                      <>
                        <div className="rounded border border-border bg-muted/40 p-3">
                          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                            <div className="min-w-0">
                              <p className="text-sm font-medium text-foreground">
                                {uiText("Browser importer")}
                              </p>
                              <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
                                {uiText(
                                  "Create a dedicated key and prepare this vault in the browser extension.",
                                )}
                              </p>
                            </div>
                            {extensionSetupReady ? (
                              <Badge
                                variant="success"
                                className="h-8 gap-1.5 border border-success/30 bg-success/10 px-3 font-mono text-success uppercase tracking-wider"
                                role="status"
                              >
                                <Check className="h-3.5 w-3.5" aria-hidden />
                                {uiText("Setup prepared")}
                              </Badge>
                            ) : (
                              <Button
                                type="button"
                                size="xs"
                                onClick={setupBrowserExtension}
                                loading={keyBusy}
                                disabled={
                                  receipt !== null || apiKeysQuery.isError || apiKeysQuery.isPending
                                }
                                className="font-mono uppercase tracking-wider"
                              >
                                <Puzzle className="h-3.5 w-3.5" />
                                {uiText("Set up extension")}
                              </Button>
                            )}
                          </div>
                          {extensionSetupReady && (
                            <p className="mt-3 text-xs font-medium text-muted-foreground">
                              {uiText(
                                "Open the PrintStash extension on this tab to finish the verified connection.",
                              )}
                            </p>
                          )}
                        </div>

                        <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
                          <label className="block space-y-1">
                            <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                              {uiText("Key name")}
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
                            disabled={
                              keyBusy ||
                              receipt !== null ||
                              apiKeysQuery.isError ||
                              apiKeysQuery.isPending
                            }
                            className={BTN_PRIMARY}
                          >
                            <KeyRound className="h-3.5 w-3.5" />
                            {uiText("Generate")}
                          </button>
                        </div>

                        {newApiKey && (
                          <div className="border border-primary/40 bg-primary/10 rounded p-3 space-y-2">
                            <p className="text-xs text-muted-foreground">
                              {uiText("Copy this key now. It will only be shown once.")}
                            </p>
                            <Button size="xs" variant="ghost" onClick={dismissReceipt}>
                              {t("settings.accountDismissSecret")}
                            </Button>
                            <div className="flex items-center gap-2">
                              <code className="flex-1 min-w-0 overflow-x-auto whitespace-nowrap rounded bg-muted px-3 py-2 text-xs text-foreground">
                                {newApiKey}
                              </code>
                              <button
                                type="button"
                                onClick={copyApiKey}
                                className={BTN_ICON}
                                title={uiText("Copy API key")}
                              >
                                <Copy className="h-4 w-4" />
                              </button>
                              <button
                                type="button"
                                onClick={copyOrcaCommand}
                                className={BTN_SECONDARY}
                                title={uiText("Copy OrcaSlicer post-processing command")}
                              >
                                <Copy className="h-3.5 w-3.5" />
                                {uiText("Orca command")}
                              </button>
                            </div>
                          </div>
                        )}

                        <div className="space-y-2">
                          {apiKeysQuery.isError && (
                            <div role="alert" className="flex items-center gap-2 text-sm">
                              <p>{t("settings.accountKeysFailed")}</p>
                              <Button
                                size="xs"
                                variant="outline"
                                onClick={() => void apiKeysQuery.refetch()}
                              >
                                {t("Retry")}
                              </Button>
                            </div>
                          )}
                          {apiKeysQuery.isPending ? (
                            <p role="status">{t("Loading…")}</p>
                          ) : apiKeysQuery.isError &&
                            apiKeys.length === 0 ? null : apiKeys.length === 0 ? (
                            <p className="text-sm text-muted-foreground">
                              {uiText("No active API keys.")}
                            </p>
                          ) : (
                            apiKeys.map((key) => (
                              <div
                                key={key.id}
                                role="group"
                                aria-label={`${uiText("API key")}: ${key.name}`}
                                className="flex items-center gap-3 border border-border rounded px-3 py-2"
                              >
                                <div className="min-w-0 flex-1">
                                  <p className="truncate text-sm text-foreground">{key.name}</p>
                                  <p className="font-mono text-2xs text-muted-foreground">
                                    {key.prefix}... ·{" "}
                                    {key.last_used_at ? uiText("Used") : uiText("Never used")}
                                  </p>
                                </div>
                                <button
                                  type="button"
                                  onClick={() => deleteApiKey(key.id)}
                                  disabled={
                                    keyBusy || apiKeysQuery.isError || apiKeysQuery.isPending
                                  }
                                  className="inline-flex h-9 w-9 items-center justify-center rounded border border-border text-red-500 hover:bg-red-500/10 disabled:opacity-50"
                                  title={uiText("Revoke API key")}
                                >
                                  <Trash2 className="h-4 w-4" />
                                </button>
                              </div>
                            ))
                          )}
                        </div>

                        <div className="rounded border border-border bg-muted/40 p-3">
                          <p className="text-xs text-muted-foreground leading-relaxed">
                            {uiText("Use your username with this API key on")}{" "}
                            <code className="font-mono">/api/v1/auth/login</code>
                            {uiText(
                              ". The hook exchanges it for a JWT Bearer token, then uploads with the normal",
                            )}{" "}
                            <code className="font-mono">{uiText("Authorization")}</code>
                            {uiText(" header.")}
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
                <StorageConfigCard storageHealth={storageHealth} migrationManaged />
                {user?.is_superuser && <StorageInventoryPanel />}
                {user?.is_superuser && <VaultMigrationPanel />}
                {user?.is_superuser && <ArtifactCacheCard />}
              </div>
            )}

            {activeSection === "backup" && (
              <div className="space-y-6 animate-panel-in">
                {(remoteConfig.isError || backupConnectionRead.isError) && (
                  <p role="alert" className="text-sm text-destructive">
                    {uiText("Could not load backup settings.")}
                  </p>
                )}
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
                        backupConfigUnavailable ||
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
                        disabled={
                          !user?.is_superuser || backupRetentionBusy || backupConfigUnavailable
                        }
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
                      disabled={
                        !user?.is_superuser ||
                        backupPolicyBusy ||
                        backupsLoading ||
                        backupConfigUnavailable ||
                        backupConnectionRead.isError ||
                        !backupConnectionRead.data
                      }
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
                          disabled={
                            !user?.is_superuser ||
                            backupPolicyBusy ||
                            backupsLoading ||
                            backupConfigUnavailable ||
                            backupConnectionRead.isError ||
                            !backupConnectionRead.data
                          }
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
                              disabled={
                                !user?.is_superuser ||
                                backupPolicyBusy ||
                                backupsLoading ||
                                backupConfigUnavailable ||
                                backupConnectionRead.isError ||
                                !backupConnectionRead.data
                              }
                            />
                          </div>
                          <div className="flex justify-center">
                            <Checkbox
                              checked={automaticLocalBackupEnabled}
                              onChange={setAutomaticLocalBackupEnabled}
                              ariaLabel={t("settings.backupLocalAutomatic")}
                              disabled={
                                !user?.is_superuser ||
                                backupPolicyBusy ||
                                backupsLoading ||
                                backupConfigUnavailable ||
                                backupConnectionRead.isError ||
                                !backupConnectionRead.data
                              }
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
                                disabled={
                                  !user?.is_superuser ||
                                  backupPolicyBusy ||
                                  backupsLoading ||
                                  backupConfigUnavailable ||
                                  backupConnectionRead.isError ||
                                  !backupConnectionRead.data
                                }
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
                                disabled={
                                  !user?.is_superuser ||
                                  backupPolicyBusy ||
                                  backupsLoading ||
                                  backupConfigUnavailable ||
                                  backupConnectionRead.isError ||
                                  !backupConnectionRead.data
                                }
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
                  title={uiText("Manual backup")}
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
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          {uiText(" Backing up…")}
                        </>
                      ) : (
                        <>
                          <HardDrive className="h-3.5 w-3.5" />
                          {uiText(" Backup now")}
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
                  <BackupRunHistory onPublished={() => void ownedBackupRead.refetch()} />
                )}
                <SettingsCard
                  icon={RotateCcw}
                  title={uiText("Restore backup")}
                  description={uiText(
                    "Recover the vault database and stored files from a previous backup.",
                  )}
                  action={
                    <button
                      type="button"
                      onClick={() => void loadBackups()}
                      disabled={!user?.is_superuser || backupsLoading}
                      className={BTN_ICON}
                      title={uiText("Refresh backups")}
                    >
                      <RefreshCw className={`h-4 w-4 ${backupsLoading ? "animate-spin" : ""}`} />
                    </button>
                  }
                >
                  <div className="divide-y divide-border">
                    {ownedBackupRead.isError && (
                      <p role="alert" className="p-4 text-sm text-destructive">
                        {uiText("Could not load backup sources.")}
                      </p>
                    )}
                    {backupDiscoveryFailed && (
                      <p role="alert" className="p-4 text-sm text-destructive">
                        {uiText("Some backup sources could not be loaded.")}
                      </p>
                    )}
                    {!user?.is_superuser ? (
                      <p className="p-4 sm:p-5 text-sm text-muted-foreground">
                        {uiText("Superuser access is required.")}
                      </p>
                    ) : backupsLoading ? (
                      <p className="p-4 sm:p-5 text-sm text-muted-foreground">
                        {uiText("Loading...")}
                      </p>
                    ) : !ownedBackupRead.isError &&
                      !backupDiscoveryFailed &&
                      backups.length === 0 &&
                      unownedBackups.length === 0 &&
                      unownedS3Backups.length === 0 &&
                      unownedRemoteBackups.length === 0 ? (
                      <p className="p-4 sm:p-5 text-sm text-muted-foreground">
                        {uiText("No backups found.")}
                      </p>
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
                                    {uiText("{value1} files · {value2} · v{value3} · {value4}", {
                                      value1: String(candidate.file_count ?? ""),
                                      value2: String(formatBytes(candidate.size_bytes) ?? ""),
                                      value3: String(candidate.app_version ?? ""),
                                      value4: String(formatDate(candidate.created_at) ?? ""),
                                    })}
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
                                      namespace: candidate.namespace ?? uiText("unavailable"),
                                    })}
                                  </p>
                                  <p className="mt-1 text-xs text-muted-foreground">
                                    {t("settings.backupProviderRef", {
                                      provider: shortOpaque(candidate.provider_ref),
                                    })}
                                  </p>
                                  <p className="mt-1 text-xs text-muted-foreground">
                                    {uiText("{value1} · {value2} files · {value3} · v{value4}", {
                                      value1: String(
                                        t("settings.backupPrefix", { prefix: candidate.prefix }) ??
                                          "",
                                      ),
                                      value2: String(candidate.file_count ?? ""),
                                      value3: String(formatBytes(candidate.size_bytes) ?? ""),
                                      value4: String(candidate.app_version ?? ""),
                                    })}
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
                                      digest: `${candidate.archive_sha256?.slice(0, 16) ?? uiText("unavailable")}…`,
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
                                    {uiText("{value1} · {value2} · {value3} · v{value4}", {
                                      value1: String(candidate.connection_name ?? ""),
                                      value2: String(candidate.provider.toUpperCase() ?? ""),
                                      value3: String(formatBytes(candidate.size_bytes) ?? ""),
                                      value4: String(candidate.app_version ?? ""),
                                    })}
                                  </p>
                                  <p className="mt-1 truncate font-mono text-2xs text-muted-foreground">
                                    {t("settings.backupSha256", {
                                      digest: `${candidate.archive_sha256?.slice(0, 16) ?? uiText("unavailable")}…`,
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
                                  {uiText("v{value1}", {
                                    value1: String(backup.app_version ?? ""),
                                  })}
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
                                {uiText("{value1} files · {value2} · {value3}", {
                                  value1: String(backup.file_count ?? ""),
                                  value2: String(formatBytes(backup.size_bytes) ?? ""),
                                  value3: String(backup.location ?? ""),
                                })}
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
                                {uiText("Download")}
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
                                {uiText("Restore")}
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

            {activeSection === "ai-search" && user?.is_superuser && <AiSearchSettings />}

            {activeSection === "maintenance" && user?.is_superuser && <MaintenancePanel />}

            {activeSection === "work" && user?.is_superuser && <BackgroundWorkPanel />}

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
                {user?.is_superuser && remoteConfig.isError && (
                  <div role="alert" className="flex items-center gap-2 text-sm">
                    <p>{t("settings.configLoadFailed")}</p>
                    <Button variant="outline" size="sm" onClick={() => void remoteConfig.refetch()}>
                      {t("Retry")}
                    </Button>
                  </div>
                )}
                {user?.is_superuser && remoteConfig.isPending && (
                  <p role="status">{t("Loading…")}</p>
                )}

                <SettingsCard
                  icon={Printer}
                  title={uiText("Printer cards")}
                  description={uiText(
                    "Choose whether printer cards include a visual. Plain cards remain more compact and information-dense.",
                  )}
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
                          {uiText("Show printer image")}
                        </p>
                        <p className="mt-0.5 text-xs text-muted-foreground">
                          {uiText("Adds a brand-neutral printer visual above each card.")}
                        </p>
                      </div>
                    </div>
                    <button
                      type="button"
                      role="switch"
                      aria-label={uiText("Show printer image on printer cards")}
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
                  title={uiText("Print tracking")}
                  description={uiText(
                    "Automatically promote a revision to known-good after its first successful print. A manual failed/archived verdict is never overridden.",
                  )}
                >
                  <div className="p-4 sm:p-5 flex items-center justify-between gap-4">
                    <span className="text-[13px] text-foreground">
                      {uiText("Auto-mark known good on successful print")}
                    </span>
                    <button
                      type="button"
                      role="switch"
                      aria-label={uiText("Auto-mark known good on successful print")}
                      aria-checked={autoMarkKnownGood}
                      disabled={
                        !user?.is_superuser ||
                        !remoteConfigData ||
                        remoteConfig.isError ||
                        autoMarkBusy ||
                        configCommand.isPending
                      }
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
                  title={uiText("Currency")}
                  description={uiText(
                    "Currency used to display cost figures in statistics and filament pricing.",
                  )}
                >
                  <div className="p-4 sm:p-5 flex items-center justify-between gap-4">
                    <label htmlFor="display-currency" className="text-[13px] text-foreground">
                      {uiText("Display currency")}
                    </label>
                    <select
                      id="display-currency"
                      value={currency}
                      onChange={(event) => saveCurrency(event.target.value)}
                      disabled={
                        !user?.is_superuser ||
                        !remoteConfigData ||
                        remoteConfig.isError ||
                        currencyBusy ||
                        configCommand.isPending
                      }
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
                  title={uiText("Model card metrics")}
                  description={uiText(
                    "Choose which 3 stats appear on each model card in the grid.",
                  )}
                  action={
                    <button type="button" onClick={resetCardMetrics} className={BTN_SECONDARY}>
                      <RotateCcw className="h-3.5 w-3.5" />
                      {uiText("Reset")}
                    </button>
                  }
                >
                  <div className="p-4 sm:p-5 grid gap-4 sm:grid-cols-3">
                    {([0, 1, 2] as const).map((slot) => (
                      <div key={slot} className="space-y-2">
                        <p className="text-2xs font-mono uppercase tracking-wider text-primary">
                          {uiText("Slot ")}
                          {slot + 1}
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
                                    {uiText("Slot ")}
                                    {otherSlot + 1}
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
                  title={uiText("Model metadata")}
                  description={uiText("Choose which metadata fields appear on model detail pages.")}
                  action={
                    <button
                      type="button"
                      onClick={resetMetadataPreferences}
                      className={BTN_SECONDARY}
                    >
                      <RotateCcw className="h-3.5 w-3.5" />
                      {uiText("Reset")}
                    </button>
                  }
                >
                  <div className="p-4 sm:p-5 space-y-3">
                    <div className="flex items-center justify-between gap-3">
                      <p className="text-2xs font-mono uppercase tracking-wider text-muted-foreground">
                        {uiText("{value1} of {value2} shown", {
                          value1: String(
                            METADATA_FIELDS.filter((f) => metadataPrefs[f.id]).length ?? "",
                          ),
                          value2: String(METADATA_FIELDS.length ?? ""),
                        })}
                      </p>
                      <div className="flex items-center gap-1.5">
                        <button
                          type="button"
                          onClick={() => setAllMetadataPreferences(true)}
                          className="font-mono text-3xs uppercase tracking-wider text-muted-foreground hover:text-primary transition-colors"
                        >
                          {uiText("Show all")}
                        </button>
                        <span className="text-muted-foreground/40">·</span>
                        <button
                          type="button"
                          onClick={() => setAllMetadataPreferences(false)}
                          className="font-mono text-3xs uppercase tracking-wider text-muted-foreground hover:text-primary transition-colors"
                        >
                          {uiText("Hide all")}
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
                {user?.is_superuser && remoteConfig.isError && (
                  <div role="alert" className="flex items-center gap-2 text-sm">
                    <p>{t("settings.configLoadFailed")}</p>
                    <Button variant="outline" size="sm" onClick={() => void remoteConfig.refetch()}>
                      {t("Retry")}
                    </Button>
                  </div>
                )}
                {user?.is_superuser && remoteConfig.isPending && (
                  <p role="status">{t("Loading…")}</p>
                )}

                <SettingsCard
                  icon={Eye}
                  title={uiText("Interactive previews")}
                  description={uiText(
                    "Balance sharpness against GPU use in the 3D Model and G-code viewers. This preference is saved in this browser.",
                  )}
                >
                  <div className="grid gap-4 p-4 sm:grid-cols-2 sm:p-5">
                    <label className="block space-y-1">
                      <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                        {uiText("Preview quality")}
                      </span>
                      <select
                        aria-label={uiText("Preview quality")}
                        value={previewPreferences.previewQuality}
                        onChange={(event) =>
                          savePreviewPreference({
                            previewQuality: selectedOption(PREVIEW_QUALITIES, event.target.value),
                          })
                        }
                        className={INPUT}
                      >
                        <option value="performance">{uiText("Performance · 1×")}</option>
                        <option value="balanced">{uiText("Balanced · 1.5×")}</option>
                        <option value="detail">{uiText("High detail · 2×")}</option>
                      </select>
                    </label>
                    <label className="block space-y-1">
                      <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                        {uiText("Screenshot resolution")}
                      </span>
                      <select
                        aria-label={uiText("Screenshot resolution")}
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
                        <option value={1}>{uiText("Standard · 1×")}</option>
                        <option value={2}>{uiText("Sharp · 2×")}</option>
                        <option value={3}>{uiText("Print-ready · 3×")}</option>
                      </select>
                    </label>
                  </div>
                </SettingsCard>

                <SettingsCard
                  icon={Images}
                  title={uiText("Model preview images")}
                  description={uiText(
                    "Choose the resolution of generated Model card images. Higher settings take longer to render and use more memory and storage.",
                  )}
                >
                  <div className="grid gap-4 p-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end sm:p-5">
                    <label className="block space-y-1">
                      <span className="block font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                        {uiText("Model image quality")}
                      </span>
                      <select
                        aria-label={uiText("Model image quality")}
                        value={modelThumbnailWidth}
                        onChange={(event) =>
                          saveModelThumbnailWidth(
                            selectedOption(MODEL_THUMBNAIL_WIDTHS, Number(event.target.value)),
                          )
                        }
                        disabled={
                          !user?.is_superuser ||
                          !remoteConfigData ||
                          remoteConfig.isError ||
                          previewBusy !== null ||
                          configCommand.isPending
                        }
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
                        <option value={320}>{uiText("Compact · 320 × 240")}</option>
                        <option value={640}>{uiText("Standard · 640 × 480")}</option>
                        <option value={1280}>{uiText("High · 1280 × 960")}</option>
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
                      {uiText("Recreate all images")}
                    </button>
                  </div>
                  <div className="border-t border-border px-4 py-3 sm:px-5">
                    <p className="text-xs text-muted-foreground">
                      {uiText(
                        "Quality changes apply to new images. Recreate all images to update existing Models in the background.",
                      )}
                    </p>
                  </div>
                </SettingsCard>
              </div>
            )}

            {activeSection === "trash" && (
              <div className="space-y-6 animate-panel-in">
                <SettingsCard
                  icon={Trash2}
                  title={uiText("Trash retention")}
                  description={uiText(
                    "Soft-deleted models stay restorable until the retention window expires.",
                  )}
                  action={
                    <button
                      type="button"
                      onClick={loadTrash}
                      disabled={trashLoading}
                      className={BTN_ICON}
                      title={uiText("Refresh trash")}
                    >
                      <RefreshCw className={`h-4 w-4 ${trashLoading ? "animate-spin" : ""}`} />
                    </button>
                  }
                >
                  <div className="p-4 sm:p-5 grid gap-3 sm:grid-cols-[160px_auto_auto] sm:items-end">
                    <label className="block">
                      <span className="block text-2xs text-muted-foreground mb-1">
                        {uiText("Days")}
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
                      {trashBusy === "settings" ? uiText("Saving") : uiText("Save retention")}
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
                      {trashBusy === "gc" ? uiText("Preparing") : uiText("Review expired")}
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
                            {uiText("GC plan #{value1} · {value2}", {
                              value1: String(gcPlan.id ?? ""),
                              value2: String(gcPlan.state ?? ""),
                            })}
                          </p>
                          <p className="mt-1 text-xs text-muted-foreground">
                            {uiText(
                              "{value1} of {value2} expired resources · {value3} storage keys · {value4}",
                              {
                                value1: String(gcPlan.resource_count ?? ""),
                                value2: String(gcPlan.candidate_pool_count ?? ""),
                                value3: String(gcPlan.key_count ?? ""),
                                value4: String(formatBytes(gcPlan.size_bytes) ?? ""),
                              },
                            )}
                          </p>
                        </div>
                        <span className="rounded border border-border px-2 py-1 font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                          {gcPlan.backup_id ? uiText("backup verified") : uiText("no backup bound")}
                        </span>
                      </div>
                      <p className="mt-3 text-2xs text-muted-foreground">
                        {uiText("Exact plan digest")}
                      </p>
                      <code className="mt-1 block break-all rounded border border-border bg-background px-2 py-2 text-3xs text-foreground">
                        {gcPlan.digest}
                      </code>
                      {gcPlan.state === "preview" && (
                        <div className="mt-3 space-y-3">
                          <p className="text-xs text-muted-foreground">
                            {uiText(
                              "Approval is fail-closed: paste the exact digest below. The server will also require verified storage and a recent backup on an independent S3 provider before starting the quarantine.",
                            )}
                          </p>
                          <input
                            className={INPUT}
                            aria-label={uiText("Confirm GC plan digest")}
                            placeholder={uiText("Paste the 64-character digest")}
                            value={gcDigestConfirmation}
                            disabled={trashBusy === "gc"}
                            onChange={(event) => setGcDigestConfirmation(event.target.value.trim())}
                          />
                        </div>
                      )}
                      {gcPlan.state === "quarantined" && gcPlan.quarantine_until && (
                        <p className="mt-3 text-xs text-muted-foreground">
                          {uiText(
                            "Recovery quarantine ends {value1}. The plan and backup are reverified before final deletion.",
                            { value1: String(formatDateTime(gcPlan.quarantine_until) ?? "") },
                          )}
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
                            {uiText("Verify backup and quarantine")}
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
                            {uiText("Reverify and finalize")}
                          </button>
                        )}
                        {["preview", "quarantined"].includes(gcPlan.state) && (
                          <button
                            type="button"
                            className={BTN_SECONDARY}
                            disabled={trashBusy === "gc"}
                            onClick={abortExpiredGcPlan}
                          >
                            {uiText("Abort plan")}
                          </button>
                        )}
                      </div>
                    </div>
                  )}
                </SettingsCard>

                <SettingsCard
                  icon={Boxes}
                  title={uiText("Deleted models")}
                  description={uiText("Restore models or remove them permanently from storage.")}
                >
                  {trashItems.length > 0 && (
                    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border bg-muted/30 px-4 py-3 text-xs text-muted-foreground sm:px-5">
                      <span>
                        {uiText("models.deletedCount", {
                          value1: String(trashItems.length),
                          count: Number(trashItems.length),
                        })}
                      </span>
                      <span className="font-mono tabular-nums" aria-label={uiText("Trash size")}>
                        {uiText("{value1} reclaimable", {
                          value1: String(
                            formatBytes(
                              trashItems.reduce((total, item) => total + item.size_bytes, 0),
                            ) ?? "",
                          ),
                        })}
                      </span>
                    </div>
                  )}
                  <div className="divide-y divide-border">
                    {!user ? (
                      <p className="p-4 sm:p-5 text-sm text-muted-foreground">
                        {uiText("Sign in to manage the trash.")}
                      </p>
                    ) : trashLoading ? (
                      <p className="p-4 sm:p-5 text-sm text-muted-foreground">
                        {uiText("Loading...")}
                      </p>
                    ) : trashItems.length === 0 ? (
                      <p className="p-4 sm:p-5 text-sm text-muted-foreground">
                        {uiText("Trash is empty.")}
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
                                {uiText("{value1} files", {
                                  value1: String(item.file_count ?? ""),
                                })}
                              </span>
                              <span className="font-mono text-3xs uppercase tracking-wider px-2 py-0.5 rounded border border-border text-muted-foreground">
                                {formatBytes(item.size_bytes)}
                              </span>
                            </div>
                            <p className="mt-1 text-xs text-muted-foreground">
                              {uiText("Deleted {value1} · Expires {value2}", {
                                value1: String(formatDate(item.deleted_at) ?? ""),
                                value2: String(formatDate(item.expires_at) ?? ""),
                              })}
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
                              {uiText("Restore")}
                            </button>
                            <button
                              type="button"
                              onClick={() => purgeTrashItem(item.id)}
                              disabled={trashBusy !== null}
                              className="inline-flex items-center gap-1.5 px-3 py-2 rounded border border-red-500/30 text-red-500 hover:bg-red-500/10 transition-colors text-xs font-medium uppercase tracking-wider disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                              {uiText("Delete")}
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
                          {uiText("v")}
                          {health?.version ?? "0.2.0"}
                        </span>
                      </div>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        {uiText("Self-hosted asset management for 3D printing workflows.")}
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
                        {releaseChecking ? uiText("Checking") : uiText("Check for updates")}
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
                        uiText("Latest published release installed.")}
                      {releaseStatus.status === "update_available" &&
                        releaseStatus.latest_version && (
                          <>
                            {uiText("Update available: v")}
                            {releaseStatus.latest_version}.
                          </>
                        )}
                      {releaseStatus.status === "unavailable" &&
                        uiText("Release check unavailable. Try again later.")}
                    </div>
                  )}
                </div>

                {/* Changelog */}
                <SettingsCard
                  icon={Info}
                  title={uiText("Latest changes")}
                  description={uiText("What changed in the current release")}
                >
                  <div className="divide-y divide-border">
                    {latestRelease && (
                      <div className="px-4 sm:px-6 py-5 grid grid-cols-1 sm:grid-cols-[8rem_1fr] gap-3">
                        <div className="flex items-start gap-2">
                          <span className="rounded bg-primary/10 px-2 py-0.5 text-xs font-semibold text-primary">
                            {uiText("v{value1}", { value1: String(latestRelease.version ?? "") })}
                          </span>
                          <span className="text-2xs text-muted-foreground pt-0.5">
                            {knownUiText(latestRelease.date)}
                          </span>
                        </div>
                        <ul className="space-y-1.5">
                          {latestRelease.changes.map((change, i) => (
                            <li key={i} className="flex gap-2 text-xs text-muted-foreground">
                              <span className="mt-1.5 h-1 w-1 flex-shrink-0 rounded-full bg-primary" />
                              <span>{knownUiText(change)}</span>
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
