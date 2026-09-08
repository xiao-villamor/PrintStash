import { uiMessage } from "@/lib/locale";
import { currentLocale } from "@/lib/locale";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  FolderSync,
  HardDrive,
  Plus,
  RefreshCw,
  ShieldAlert,
  Trash2,
} from "lucide-react";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import {
  createExternalLibrary,
  deleteExternalLibrary,
  getJobStatus,
  getVaultConfig,
  enrollExternalLibraryRoot,
  listExternalLibraries,
  listStorageConnections,
  scanExternalLibrary,
  updateExternalLibrary,
  updateVaultConfig,
} from "@/lib/api";
import { toast } from "@/lib/toast";
import { Localized } from "@/components/ui/localized";
import { trackImportJob } from "@/lib/task-center";
import type {
  ExternalLibrary,
  ExternalLibraryCollectionMode,
  ExternalLibraryCreate,
  ExternalLibraryWatchMode,
  LibrarySourceKind,
  StorageConnection,
} from "@/types";

const BTN_PRIMARY =
  "inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded bg-primary text-primary-foreground text-xs font-medium uppercase tracking-wider hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed";
const BTN_SECONDARY =
  "inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded border border-border text-muted-foreground hover:bg-muted transition-colors text-xs font-medium uppercase tracking-wider disabled:opacity-50 disabled:cursor-not-allowed";
