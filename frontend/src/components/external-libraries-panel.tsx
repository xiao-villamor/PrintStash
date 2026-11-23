import { EditLibrarySourceModal } from "@/components/edit-library-source-modal";
import { cn } from "@/lib/utils";
import {
  SETTINGS_BUTTON,
  SETTINGS_BUTTON_DANGER,
  SETTINGS_BUTTON_PRIMARY,
  SETTINGS_FIELD,
} from "@/components/settings-controls";
import { SettingsNotice, SettingsStatus, SettingsGroupHead } from "@/components/settings-card";
import { Switch } from "@/components/ui/switch";
import { describeSourceLocation } from "@/lib/library-source-location";
import { Link } from "@/lib/link";
import { currentLocale } from "@/lib/locale";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { FolderSync, Plus, RefreshCw } from "lucide-react";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { Modal } from "@/components/ui/modal";
import { useQuery } from "@tanstack/react-query";
import {
  defaultLibrarySourcesApi,
  librarySourcesOptions,
  useLibrarySourceCommand,
  type LibrarySourcesApi,
} from "@/lib/queries/settings-library-sources";
import { vaultConfigOptions, useVaultConfigCommand } from "@/lib/queries/settings-config";
import { storageConnectionsOptions, storageReadDenied } from "@/lib/queries/settings-storage";
import { useAuth } from "@/lib/auth-context";
import { onAuthChange } from "@/lib/auth-store";
import { captureEditingBase } from "@/lib/api/editing";
import type { VaultConfigRead } from "@/types";
import { getSessionVersion } from "@/lib/session-transport";
import { parseApiError, userMessage } from "@/lib/errors";
import { useI18n } from "@/lib/i18n";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/lib/toast";
import { Localized } from "@/components/ui/localized";
import type {
  ExternalLibrary,
  ExternalLibraryCollectionMode,
  ExternalLibraryCreate,
  ExternalLibraryWatchMode,
  LibrarySourceKind,
} from "@/types";

type SourceToggleReview =
  | { phase: "idle" }
  | { phase: "required" | "loading"; next: boolean; problem: "conflict" | "unconfirmed" }
  | {
      phase: "ready";
      next: boolean;
      problem: "conflict" | "unconfirmed";
      snapshot: VaultConfigRead;
    };

const BTN_PRIMARY = SETTINGS_BUTTON_PRIMARY;
const BTN_SECONDARY = SETTINGS_BUTTON;
const INPUT = SETTINGS_FIELD;

// Cron presets surfaced as a dropdown; "" = manual only. Anything not in this
// list shows the "Custom" option with a raw cron input.
const SCHEDULE_PRESETS: { label: string; cron: string }[] = [
  {
    get label() {
      return uiText("Manual only");
    },
    cron: "",
  },
  {
    get label() {
      return uiText("Hourly");
    },
    cron: "0 * * * *",
  },
  {
    get label() {
      return uiText("Every 6 hours");
    },
    cron: "0 */6 * * *",
  },
  {
    get label() {
      return uiText("Daily (midnight)");
    },
    cron: "0 0 * * *",
  },
  {
    get label() {
      return uiText("Weekly (Sunday)");
    },
    cron: "0 0 * * 0",
  },
];
const PRESET_CRONS = SCHEDULE_PRESETS.map((p) => p.cron);
const CUSTOM_SENTINEL = "__custom__";

const WATCH_OPTIONS: { value: ExternalLibraryWatchMode; label: string }[] = [
  {
    value: "auto",
    get label() {
      return uiText("Auto (watch local folders)");
    },
  },
  {
    value: "events",
    get label() {
      return uiText("On (force watching)");
    },
  },
  {
    value: "off",
    get label() {
      return uiText("Off (schedule only)");
    },
  },
];
const COLLECTION_MODES = [
  "mirror",
  "single",
] as const satisfies readonly ExternalLibraryCollectionMode[];

// A <select> hands back a bare string. These decode it into the domain type at
// that boundary; the elements only render the values below, so an unmatched
// value can only come from a tampered DOM and falls back to the default mode.
function parseWatchMode(value: string): ExternalLibraryWatchMode {
  return WATCH_OPTIONS.find((option) => option.value === value)?.value ?? "auto";
}

function parseCollectionMode(value: string): ExternalLibraryCollectionMode {
  return COLLECTION_MODES.find((collectionMode) => collectionMode === value) ?? "mirror";
}

function describeSchedule(cron: string): string {
  if (!cron) return uiText("Manual only");
  const preset = SCHEDULE_PRESETS.find((p) => p.cron === cron);
  return preset ? preset.label : uiText("Custom ({value1})", { value1: String(cron) });
}

function watchStatus(lib: ExternalLibrary): string {
  if (!lib.enabled) return uiText("Paused");
  if ((lib.source_kind ?? "mounted") !== "mounted") {
    return uiText("Remote source — bounded scheduled scans only");
  }
  if (lib.watch_active) {
    return lib.fs_kind === "network"
      ? uiText("Watching (forced — polling network folder)")
      : uiText("Watching (real-time)");
  }
  if (lib.watch_mode === "off") return uiText("Watching off — scheduled scans only");
  if (lib.fs_kind === "network") return uiText("Network folder — scheduled scans only");
  if (lib.fs_kind === "unknown") return uiText("Unknown filesystem — scheduled scans only");
  return uiText("Scheduled scans only");
}

