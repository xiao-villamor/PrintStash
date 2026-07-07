import { useCallback, useEffect, useState } from "react";
import {
  FolderSync,
  Plus,
  RefreshCw,
  Trash2,
  HardDrive,
  AlertTriangle,
} from "lucide-react";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import {
  createExternalLibrary,
  deleteExternalLibrary,
  getJobStatus,
  getVaultConfig,
  listExternalLibraries,
  scanExternalLibrary,
  updateExternalLibrary,
  updateVaultConfig,
} from "@/lib/api";
import { toast } from "@/lib/toast";
import type {
  ExternalLibrary,
  ExternalLibraryCollectionMode,
  ExternalLibraryWatchMode,
} from "@/types";

const BTN_PRIMARY =
  "inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded bg-[var(--primary)] text-[var(--primary-foreground)] text-xs font-medium uppercase tracking-wider hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed";
const BTN_SECONDARY =
  "inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded border border-border text-muted-foreground hover:bg-muted transition-colors text-xs font-medium uppercase tracking-wider disabled:opacity-50 disabled:cursor-not-allowed";
const INPUT =
  "w-full px-3 py-2 bg-background border border-border rounded text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent disabled:opacity-50";

// Cron presets surfaced as a dropdown; "" = manual only. Anything not in this
// list shows the "Custom" option with a raw cron input.
const SCHEDULE_PRESETS: { label: string; cron: string }[] = [
  { label: "Manual only", cron: "" },
  { label: "Hourly", cron: "0 * * * *" },
  { label: "Every 6 hours", cron: "0 */6 * * *" },
  { label: "Daily (midnight)", cron: "0 0 * * *" },
  { label: "Weekly (Sunday)", cron: "0 0 * * 0" },
];
const PRESET_CRONS = SCHEDULE_PRESETS.map((p) => p.cron);
const CUSTOM_SENTINEL = "__custom__";

const WATCH_OPTIONS: { value: ExternalLibraryWatchMode; label: string }[] = [
  { value: "auto", label: "Auto (watch local folders)" },
  { value: "events", label: "On (force watching)" },
  { value: "off", label: "Off (schedule only)" },
];

function describeSchedule(cron: string): string {
  if (!cron) return "Manual only";
  const preset = SCHEDULE_PRESETS.find((p) => p.cron === cron);
  return preset ? preset.label : `Custom (${cron})`;
}