const INPUT =
  "w-full px-3 py-2 bg-background border border-border rounded text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent disabled:opacity-50";

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
}: {
  value: string;
  onChange: (cron: string) => void;
  disabled?: boolean;
  inputClass: string;
}) {
  useUiLocale();
  const isPreset = PRESET_CRONS.includes(value);
  return (
    <div className="flex flex-col gap-2">
      <select
        className={inputClass}
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

/**
 * The API this panel drives. Declared as a port so a test can render the panel
 * against a stub; production callers get {@link VAULT_API}, which wires the real
 * `@/lib/api` calls. The two config calls are narrowed to the one flag the panel
 * cares about.
 */
export interface ExternalLibrariesApi {
  isFeatureEnabled: () => Promise<boolean>;
  setFeatureEnabled: (enabled: boolean) => Promise<void>;
  list: typeof listExternalLibraries;
  enroll: typeof enrollExternalLibraryRoot;
  create: typeof createExternalLibrary;
  update: typeof updateExternalLibrary;
  remove: typeof deleteExternalLibrary;
  scan: typeof scanExternalLibrary;
  jobStatus: typeof getJobStatus;
  listConnections?: typeof listStorageConnections;
}

const VAULT_API: ExternalLibrariesApi = {
  isFeatureEnabled: async () => (await getVaultConfig()).external_libraries_enabled,
  setFeatureEnabled: async (enabled) => {
    await updateVaultConfig({ external_libraries_enabled: enabled });
  },
  list: listExternalLibraries,
  enroll: enrollExternalLibraryRoot,
  create: createExternalLibrary,
  update: updateExternalLibrary,
  remove: deleteExternalLibrary,
  scan: scanExternalLibrary,
  jobStatus: getJobStatus,
  listConnections: listStorageConnections,
};

async function pollScanJob(
  jobId: string,
  jobStatus: ExternalLibrariesApi["jobStatus"],
): Promise<void> {
  // Mirrors the upload modal's polling: wait until the scan job terminates.
  const deadline = Date.now() + 15 * 60 * 1000;
  while (Date.now() < deadline) {
    const job = await jobStatus(jobId);
    if (job.state === "completed") return;
    if (job.state === "failed") {
      throw new Error(job.error || "scan_failed");
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error("scan_timeout");
}

export function ExternalLibrariesPanel({
  canEdit,
  initialRootPath = "",
  api = VAULT_API,
}: {
  canEdit: boolean;
  initialRootPath?: string;
  api?: ExternalLibrariesApi;
}) {
  useUiLocale();
  const [enabled, setEnabled] = useState(false);
  const [enableBusy, setEnableBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const [libraries, setLibraries] = useState<ExternalLibrary[]>([]);
  const [connections, setConnections] = useState<StorageConnection[]>([]);
  const [busyId, setBusyId] = useState<number | "create" | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ExternalLibrary | null>(null);
  const [enrollTarget, setEnrollTarget] = useState<ExternalLibrary | null>(null);

  // Add-library draft.
  const [name, setName] = useState("");
  const [rootPath, setRootPath] = useState(initialRootPath);
  const [sourceKind, setSourceKind] = useState<LibrarySourceKind>("mounted");
  const [connectionId, setConnectionId] = useState<number | "">("");
  const [sourcePrefix, setSourcePrefix] = useState("");
  const [scanSchedule, setScanSchedule] = useState("0 * * * *");
  const [watchMode, setWatchMode] = useState<ExternalLibraryWatchMode>("auto");
  const [mode, setMode] = useState<ExternalLibraryCollectionMode>("mirror");

  const refresh = useCallback(async () => {
    try {
      setLibraries(await api.list());
      if (api.listConnections) setConnections(await api.listConnections());
    } catch (e) {
      toast.error(e);
    }
  }, [api]);

  useEffect(() => {
    let cancelled = false;
    api
      .isFeatureEnabled()
      .then((featureEnabled) => {
        if (cancelled) return;
        setEnabled(featureEnabled);
        setLoaded(true);
        if (featureEnabled) refresh();
      })
      .catch(() => setLoaded(true));
    return () => {
      cancelled = true;
    };
  }, [api, refresh]);

  async function toggleFeature(next: boolean) {
    setEnableBusy(true);
    setEnabled(next);
    try {
      await api.setFeatureEnabled(next);
      toast.success(
        next ? uiText("Library sources enabled.") : uiText("Library sources disabled."),
      );
      if (next) await refresh();
    } catch (e) {
      setEnabled(!next);
      toast.error(e);
    } finally {
      setEnableBusy(false);
    }
  }

  async function handleCreate() {
    if (!name.trim() || (sourceKind === "mounted" ? !rootPath.trim() : connectionId === "")) {
      toast.error(
        sourceKind === "mounted"
          ? uiText("Name and folder path are required.")
          : uiText("Name and a compatible remote connection are required."),
      );
      return;
    }
    setBusyId("create");
    try {
      const body: ExternalLibraryCreate = {
        name: name.trim(),
        root_path: sourceKind === "mounted" ? rootPath.trim() : undefined,
        scan_schedule: scanSchedule,
        watch_mode: sourceKind === "mounted" ? watchMode : "off",
        collection_mode: mode,
      };
      if (sourceKind !== "mounted") {
        body.source_kind = sourceKind;
        body.connection_id = connectionId === "" ? null : connectionId;
        body.source_prefix = sourcePrefix.trim();
      }
      await api.create(body);
      setName("");
      setRootPath("");
      setSourceKind("mounted");
      setConnectionId("");
      setSourcePrefix("");
      setScanSchedule("0 * * * *");
      setWatchMode("auto");
      setMode("mirror");
      toast.success(uiText("Library source added."));
      await refresh();
    } catch (e) {
      toast.error(e);
    } finally {
      setBusyId(null);
    }
  }

  async function handleScan(lib: ExternalLibrary) {
    setBusyId(lib.id);
    try {
      const resp = await api.scan(lib.id);
      trackImportJob(resp.job_id, uiMessage("Scan {value1}", { value1: String(lib.name) }));
      await pollScanJob(resp.job_id, api.jobStatus);
      toast.success(uiText('Scan complete for "{value1}".', { value1: String(lib.name) }));
      await refresh();
    } catch (e) {
      toast.error(e);
      await refresh();
    } finally {
      setBusyId(null);
    }
  }

  async function handleToggleEnabled(lib: ExternalLibrary) {
    setBusyId(lib.id);
    try {
      await api.update(lib.id, { enabled: !lib.enabled });
      await refresh();
    } catch (e) {
      toast.error(e);
    } finally {
      setBusyId(null);
    }
  }

  async function handleEnroll(lib: ExternalLibrary) {
    setBusyId(lib.id);
    try {
      await api.enroll(lib.id, { confirm_root_path: lib.root_path });
      toast.success(uiText("Root verified. Rescan to resume indexing."));
      setEnrollTarget(null);
      await refresh();
    } catch (e) {
      toast.error(e);
    } finally {
      setBusyId(null);
    }
  }

  async function handleUpdate(
    lib: ExternalLibrary,
    patch: { scan_schedule?: string; watch_mode?: ExternalLibraryWatchMode },
  ) {
    setBusyId(lib.id);
    try {
      await api.update(lib.id, patch);
      await refresh();
    } catch (e) {
      toast.error(e);
    } finally {
      setBusyId(null);
    }
  }

  async function handleDelete(lib: ExternalLibrary) {
    setBusyId(lib.id);
    try {
      await api.remove(lib.id);
      toast.success(
        uiText('Removed "{value1}". Source files were not touched.', { value1: String(lib.name) }),
      );
      await refresh();
    } catch (e) {
      toast.error(e);
    } finally {
      setBusyId(null);
      setDeleteTarget(null);
    }
  }

  if (!loaded) return null;

  return (
    <Localized>
      <div className="overflow-hidden rounded-lg border border-border bg-card shadow-sm">
        <div className="px-4 sm:px-5 py-3.5 border-b border-border flex items-start justify-between gap-3">
          <div className="flex items-start gap-3 min-w-0">
            <div className="w-8 h-8 rounded bg-muted flex items-center justify-center text-muted-foreground flex-shrink-0">
              <FolderSync className="h-4 w-4" />
            </div>
            <div className="min-w-0">
              <h3 className="text-sm font-semibold text-foreground">{uiText("Library sources")}</h3>
              <p className="text-xs text-muted-foreground mt-0.5">
                {uiText(
                  "Index existing models from mounted folders, S3, WebDAV, or SFTP without copying them into Vault storage. Source files stay externally owned and are never deleted by PrintStash. Off by default.",
                )}
              </p>
            </div>
          </div>
          <button
            type="button"
            role="switch"
            aria-label={uiText("Library sources enabled")}
            aria-checked={enabled}
            disabled={!canEdit || enableBusy}
            onClick={() => toggleFeature(!enabled)}
            className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-50 ${
              enabled ? "bg-primary" : "bg-outline-variant"
            }`}
          >
            <span
              className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                enabled ? "translate-x-6" : "translate-x-1"
              }`}
            />
          </button>
        </div>

        {enabled && (
          <div className="p-4 sm:p-5 space-y-5">
            {/* Existing libraries */}
            {libraries.length === 0 ? (
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
              <ul className="space-y-3">
                {libraries.map((lib) => {
                  const busy = busyId === lib.id;
                  const s = lib.last_scan_summary;
                  const binding = bindingStatus(lib);
                  const rootBound = lib.binding_state === "bound";
                  return (
                    <li
                      key={lib.id}
                      className="rounded border border-border bg-background p-3 sm:p-4"
                    >
                      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                        <div className="min-w-0 w-full">
                          <div className="flex items-center gap-2">
                            <HardDrive className="h-3.5 w-3.5 text-muted-foreground" />
                            <span className="text-sm font-medium text-foreground truncate">
                              {lib.name}
                            </span>
                            {!lib.enabled && (
                              <span className="font-mono text-3xs uppercase tracking-wider text-muted-foreground/70 border border-border rounded px-1.5 py-0.5">
                                {uiText("paused")}
                              </span>
                            )}
                          </div>
                          <p className="text-xs text-muted-foreground font-mono mt-1 truncate">
                            {lib.root_path}
                          </p>
                          {(lib.source_kind ?? "mounted") !== "mounted" && (
                            <p className="mt-1 text-2xs text-muted-foreground">
                              {uiText("{value1} · remote source · read-only", {
                                value1: String((lib.source_kind ?? "mounted").toUpperCase() ?? ""),
                              })}
                            </p>
                          )}
                          <div
                            className={`mt-2 flex items-start gap-2 rounded border p-2 ${
                              binding.tone === "bound"
                                ? "border-success/30 bg-success/10"
                                : "border-warning/30 bg-warning/10"
                            }`}
                            role={rootBound ? undefined : "alert"}
                          >
                            {rootBound ? (
                              <CheckCircle2
                                className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success"
                                aria-hidden
                              />
                            ) : (
                              <ShieldAlert
                                className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning"
                                aria-hidden
                              />
                            )}
                            <div className="min-w-0">
                              <p className="text-2xs font-semibold text-foreground">
                                {binding.label}
                              </p>
                              <p className="mt-0.5 text-2xs leading-relaxed text-muted-foreground">
                                {binding.description}
                              </p>
                              {lib.binding_reason && !rootBound && (
                                <p className="mt-0.5 font-mono text-3xs text-muted-foreground">
                                  {lib.binding_reason}
                                </p>
                              )}
                              {lib.root_enrollable && canEdit && (
                                <button
                                  type="button"
                                  className={`${BTN_SECONDARY} mt-2`}
                                  disabled={busy}
                                  onClick={() => setEnrollTarget(lib)}
                                >
                                  {uiText("Review and enroll")}
                                </button>
                              )}
                            </div>
                          </div>
                          <p className="text-2xs text-muted-foreground mt-1">
                            {lib.collection_mode === "mirror"
                              ? uiText("Mirrors subfolders → collections")
                              : uiText("Single collection")}{" "}
                            · {describeSchedule(lib.scan_schedule)}
                            {uiText(" · last scan")} {formatDate(lib.last_scanned_at)}
                          </p>
                          <p className="text-2xs text-muted-foreground mt-0.5">
                            {watchStatus(lib)}
                          </p>
                          {canEdit && (
                            <div className="mt-2 grid gap-2 sm:grid-cols-2 max-w-md">
                              <ScheduleControl
                                value={lib.scan_schedule}
                                disabled={busy}
                                inputClass={`${INPUT} !py-1.5 text-xs`}
                                onChange={(cron) => handleUpdate(lib, { scan_schedule: cron })}
                              />
                              {(lib.source_kind ?? "mounted") === "mounted" && (
                                <select
                                  className={`${INPUT} !py-1.5 text-xs self-start`}
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
                          {lib.last_scan_status === "error" && (
                            <p className="mt-1 inline-flex items-center gap-1 text-2xs text-destructive">
                              <AlertTriangle className="h-3 w-3" />
                              {s?.error || uiText("Last scan failed")}
                            </p>
                          )}
                          {(lib.last_scan_status === "ok" || lib.last_scan_status === "partial") &&
                            s && (
                              <p className="text-2xs text-muted-foreground mt-1">
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
                          {lib.last_scan_status === "partial" && (
                            <p className="mt-1 inline-flex items-center gap-1 text-2xs text-destructive">
                              <AlertTriangle className="h-3 w-3" />
                              {uiText("Some files could not be indexed")}
                            </p>
                          )}
                        </div>
                        <div className="flex flex-shrink-0 items-center gap-1.5 self-end sm:self-auto">
                          <button
                            type="button"
                            disabled={!canEdit || busy || !rootBound}
                            onClick={() => handleScan(lib)}
                            title={
                              rootBound ? undefined : uiText("Verify the source before scanning.")
                            }
                            className={BTN_SECONDARY}
                          >
                            <RefreshCw className={`h-3.5 w-3.5 ${busy ? "animate-spin" : ""}`} />
                            {busy ? uiText("Scanning") : uiText("Scan now")}
                          </button>
                          <button
                            type="button"
                            role="switch"
                            aria-checked={lib.enabled}
                            aria-label={uiText("Auto-scan enabled")}
                            disabled={!canEdit || busy || !rootBound}
                            onClick={() => handleToggleEnabled(lib)}
                            className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-50 ${
                              lib.enabled ? "bg-primary" : "bg-outline-variant"
                            }`}
                          >
                            <span
                              className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                                lib.enabled ? "translate-x-6" : "translate-x-1"
                              }`}
                            />
                          </button>
                          <button
                            type="button"
                            disabled={!canEdit || busy}
                            onClick={() => setDeleteTarget(lib)}
                            className="inline-flex h-9 w-9 items-center justify-center rounded border border-border text-muted-foreground hover:bg-muted hover:text-destructive transition-colors disabled:opacity-50"
                            aria-label={uiText("Remove library source")}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}

            <p className="rounded-md bg-muted p-3 text-xs leading-relaxed text-muted-foreground">
              {uiText(
                "Remote connection profiles are managed in Settings → Remote storage. Create one there, allow Library sources, then select it below.",
              )}
            </p>

            {/* Add a library source */}
            <div className="rounded border border-dashed border-border p-3 sm:p-4 space-y-3">
              <p className="text-2xs font-mono uppercase tracking-wider text-primary">
                {uiText("Add a library source")}
              </p>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                  {uiText("Source name")}
                  <input
                    className={INPUT}
                    aria-label={uiText("Source name")}
                    placeholder={uiText("e.g. Workshop NAS")}
                    value={name}
                    disabled={!canEdit}
                    onChange={(e) => setName(e.target.value)}
                  />
                </label>
                <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                  {uiText("Source type")}
                  <select
                    className={INPUT}
                    aria-label={uiText("Library source type")}
                    value={sourceKind}
                    disabled={!canEdit}
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
                      disabled={!canEdit}
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
                        disabled={!canEdit}
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
                        disabled={!canEdit}
                        onChange={(event) => setSourcePrefix(event.target.value)}
                      />
                    </label>
                  </>
                )}
                <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                  {uiText("Scan schedule")}
                  <ScheduleControl
                    value={scanSchedule}
                    disabled={!canEdit}
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
                      disabled={!canEdit}
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
                    disabled={!canEdit}
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
                  disabled={!canEdit || busyId === "create"}
                  onClick={handleCreate}
                  className={BTN_PRIMARY}
                >
                  <Plus className="h-3.5 w-3.5" />
                  {busyId === "create" ? uiText("Adding") : uiText("Add source")}
                </button>
              </div>
            </div>
          </div>
        )}

        <ConfirmModal
          open={deleteTarget !== null}
          onClose={() => setDeleteTarget(null)}
          title={uiText("Remove library source?")}
          description={
            deleteTarget
              ? uiText(
                  '"{value1}" will be removed and its indexed models moved to trash. Source files remain untouched in their mounted folder or remote storage.',
                  { value1: String(deleteTarget.name) },
                )
              : ""
          }
          confirmLabel={uiText("Remove")}
          busy={deleteTarget !== null && busyId === deleteTarget.id}
          onConfirm={() => deleteTarget && handleDelete(deleteTarget)}
        />
        <ConfirmModal
          open={enrollTarget !== null}
          onClose={() => {
            if (busyId === null) setEnrollTarget(null);
          }}
          title={uiText("Enroll mounted source root?")}
          description={
            enrollTarget
              ? uiText(
                  "Verify that this exact mounted path belongs to this PrintStash installation before enrolling it: {value1}. This re-enables safe scans, watching, and writeback.",
                  { value1: String(enrollTarget.root_path) },
                )
              : ""
          }
          confirmLabel={uiText("Enroll root")}
          busy={enrollTarget !== null && busyId === enrollTarget.id}
          onConfirm={() => enrollTarget && handleEnroll(enrollTarget)}
        />
      </div>
    </Localized>
  );
}