interface ExternalLibraryBindingStatus {
  label: string;
  description: string;
  tone: "bound" | "recovery";
}

function bindingStatus(lib: ExternalLibrary): ExternalLibraryBindingStatus {
  const isMounted = (lib.source_kind ?? "mounted") === "mounted";
  if (lib.binding_state === "bound") {
    return {
      get label() {
        return uiText("Source verified");
      },
      description: isMounted
        ? uiText("This mounted root is verified for this PrintStash installation.")
        : uiText("This remote location is verified through its encrypted connection."),
      tone: "bound",
    };
  }
  if (lib.binding_state === "unbound") {
    return {
      get label() {
        return uiText("Needs enrollment");
      },
      get description() {
        return uiText(
          "This existing library has no root proof. Scans, watching, and writeback stay paused until you verify and enroll this exact path.",
        );
      },
      tone: "recovery",
    };
  }
  if (lib.binding_state === "missing") {
    return {
      get label() {
        return uiText("Root proof unavailable");
      },
      get description() {
        return uiText(
          "The root or its proof is unavailable. Scans, watching, and writeback stay paused until you verify the intended mount and enroll it again.",
        );
      },
      tone: "recovery",
    };
  }
  return {
    get label() {
      return uiText("Root binding blocked");
    },
    get description() {
      return uiText(
        "This root cannot be used safely. Scans, watching, and writeback stay paused; verify the intended mount and resolve the binding problem before continuing.",
      );
    },
    tone: "recovery",
  };
}