function watchStatus(lib: ExternalLibrary): string {
  if (!lib.enabled) return "Paused";
  if (lib.watch_active) {
    return lib.fs_kind === "network"
      ? "Watching (forced — polling network folder)"
      : "Watching (real-time)";
  }
  if (lib.watch_mode === "off") return "Watching off — scheduled scans only";
  if (lib.fs_kind === "network")
    return "Network folder — scheduled scans only";
  if (lib.fs_kind === "unknown")
    return "Unknown filesystem — scheduled scans only";
  return "Scheduled scans only";
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
        <option value={CUSTOM_SENTINEL}>Custom cron…</option>
      </select>
      {!isPreset && (
        <input
          className={`${inputClass} font-mono`}
          placeholder="*/30 * * * * (min hour dom mon dow)"
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </div>
  );
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "Never";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

async function pollScanJob(jobId: string): Promise<void> {
  // Mirrors the upload modal's polling: wait until the scan job terminates.
  const deadline = Date.now() + 15 * 60 * 1000;
  while (Date.now() < deadline) {
    const job = await getJobStatus(jobId);
    if (job.state === "completed") return;
    if (job.state === "failed") {
      throw new Error(job.error || "scan_failed");
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error("scan_timeout");
}

export function ExternalLibrariesPanel({ canEdit }: { canEdit: boolean }) {
  const [enabled, setEnabled] = useState(false);
  const [enableBusy, setEnableBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const [libraries, setLibraries] = useState<ExternalLibrary[]>([]);
  const [busyId, setBusyId] = useState<number | "create" | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ExternalLibrary | null>(null);

  // Add-library draft.
  const [name, setName] = useState("");
  const [rootPath, setRootPath] = useState("");
  const [scanSchedule, setScanSchedule] = useState("0 * * * *");
  const [watchMode, setWatchMode] = useState<ExternalLibraryWatchMode>("auto");
  const [mode, setMode] = useState<ExternalLibraryCollectionMode>("mirror");

  const refresh = useCallback(async () => {
    try {
      setLibraries(await listExternalLibraries());
    } catch (e) {
      toast.error(e);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    getVaultConfig()
      .then((cfg) => {
        if (cancelled) return;
        setEnabled(cfg.external_libraries_enabled);
        setLoaded(true);
        if (cfg.external_libraries_enabled) refresh();
      })
      .catch(() => setLoaded(true));
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  async function toggleFeature(next: boolean) {
    setEnableBusy(true);
    setEnabled(next);
    try {
      await updateVaultConfig({ external_libraries_enabled: next });
      toast.success(
        next ? "Shared volumes enabled." : "Shared volumes disabled.",
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
    if (!name.trim() || !rootPath.trim()) {
      toast.error("Name and folder path are required.");
      return;
    }
    setBusyId("create");
    try {
      await createExternalLibrary({
        name: name.trim(),
        root_path: rootPath.trim(),
        scan_schedule: scanSchedule,
        watch_mode: watchMode,
        collection_mode: mode,
      });
      setName("");
      setRootPath("");
      setScanSchedule("0 * * * *");
      setWatchMode("auto");
      setMode("mirror");
      toast.success("Library added.");
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
      const resp = await scanExternalLibrary(lib.id);
      await pollScanJob(resp.job_id);
      toast.success(`Scan complete for "${lib.name}".`);
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
      await updateExternalLibrary(lib.id, { enabled: !lib.enabled });
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
      await updateExternalLibrary(lib.id, patch);
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
      await deleteExternalLibrary(lib.id);
      toast.success(`Removed "${lib.name}". Files on the volume were not touched.`);
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
    <div className="bg-card border border-border rounded">
      <div className="px-4 sm:px-5 py-3.5 border-b border-border flex items-start justify-between gap-3">
        <div className="flex items-start gap-3 min-w-0">
          <div className="w-8 h-8 rounded bg-muted flex items-center justify-center text-muted-foreground flex-shrink-0">
            <FolderSync className="h-4 w-4" />
          </div>
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-foreground">
              Shared volumes
            </h3>
            <p className="text-xs text-muted-foreground mt-0.5">
              Mirror a folder — on the server or a NAS — in place: files are indexed
              where they live, never copied. Local folders can be watched in real
              time; all folders support scheduled and manual scans. Off by default.
            </p>
          </div>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={enabled}
          disabled={!canEdit || enableBusy}
          onClick={() => toggleFeature(!enabled)}
          className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-50 ${
            enabled ? "bg-[var(--primary)]" : "bg-[var(--outline-variant)]"
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
            <p className="text-[13px] text-muted-foreground">
              No libraries yet. Add a folder below to start mirroring.
            </p>
          ) : (
            <ul className="space-y-3">
              {libraries.map((lib) => {
                const busy = busyId === lib.id;
                const s = lib.last_scan_summary;
                return (
                  <li
                    key={lib.id}
                    className="rounded border border-border bg-background p-3 sm:p-4"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <HardDrive className="h-3.5 w-3.5 text-muted-foreground" />
                          <span className="text-sm font-medium text-foreground truncate">
                            {lib.name}
                          </span>
                          {!lib.enabled && (
                            <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground/70 border border-border rounded px-1.5 py-0.5">
                              paused
                            </span>
                          )}
                        </div>
                        <p className="text-xs text-muted-foreground font-mono mt-1 truncate">
                          {lib.root_path}
                        </p>
                        <p className="text-[11px] text-muted-foreground mt-1">
                          {lib.collection_mode === "mirror"
                            ? "Mirrors subfolders → collections"
                            : "Single collection"}{" "}
                          · {describeSchedule(lib.scan_schedule)} · last scan{" "}
                          {formatDate(lib.last_scanned_at)}
                        </p>
                        <p className="text-[11px] text-muted-foreground mt-0.5">
                          {watchStatus(lib)}
                        </p>
                        {canEdit && (
                          <div className="mt-2 grid gap-2 sm:grid-cols-2 max-w-md">
                            <ScheduleControl
                              value={lib.scan_schedule}
                              disabled={busy}
                              inputClass={`${INPUT} !py-1.5 text-xs`}
                              onChange={(cron) =>
                                handleUpdate(lib, { scan_schedule: cron })
                              }
                            />
                            <select
                              className={`${INPUT} !py-1.5 text-xs self-start`}
                              value={lib.watch_mode}
                              disabled={busy}
                              onChange={(e) =>
                                handleUpdate(lib, {
                                  watch_mode: e.target
                                    .value as ExternalLibraryWatchMode,
                                })
                              }
                            >
                              {WATCH_OPTIONS.map((o) => (
                                <option key={o.value} value={o.value}>
                                  {o.label}
                                </option>
                              ))}
                            </select>
                          </div>
                        )}
                        {lib.last_scan_status === "error" && (
                          <p className="mt-1 inline-flex items-center gap-1 text-[11px] text-[var(--destructive,#dc2626)]">
                            <AlertTriangle className="h-3 w-3" />
                            {s?.error || "Last scan failed"}
                          </p>
                        )}
                        {(lib.last_scan_status === "ok" ||
                          lib.last_scan_status === "partial") &&
                          s && (
                            <p className="text-[11px] text-muted-foreground mt-1">
                              +{s.added} added · {s.updated} updated · {s.removed}{" "}
                              removed
                              {s.errors.length > 0
                                ? ` · ${s.errors.length} errors`
                                : ""}
                            </p>
                          )}
                        {lib.last_scan_status === "partial" && (
                          <p className="mt-1 inline-flex items-center gap-1 text-[11px] text-[var(--destructive,#dc2626)]">
                            <AlertTriangle className="h-3 w-3" />
                            Some files could not be indexed
                          </p>
                        )}
                      </div>
                      <div className="flex flex-shrink-0 items-center gap-1.5">
                        <button
                          type="button"
                          disabled={!canEdit || busy}
                          onClick={() => handleScan(lib)}
                          className={BTN_SECONDARY}
                        >
                          <RefreshCw
                            className={`h-3.5 w-3.5 ${busy ? "animate-spin" : ""}`}
                          />
                          {busy ? "Scanning" : "Scan now"}
                        </button>
                        <button
                          type="button"
                          role="switch"
                          aria-checked={lib.enabled}
                          aria-label="Auto-scan enabled"
                          disabled={!canEdit || busy}
                          onClick={() => handleToggleEnabled(lib)}
                          className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-50 ${
                            lib.enabled
                              ? "bg-[var(--primary)]"
                              : "bg-[var(--outline-variant)]"
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
                          className="inline-flex h-9 w-9 items-center justify-center rounded border border-border text-muted-foreground hover:bg-muted hover:text-[var(--destructive,#dc2626)] transition-colors disabled:opacity-50"
                          aria-label="Remove library"
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

          {/* Add a library */}
          <div className="rounded border border-dashed border-border p-3 sm:p-4 space-y-3">
            <p className="text-[11px] font-mono uppercase tracking-wider text-[var(--primary)]">
              Add a folder
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
              <input
                className={INPUT}
                placeholder="Name (e.g. NAS models)"
                value={name}
                disabled={!canEdit}
                onChange={(e) => setName(e.target.value)}
              />
              <input
                className={INPUT}
                placeholder="Absolute folder path (e.g. /mnt/nas/3d)"
                value={rootPath}
                disabled={!canEdit}
                onChange={(e) => setRootPath(e.target.value)}
              />
              <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                Scan schedule
                <ScheduleControl
                  value={scanSchedule}
                  disabled={!canEdit}
                  inputClass={INPUT}
                  onChange={setScanSchedule}
                />
              </label>
              <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                Real-time watching
                <select
                  className={INPUT}
                  value={watchMode}
                  disabled={!canEdit}
                  onChange={(e) =>
                    setWatchMode(e.target.value as ExternalLibraryWatchMode)
                  }
                >
                  {WATCH_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </label>
              <select
                className={INPUT}
                value={mode}
                disabled={!canEdit}
                onChange={(e) =>
                  setMode(e.target.value as ExternalLibraryCollectionMode)
                }
              >
                <option value="mirror">Mirror subfolders as collections</option>
                <option value="single">Single collection (flat)</option>
              </select>
            </div>
            <p className="text-[11px] text-muted-foreground">
              Watching gives near-real-time updates on local folders. Network
              folders (NAS over NFS/SMB) don't deliver file events, so they fall
              back to the schedule above.
            </p>
            <div className="flex justify-end">
              <button
                type="button"
                disabled={!canEdit || busyId === "create"}
                onClick={handleCreate}
                className={BTN_PRIMARY}
              >
                <Plus className="h-3.5 w-3.5" />
                {busyId === "create" ? "Adding" : "Add library"}
              </button>
            </div>
          </div>
        </div>
      )}

      <ConfirmModal
        open={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        title="Remove external library?"
        description={
          deleteTarget
            ? `"${deleteTarget.name}" will be removed and its indexed models moved to trash. The files on the shared volume are never touched.`
            : ""
        }
        confirmLabel="Remove"
        busy={deleteTarget !== null && busyId === deleteTarget.id}
        onConfirm={() => deleteTarget && handleDelete(deleteTarget)}
      />
    </div>
  );
}