function ScheduleControl({
  value,
  onChange,
  disabled,
  inputClass,
  label,
}: {
  value: string;
  onChange: (cron: string) => void;
  disabled?: boolean;
  inputClass: string;
  label?: string;
}) {
  useUiLocale();
  const isPreset = PRESET_CRONS.includes(value);
  return (
    <div className="flex flex-col gap-2">
      <select
        className={inputClass}
        aria-label={label}
        value={isPreset ? value : CUSTOM_SENTINEL}
        disabled={disabled}
        onChange={(e) => {
          const next = e.target.value;
          // Switching to custom keeps a sensible editable starting point.
          onChange(next === CUSTOM_SENTINEL ? "0 */2 * * *" : next);
        }}
      >
        {SCHEDULE_PRESETS.map((p) => (
          <option key={p.cron || "manual"} value={p.cron}>
            {p.label}
          </option>
        ))}
        <option value={CUSTOM_SENTINEL}>{uiText("Custom cron…")}</option>
      </select>
      {!isPreset && (
        <input
          className={`${inputClass} font-mono`}
          placeholder={uiText("*/30 * * * * (min hour dom mon dow)")}
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </div>
  );
}

function formatDate(value: string | null | undefined): string {
  if (!value) return uiText("Never");
  return new Intl.DateTimeFormat(currentLocale(), {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function isLibrarySourceKind(value: string): value is LibrarySourceKind {
  return (
    value === "mounted" ||
    value === "s3" ||
    value === "webdav" ||
    value === "sftp" ||
    value === "gdrive"
  );
}

/** Existing injection supplies the same full DTO contracts as production readers/writers. */
export type ExternalLibrariesApi = LibrarySourcesApi;

export function ExternalLibrariesPanel(props: {
  canEdit: boolean;
  initialRootPath?: string;
  api?: ExternalLibrariesApi;
}) {
  const { user } = useAuth();
  const { t } = useI18n();
  if (!props.canEdit || !user?.is_superuser)
    return (
      <Card>
        <CardHeader>
          <CardTitle>{uiText("Library sources")}</CardTitle>
        </CardHeader>
        <CardContent>
          <p>{t("librarySources.adminRequired")}</p>
        </CardContent>
      </Card>
    );
  return <AdminLibrarySourcesPanel {...props} />;
}
function AdminLibrarySourcesPanel({
  canEdit,
  initialRootPath = "",
  api = defaultLibrarySourcesApi,
}: {
  canEdit: boolean;
  initialRootPath?: string;
  api?: ExternalLibrariesApi;
}) {
  useUiLocale();
  const { t } = useI18n();
  const { user } = useAuth();
  const allowed = !!user?.is_superuser && canEdit;
  const currentAllowed = useRef(allowed);
  useLayoutEffect(() => {
    currentAllowed.current = allowed;
  }, [allowed]);
  const live = useRef(true);
  const [retired, setRetired] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [configWriteDenied, setConfigWriteDenied] = useState(false);
  const draftRevision = useRef(0);
  useEffect(() => {
    live.current = true;
    const release = onAuthChange(() => setRetired(true));
    return () => {
      live.current = false;
      release();
    };
  }, []);
  const config = useQuery({
    ...vaultConfigOptions(api.getConfig),
    enabled: allowed && !retired,
    retry: false,
  });
  const configCommand = useVaultConfigCommand(api.updateConfig);
  const enabled = config.data?.external_libraries_enabled ?? false;
  const sources = useQuery({
    ...librarySourcesOptions(api.list),
    enabled: allowed && !retired && enabled,
  });
  const connectionRead = useQuery({
    ...storageConnectionsOptions(api.listConnections),
    enabled: allowed && !retired && enabled,
  });
  const command = useLibrarySourceCommand(api, allowed && !retired);
  const denied =
    configWriteDenied ||
    storageReadDenied(parseApiError(config.error)) ||
    storageReadDenied(parseApiError(sources.error)) ||
    storageReadDenied(parseApiError(command.error));
  const libraries =
    allowed && !retired && !denied && sources.data?.kind === "enabled"
      ? sources.data.items.map((row) =>
          command.intent?.id === row.id ? { ...row, ...command.intent.payload } : row,
        )
      : [];
  const connections =
    allowed && !retired && !storageReadDenied(parseApiError(connectionRead.error))
      ? (connectionRead.data ?? [])
      : [];
  const commandsAllowed =
    allowed &&
    !retired &&
    !denied &&
    !config.isError &&
    !sources.isError &&
    sources.data?.kind === "enabled";
  const busyId = command.busyId;
  const enableBusy = configCommand.isPending;
  const [toggleReview, setToggleReview] = useState<SourceToggleReview>({ phase: "idle" });
  const [editTarget, setEditTarget] = useState<ExternalLibrary | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<{
    source: ExternalLibrary;
    session: number;
    readVersion: number;
  } | null>(null);
  const [enrollTarget, setEnrollTarget] = useState<{
    source: ExternalLibrary;
    session: number;
    readVersion: number;
  } | null>(null);
  function current(session: number) {
    return live.current && currentAllowed.current && !retired && session === getSessionVersion();
  }
  // Add-library draft.
  const [name, setName] = useState("");
  const [rootPath, setRootPath] = useState(initialRootPath);
  const [sourceKind, setSourceKind] = useState<LibrarySourceKind>("mounted");
  const [connectionId, setConnectionId] = useState<number | "">("");
  const [sourcePrefix, setSourcePrefix] = useState("");
  const [scanSchedule, setScanSchedule] = useState("0 * * * *");
  const [watchMode, setWatchMode] = useState<ExternalLibraryWatchMode>("auto");
  const [mode, setMode] = useState<ExternalLibraryCollectionMode>("mirror");

  async function toggleFeature(next: boolean, reviewed?: VaultConfigRead) {
    if (
      !allowed ||
      config.isError ||
      !config.data ||
      enableBusy ||
      (toggleReview.phase !== "idle" && !reviewed)
    )
      return;
    const session = getSessionVersion();
    try {
      await configCommand.mutateAsync({
        session,
        payload: { external_libraries_enabled: next },
        base: captureEditingBase(reviewed ?? config.data),
      });
      if (current(session)) {
        setToggleReview({ phase: "idle" });
        toast.success(
          next ? uiText("Library sources enabled.") : uiText("Library sources disabled."),
        );
      }
    } catch (error) {
      if (current(session)) {
        if (storageReadDenied(parseApiError(error))) setConfigWriteDenied(true);
        const status = parseApiError(error).status;
        if (status === 412 || status === 428 || status === 0 || status >= 500)
          setToggleReview({
            phase: "required",
            next,
            problem: status === 412 || status === 428 ? "conflict" : "unconfirmed",
          });
        toast.error(error);
      }
    }
  }
  async function reviewToggle() {
    if (toggleReview.phase === "idle" || toggleReview.phase === "loading" || enableBusy) return;
    const session = getSessionVersion();
    const { next, problem } = toggleReview;
    setToggleReview({ phase: "loading", next, problem });
    try {
      const result = await config.refetch({ throwOnError: true });
      if (!result.data) throw new Error("Configuration review has no data");
      if (current(session))
        setToggleReview({ phase: "ready", next, problem, snapshot: result.data });
    } catch (error) {
      if (current(session)) {
        setToggleReview({ phase: "required", next, problem });
        if (storageReadDenied(parseApiError(error))) setConfigWriteDenied(true);
        toast.error(error);
      }
    }
  }
  async function recoverConfig() {
    const session = getSessionVersion();
    const result = await config.refetch();
    if (current(session) && !result.error) setConfigWriteDenied(false);
  }
  async function recoverSources() {
    const session = getSessionVersion();
    const result = await sources.refetch();
    if (current(session) && !result.error) command.clearError();
  }
  async function handleCreate() {
    if (!commandsAllowed || busyId !== null || (sourceKind !== "mounted" && connectionRead.isError))
      return;
    if (!name.trim() || (sourceKind === "mounted" ? !rootPath.trim() : connectionId === "")) {
      toast.error(
        sourceKind === "mounted"
          ? uiText("Name and folder path are required.")
          : uiText("Name and a compatible remote connection are required."),
      );
      return;
    }
    if (
      sourceKind !== "mounted" &&
      !connections.some(
        (row) =>
          row.id === connectionId &&
          row.kind === sourceKind &&
          row.enabled &&
          ["library", "both"].includes(row.purpose),
      )
    )
      return;
    const session = getSessionVersion();
    const revision = draftRevision.current;
    const payload: ExternalLibraryCreate = {
      name: name.trim(),
      root_path: sourceKind === "mounted" ? rootPath.trim() : undefined,
      scan_schedule: scanSchedule,
      watch_mode: sourceKind === "mounted" ? watchMode : "off",
      collection_mode: mode,
    };
    if (sourceKind !== "mounted") {
      payload.source_kind = sourceKind;
      payload.connection_id = connectionId === "" ? null : connectionId;
      payload.source_prefix = sourcePrefix.trim();
    }
    try {
      await command.mutateAsync({ session, kind: "create", payload });
      if (!current(session)) return;
      if (draftRevision.current === revision) {
        setName("");
        setRootPath("");
        setSourceKind("mounted");
        setConnectionId("");
        setSourcePrefix("");
        setScanSchedule("0 * * * *");
        setWatchMode("auto");
        setMode("mirror");
        setAddOpen(false);
      }
      toast.success(uiText("Library source added."));
    } catch (error) {
      if (current(session)) toast.error(error);
    }
  }
  async function handleScan(lib: ExternalLibrary) {
    if (!commandsAllowed || busyId !== null || command.blocked) return;
    const session = getSessionVersion();
    try {
      await command.mutateAsync({
        session,
        kind: "scan",
        id: lib.id,
        title: uiText("Scan {value1}", { value1: lib.name }),
      });
      if (current(session))
        toast.success(uiText('Scan complete for "{value1}".', { value1: lib.name }));
    } catch (error) {
      if (current(session)) toast.error(error);
    }
  }
  async function handleToggleEnabled(lib: ExternalLibrary) {
    await handleUpdate(lib, { enabled: !lib.enabled });
  }
  async function handleUpdate(
    lib: ExternalLibrary,
    payload: import("@/types").ExternalLibraryUpdate,
  ) {
    if (!commandsAllowed || busyId !== null || command.blocked) return;
    const session = getSessionVersion();
    try {
      await command.mutateAsync({
        session,
        kind: "update",
        id: lib.id,
        base: captureEditingBase(lib),
        payload,
      });
    } catch (error) {
      if (current(session)) toast.error(error);
    }
  }
  async function handleEnroll(target: { source: ExternalLibrary; session: number }) {
    if (!commandsAllowed || busyId !== null || command.blocked) return;
    try {
      await command.mutateAsync({
        session: target.session,
        kind: "enroll",
        id: target.source.id,
        root: target.source.root_path,
      });
      if (!current(target.session)) return;
      toast.success(uiText("Root verified. Rescan to resume indexing."));
      setEnrollTarget((previous) => (previous === target ? null : previous));
    } catch (error) {
      if (current(target.session)) toast.error(error);
    }
  }
  async function handleDelete(target: { source: ExternalLibrary; session: number }) {
    if (!commandsAllowed || busyId !== null || command.blocked) return;
    try {
      await command.mutateAsync({ session: target.session, kind: "delete", id: target.source.id });
      if (!current(target.session)) return;
      toast.success(
        uiText('Removed "{value1}". Source files were not touched.', {
          value1: target.source.name,
        }),
      );
      setDeleteTarget((previous) => (previous === target ? null : previous));
    } catch (error) {
      if (current(target.session)) toast.error(error);
    }
  }
  if (retired) return null;
  if (!allowed || !config.data || denied)
    return (
      <Card>
        <CardHeader>
          <CardTitle>{uiText("Library sources")}</CardTitle>
        </CardHeader>
        <CardContent>
          {!allowed ? (
            <p>{t("librarySources.adminRequired")}</p>
          ) : config.isPending ? (
            <Skeleton className="h-20 w-full" />
          ) : (
            <div role="alert">
              <p>
                {t(
                  config.isError || configWriteDenied
                    ? "librarySources.configFailed"
                    : "librarySources.sourcesFailed",
                )}
              </p>
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  void (config.isError || configWriteDenied ? recoverConfig() : recoverSources())
                }
              >
                {t("Retry")}
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
    );

  return (
    <Localized>
      <div className="overflow-hidden border border-border bg-card">
        <SettingsGroupHead
          icon={FolderSync}
          level={3}
          title={uiText("Scanning")}
          description={uiText("Applies to every Library source.")}
          className="settings-card-head--flush"
        />
        <div className="settings-section-body">
          <div className="settings-preference-row">
            <span>{uiText("Library sources enabled")}</span>
            <Switch
              className="settings-row-switch"
              checked={enabled}
              onChange={(value) => toggleFeature(value)}
              label={uiText("Library sources enabled")}
              stateLabels={{ on: uiText("On"), off: uiText("Off") }}
              disabled={!allowed || config.isError || enableBusy || toggleReview.phase !== "idle"}
            />
          </div>
          <div className="settings-preference-row">
            <span>{uiText("Auto-scan enabled")}</span>
            <span className="text-xs text-muted-foreground">{uiText("Configured per source")}</span>
          </div>
          <div className="settings-preference-row">
            <span>{uiText("Scan schedule")}</span>
            <span className="text-xs text-muted-foreground">{uiText("Configured per source")}</span>
          </div>
        </div>

        {toggleReview.phase !== "idle" && (
          <div role="alert" className="space-y-3 p-4">
            <p>
              {uiText(
                toggleReview.problem === "conflict"
                  ? "library.editConflict"
                  : "library.saveUnconfirmed",
              )}
            </p>
            <Button
              size="sm"
              variant="outline"
              disabled={enableBusy || toggleReview.phase === "loading"}
              onClick={() => void reviewToggle()}
            >
              {uiText("library.reviewLatest")}
            </Button>
            {toggleReview.phase === "ready" && (
              <section aria-label={uiText("library.latestVersion")} className="space-y-3">
                <h3 className="text-sm font-semibold">{uiText("library.latestVersion")}</h3>
                <p>
                  {uiText("Library sources")}:{" "}
                  {uiText(
                    toggleReview.snapshot.external_libraries_enabled ? "Enabled" : "Disabled",
                  )}
                </p>
                <div className="flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={enableBusy || config.isError}
                    onClick={() => setToggleReview({ phase: "idle" })}
                  >
                    {uiText("library.useLatest")}
                  </Button>
                  <Button
                    size="sm"
                    disabled={enableBusy || config.isError}
                    onClick={() => void toggleFeature(toggleReview.next, toggleReview.snapshot)}
                  >
                    {uiText("library.retryDraft")}
                  </Button>
                </div>
              </section>
            )}
          </div>
        )}
        {config.isError && (
          <div role="alert" className="p-4">
            <p>{t("librarySources.configFailed")}</p>
            <Button size="sm" variant="outline" onClick={() => void recoverConfig()}>
              {t("Retry")}
            </Button>
          </div>
        )}
        {enabled && !denied && command.review.phase !== "idle" && (
          <section role="alert" className="space-y-3 rounded border border-border p-4">
            <p>
              {uiText(
                command.review.phase === "retired"
                  ? "storage.connectionUnavailable"
                  : command.review.problem === "conflict"
                    ? "library.editConflict"
                    : "library.saveUnconfirmed",
              )}
            </p>
            {command.intent?.payload.scan_schedule !== undefined && (
              <p>
                {uiText("Scan schedule")}: {describeSchedule(command.intent.payload.scan_schedule)}
              </p>
            )}
            {command.intent?.payload.enabled !== undefined && (
              <p>
                {uiText("Auto-scan enabled")}:{" "}
                {uiText(command.intent.payload.enabled ? "Enabled" : "Disabled")}
              </p>
            )}
            {command.review.phase === "ready" && (
              <section aria-label={uiText("library.latestVersion")}>
                <p>{command.review.snapshot.name}</p>
                <p>{command.review.snapshot.root_path}</p>
                <p>{describeSchedule(command.review.snapshot.scan_schedule)}</p>
                <p>{watchStatus(command.review.snapshot)}</p>
                <p>{uiText(command.review.snapshot.enabled ? "Enabled" : "Disabled")}</p>
              </section>
            )}
            <div className="flex flex-wrap gap-2">
              {command.review.phase !== "retired" && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busyId !== null || command.review.phase === "loading"}
                  onClick={() => {
                    const session = getSessionVersion();
                    void command.reviewLatest().catch((error) => {
                      if (current(session)) toast.error(error);
                    });
                  }}
                >
                  {uiText("Review current values")}
                </Button>
              )}
              {command.review.phase === "ready" && (
                <>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busyId !== null}
                    onClick={() => {
                      const session = getSessionVersion();
                      void command.adopt().catch((error) => {
                        if (current(session)) toast.error(error);
                      });
                    }}
                  >
                    {uiText("Use current values")}
                  </Button>
                  <Button
                    size="sm"
                    disabled={busyId !== null || !command.review.sameIdentity}
                    onClick={() => {
                      const session = getSessionVersion();
                      void command.saveRevised().catch((error) => {
                        if (current(session)) toast.error(error);
                      });
                    }}
                  >
                    {uiText("Save revised changes")}
                  </Button>
                </>
              )}
            </div>
          </section>
        )}
        <SettingsGroupHead
          icon={FolderSync}
          level={3}
          title={uiText("Sources")}
          description={uiText(
            "PrintStash stores catalog metadata and thumbnails only; source files stay in their original location.",
          )}
          action={
            <Button
              size="sm"
              disabled={!enabled || !commandsAllowed}
              onClick={() => setAddOpen(true)}
            >
              {uiText("Add source")}
            </Button>
          }
          className="settings-card-head--flush border-t"
        />
        {enabled && (
          <div className="settings-section-body space-y-4">
            {(sources.isError || sources.data?.kind === "disabled") && (
              <div role="alert">
                <p>{t("librarySources.sourcesFailed")}</p>
                <Button size="sm" variant="outline" onClick={() => void recoverSources()}>
                  {t("Retry")}
                </Button>
              </div>
            )}
            {sources.isPending && <Skeleton className="h-24 w-full" />}
            {/* Existing libraries */}
            {!sources.isPending &&
            !sources.isError &&
            sources.data?.kind === "enabled" &&
            libraries.length === 0 ? (
              <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border bg-muted/20 px-6 py-8 text-center">
                <FolderSync className="h-7 w-7 text-muted-foreground/50" />
                <p className="text-sm font-medium text-foreground">
                  {uiText("No library sources yet")}
                </p>
                <p className="text-xs text-muted-foreground">
                  {uiText(
                    "Add a mounted folder or connect remote storage to index existing models without copying them into the Vault.",
                  )}
                </p>
              </div>
            ) : (
              <ul>
                {libraries.map((lib) => {
                  const busy = busyId === lib.id || command.blocked;
                  const s = lib.last_scan_summary;
                  const binding = bindingStatus(lib);
                  const rootBound = lib.binding_state === "bound";
                  return (
                    <li key={lib.id} className="settings-list-row items-start">
                      <div className="min-w-0 flex-1 space-y-0.5">
                        <p className="settings-list-title truncate">
                          {lib.name}
                          {!lib.enabled && (
                            <span className="font-normal text-muted-foreground">
                              {" · "}
                              {uiText("paused")}
                            </span>
                          )}
                        </p>
                        <p className="settings-list-meta truncate font-mono">
                          {describeSourceLocation(lib, connections)}
                        </p>
                        <p className="settings-list-meta">
                          {[
                            (lib.source_kind ?? "mounted") !== "mounted"
                              ? uiText("{value1} · remote source · read-only", {
                                  value1: String((lib.source_kind ?? "mounted").toUpperCase()),
                                })
                              : null,
                            lib.collection_mode === "mirror"
                              ? uiText("Mirrors subfolders → collections")
                              : uiText("Single collection"),
                            describeSchedule(lib.scan_schedule),
                            watchStatus(lib),
                            uiText("Last scan {date}", { date: formatDate(lib.last_scanned_at) }),
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                        </p>
                        {(lib.last_scan_status === "ok" || lib.last_scan_status === "partial") &&
                          s && (
                            <p className="settings-list-meta">
                              +{s.added}
                              {uiText(" added · ")}
                              {s.updated}
                              {uiText(" updated · ")}
                              {s.removed}
                              {uiText(" removed")}
                              {s.errors.length > 0
                                ? uiText(" · {value1} errors", {
                                    value1: String(s.errors.length),
                                  })
                                : ""}
                            </p>
                          )}
                        {lib.last_scan_status === "error" && (
                          <p className="text-xs text-destructive">
                            {s?.error || uiText("Last scan failed")}
                          </p>
                        )}
                        {lib.last_scan_status === "partial" && (
                          <p className="text-xs text-destructive">
                            {uiText("Some files could not be indexed")}
                          </p>
                        )}
                        {!rootBound && (
                          <SettingsNotice
                            tone="warning"
                            role="alert"
                            className="mt-2"
                            title={binding.label}
                            action={
                              lib.root_enrollable && commandsAllowed ? (
                                <button
                                  type="button"
                                  className={BTN_SECONDARY}
                                  disabled={busy}
                                  onClick={() =>
                                    setEnrollTarget({
                                      source: lib,
                                      session: getSessionVersion(),
                                      readVersion: sources.dataUpdatedAt,
                                    })
                                  }
                                >
                                  {uiText("Review and enroll")}
                                </button>
                              ) : undefined
                            }
                          >
                            <p>{binding.description}</p>
                            {lib.binding_reason && (
                              <p className="font-mono">{lib.binding_reason}</p>
                            )}
                          </SettingsNotice>
                        )}
                        {(lib.last_scan_status === "ok" || lib.last_scan_status === "partial") &&
                          s?.no_files_found === true && (
                            <SettingsNotice tone="warning" role="status" className="mt-2">
                              <p>
                                {uiText(
                                  "No model files found at {value1}. Check the folder name: remote paths are case-sensitive.",
                                  { value1: describeSourceLocation(lib, connections) },
                                )}{" "}
                                {(lib.source_kind ?? "mounted") !== "mounted" && (
                                  <Link
                                    href="/settings?section=remote-storage"
                                    className="font-medium underline"
                                  >
                                    {uiText("Edit connection")}
                                  </Link>
                                )}
                              </p>
                            </SettingsNotice>
                          )}
                        {commandsAllowed && (
                          <div className="mt-2 flex flex-wrap gap-2">
                            <ScheduleControl
                              label={`${uiText("Scan schedule")} ${lib.name}`}
                              value={lib.scan_schedule}
                              disabled={busy}
                              inputClass={cn(INPUT, "w-56")}
                              onChange={(cron) => handleUpdate(lib, { scan_schedule: cron })}
                            />
                            {(lib.source_kind ?? "mounted") === "mounted" && (
                              <select
                                className={cn(INPUT, "w-56 self-start")}
                                value={lib.watch_mode}
                                disabled={busy}
                                onChange={(e) =>
                                  handleUpdate(lib, {
                                    watch_mode: parseWatchMode(e.target.value),
                                  })
                                }
                              >
                                {WATCH_OPTIONS.map((o) => (
                                  <option key={o.value} value={o.value}>
                                    {o.label}
                                  </option>
                                ))}
                              </select>
                            )}
                          </div>
                        )}
                      </div>
                      <div>
                        {rootBound && <SettingsStatus>{binding.label}</SettingsStatus>}
                        <Switch
                          checked={lib.enabled}
                          onChange={() => handleToggleEnabled(lib)}
                          label={uiText("Auto-scan enabled")}
                          stateLabels={{ on: uiText("Auto-scan on"), off: uiText("Auto-scan off") }}
                          disabled={
                            !commandsAllowed || command.blocked || busyId !== null || !rootBound
                          }
                        />
                        <button
                          type="button"
                          disabled={
                            !commandsAllowed || command.blocked || busyId !== null || !rootBound
                          }
                          onClick={() => handleScan(lib)}
                          title={
                            rootBound ? undefined : uiText("Verify the source before scanning.")
                          }
                          className={BTN_SECONDARY}
                        >
                          {busyId === lib.id && (
                            <RefreshCw className="h-3.5 w-3.5 animate-spin" aria-hidden />
                          )}
                          {busyId === lib.id ? uiText("Scanning") : uiText("Scan now")}
                        </button>
                        <button
                          type="button"
                          disabled={!commandsAllowed || command.blocked || busyId !== null}
                          onClick={() => setEditTarget(lib)}
                          className={BTN_SECONDARY}
                        >
                          {uiText("Edit source")}
                        </button>
                        <button
                          type="button"
                          disabled={!commandsAllowed || command.blocked || busyId !== null}
                          onClick={() =>
                            setDeleteTarget({
                              source: lib,
                              session: getSessionVersion(),
                              readVersion: sources.dataUpdatedAt,
                            })
                          }
                          className={SETTINGS_BUTTON_DANGER}
                          aria-label={uiText("Remove library source")}
                        >
                          {uiText("Remove…")}
                        </button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}

            <SettingsNotice tone="info">
              <p>
                {uiText(
                  "Remote connection profiles are managed in Settings → Remote storage. Create one there, allow Library sources, then select it below.",
                )}
              </p>
            </SettingsNotice>

            {connectionRead.isError && (
              <SettingsNotice
                tone="danger"
                role="alert"
                title={t("librarySources.connectionsFailed")}
                action={
                  <Button variant="outline" size="sm" onClick={() => void connectionRead.refetch()}>
                    {t("Retry")}
                  </Button>
                }
              />
            )}
            {/* Add a library source */}
            <Modal
              open={addOpen}
              onClose={() => {
                if (busyId !== "create") setAddOpen(false);
              }}
              title={uiText("Add a library source")}
            >
              <div
                className="space-y-3"
                onChangeCapture={() => {
                  draftRevision.current += 1;
                }}
              >
                <div className="grid gap-3 sm:grid-cols-2">
                  <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                    {uiText("Source name")}
                    <input
                      className={INPUT}
                      aria-label={uiText("Source name")}
                      placeholder={uiText("e.g. Workshop NAS")}
                      value={name}
                      disabled={!commandsAllowed}
                      onChange={(e) => setName(e.target.value)}
                    />
                  </label>
                  <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                    {uiText("Source type")}
                    <select
                      className={INPUT}
                      aria-label={uiText("Library source type")}
                      value={sourceKind}
                      disabled={!commandsAllowed}
                      onChange={(event) => {
                        if (!isLibrarySourceKind(event.target.value)) return;
                        setSourceKind(event.target.value);
                        setConnectionId("");
                      }}
                    >
                      <option value="mounted">{uiText("Mounted folder (SMB/NFS/local)")}</option>
                      <option value="s3">{uiText("S3 / compatible")}</option>
                      <option value="webdav">{uiText("WebDAV / Nextcloud")}</option>
                      <option value="sftp">SFTP</option>
                      <option value="gdrive">{uiText("Google Drive")}</option>
                    </select>
                  </label>
                  {sourceKind === "mounted" ? (
                    <label className="flex flex-col gap-1 text-xs text-muted-foreground sm:col-span-2">
                      {uiText("Mounted folder path")}
                      <input
                        className={INPUT}
                        aria-label={uiText("Mounted folder path")}
                        placeholder={uiText("e.g. /mnt/nas/3d")}
                        value={rootPath}
                        disabled={!commandsAllowed}
                        onChange={(e) => setRootPath(e.target.value)}
                      />
                    </label>
                  ) : (
                    <>
                      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                        {uiText("Remote connection")}
                        <select
                          className={INPUT}
                          aria-label={uiText("Remote source connection")}
                          value={connectionId}
                          disabled={
                            !commandsAllowed || connectionRead.isPending || connectionRead.isError
                          }
                          onChange={(event) =>
                            setConnectionId(event.target.value ? Number(event.target.value) : "")
                          }
                        >
                          <option value="">{uiText("Choose an enabled connection")}</option>
                          {connections
                            .filter(
                              (connection) =>
                                connection.kind === sourceKind &&
                                connection.enabled &&
                                ["library", "both"].includes(connection.purpose ?? "library"),
                            )
                            .map((connection) => (
                              <option key={connection.id} value={connection.id}>
                                {connection.name}
                              </option>
                            ))}
                        </select>
                      </label>
                      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                        {uiText("Path within connection (optional)")}
                        <input
                          className={INPUT}
                          aria-label={uiText("Source path within connection")}
                          placeholder={uiText("e.g. production/models")}
                          value={sourcePrefix}
                          disabled={!commandsAllowed}
                          onChange={(event) => setSourcePrefix(event.target.value)}
                        />
                      </label>
                    </>
                  )}
                  <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                    {uiText("Scan schedule")}
                    <ScheduleControl
                      value={scanSchedule}
                      disabled={!commandsAllowed}
                      inputClass={INPUT}
                      onChange={setScanSchedule}
                    />
                  </label>
                  {sourceKind === "mounted" && (
                    <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                      {uiText("Real-time watching")}
                      <select
                        className={INPUT}
                        value={watchMode}
                        disabled={!commandsAllowed}
                        onChange={(e) => setWatchMode(parseWatchMode(e.target.value))}
                      >
                        {WATCH_OPTIONS.map((o) => (
                          <option key={o.value} value={o.value}>
                            {o.label}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                    {uiText("Collection layout")}
                    <select
                      className={INPUT}
                      aria-label={uiText("Collection layout")}
                      value={mode}
                      disabled={!commandsAllowed}
                      onChange={(e) => setMode(parseCollectionMode(e.target.value))}
                    >
                      <option value="mirror">{uiText("Map subfolders to collections")}</option>
                      <option value="single">{uiText("Single collection (flat)")}</option>
                    </select>
                  </label>
                </div>
                <p className="text-2xs text-muted-foreground">
                  {uiText(
                    "PrintStash stores catalog metadata and thumbnails only; source files stay in their original location.",
                  )}{" "}
                  {sourceKind === "mounted"
                    ? uiText(
                        "Mounted sources support manual and scheduled scans. Local folders can also be watched and may accept create-only write-back.",
                      )
                    : uiText(
                        "Remote sources use bounded manual or scheduled scans and are always read-only.",
                      )}
                </p>
                <div className="flex justify-end">
                  <button
                    type="button"
                    disabled={
                      !commandsAllowed ||
                      busyId !== null ||
                      (sourceKind !== "mounted" &&
                        (connectionRead.isPending || connectionRead.isError))
                    }
                    onClick={handleCreate}
                    className={BTN_PRIMARY}
                  >
                    <Plus className="h-3.5 w-3.5" />
                    {busyId === "create" ? uiText("Adding") : uiText("Add source")}
                  </button>
                </div>
              </div>
            </Modal>
          </div>
        )}

        {commandsAllowed && (
          <ConfirmModal
            open={deleteTarget !== null && deleteTarget.readVersion === sources.dataUpdatedAt}
            onClose={() => setDeleteTarget(null)}
            title={uiText("Remove library source?")}
            description={
              (deleteTarget
                ? uiText(
                    '"{value1}" will be removed and its indexed models moved to trash. Source files remain untouched in their mounted folder or remote storage.',
                    { value1: String(deleteTarget.source.name) },
                  )
                : "") + (command.error ? `\n\n${userMessage(command.error)}` : "")
            }
            confirmLabel={uiText("Remove")}
            busy={deleteTarget !== null && busyId === deleteTarget.source.id}
            onConfirm={() => deleteTarget && handleDelete(deleteTarget)}
          />
        )}
        {commandsAllowed && (
          <ConfirmModal
            open={enrollTarget !== null && enrollTarget.readVersion === sources.dataUpdatedAt}
            onClose={() => {
              if (busyId === null) setEnrollTarget(null);
            }}
            title={uiText("Enroll mounted source root?")}
            description={
              (enrollTarget
                ? uiText(
                    "Verify that this exact mounted path belongs to this PrintStash installation before enrolling it: {value1}. This re-enables safe scans, watching, and writeback.",
                    { value1: String(enrollTarget.source.root_path) },
                  )
                : "") + (command.error ? `\n\n${userMessage(command.error)}` : "")
            }
            confirmLabel={uiText("Enroll root")}
            busy={enrollTarget !== null && busyId === enrollTarget.source.id}
            onConfirm={() => enrollTarget && handleEnroll(enrollTarget)}
          />
        )}
        {editTarget && (
          <EditLibrarySourceModal
            key={editTarget.id}
            source={editTarget}
            location={describeSourceLocation({ ...editTarget, source_prefix: "" }, connections)}
            disabled={!commandsAllowed || command.blocked || busyId !== null}
            onSave={(payload) => void handleUpdate(editTarget, payload)}
            onClose={() => setEditTarget(null)}
          />
        )}
      </div>
    </Localized>
  );
}
