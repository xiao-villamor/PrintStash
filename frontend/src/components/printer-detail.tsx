"use client";

import { useEffect, useRef, useState } from "react";
import { Link } from "@/lib/navigation";
import {
  MoonrakerConfigRead,
  PrintJobRead,
  PrinterDiagnostics,
  PrinterFileRead,
  PrinterRead,
  PrinterSnapshot,
  PrinterStatus,
} from "@/types";
import {
  cancelPrinter,
  deletePrinterFile,
  getMoonrakerConfig,
  getPrinterDiagnostics,
  getPrinter,
  listPrinterFiles,
  listPrinterJobs,
  openPrinterWS,
  pausePrinter,
  resumePrinter,
  startPrinterFile,
  syncPrinterFiles,
} from "@/lib/api";
import { toast } from "@/lib/toast";
import { useRequireAuth } from "@/lib/use-require-auth";
import { formatBytes, formatDuration } from "@/lib/format";
import {
  ArrowLeft,
  AlertTriangle,
  CheckCircle2,
  FileText,
  Info,
  Loader2,
  Pause,
  Play,
  Square,
  RefreshCcw,
  Settings,
  Thermometer,
  Trash2,
  Wifi,
  WifiOff,
  XCircle,
} from "lucide-react";

const STATUS_COLORS: Record<PrinterStatus, string> = {
  ready: "bg-emerald-500",
  printing: "bg-blue-600 dark:bg-orange-600",
  paused: "bg-amber-500",
  offline: "bg-slate-400",
  unknown: "bg-slate-400",
  error: "bg-red-600",
};

const BTN_SECONDARY =
  "inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-40";
const BTN_DANGER =
  "inline-flex items-center gap-1.5 rounded-md border border-red-300/50 bg-background px-3 py-1.5 text-xs font-medium text-red-600 transition-colors hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-40 dark:hover:bg-red-950/40";
const SECTION_CLASS = "overflow-hidden rounded-lg border border-border bg-background";
const SECTION_HEADER_CLASS = "flex items-center justify-between gap-3 border-b border-border bg-muted/40 px-5 py-4";

function providerLabel(provider: PrinterRead["provider"]): string {
  return provider === "bambu_lan" ? "Bambu LAN" : "Moonraker";
}

function checkLabel(name: string): string {
  return name.replaceAll("_", " ");
}

function actionLabel(name: string): string {
  return name.replaceAll("_", " ");
}

function deepMerge<T extends Record<string, any>>(a: T, b: Partial<T>): T {
  const out: any = { ...a };
  for (const k of Object.keys(b)) {
    const av = (a as any)[k];
    const bv = (b as any)[k];
    if (
      av &&
      bv &&
      typeof av === "object" &&
      typeof bv === "object" &&
      !Array.isArray(av) &&
      !Array.isArray(bv)
    ) {
      out[k] = deepMerge(av, bv);
    } else {
      out[k] = bv;
    }
  }
  return out;
}

export function PrinterDetailPage({
  printerId,
  initialPrinter,
}: {
  printerId: number;
  initialPrinter?: PrinterRead;
}) {
  const auth = useRequireAuth();
  const [printer, setPrinter] = useState<PrinterRead | null>(
    initialPrinter ?? null,
  );
  const [diagnostics, setDiagnostics] = useState<PrinterDiagnostics | null>(null);
  const [moonrakerConfig, setMoonrakerConfig] = useState<MoonrakerConfigRead | null>(null);
  const [snapshot, setSnapshot] = useState<PrinterSnapshot>({});
  const [jobs, setJobs] = useState<PrintJobRead[]>([]);
  const [printerFiles, setPrinterFiles] = useState<PrinterFileRead[]>([]);
  const [wsConnected, setWsConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"pause" | "resume" | "cancel" | null>(null);
  const [activeTab, setActiveTab] = useState<"status" | "files" | "jobs" | "config" | "diagnostics">("status");
  const [startingFileId, setStartingFileId] = useState<number | null>(null);
  const [deletingFileId, setDeletingFileId] = useState<number | null>(null);
  const [syncingFiles, setSyncingFiles] = useState(false);
  const [checkingDiagnostics, setCheckingDiagnostics] = useState(false);
  const [loadingConfig, setLoadingConfig] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  async function loadJobs() {
    try {
      setJobs(await listPrinterJobs(printerId));
    } catch (e) {
      console.warn("Failed to load jobs:", e);
    }
  }

  async function loadPrinterFiles() {
    try {
      setPrinterFiles(await listPrinterFiles(printerId));
    } catch (e) {
      console.warn("Failed to load printer files:", e);
    }
  }

  async function loadPrinter() {
    try {
      setPrinter(await getPrinter(printerId));
    } catch (e: any) {
      setError(e.message);
    }
  }

  async function loadDiagnostics() {
    setCheckingDiagnostics(true);
    try {
      setDiagnostics(await getPrinterDiagnostics(printerId));
    } catch (e) {
      toast.error(e);
    } finally {
      setCheckingDiagnostics(false);
    }
  }

  async function loadMoonrakerConfig() {
    if (printer?.provider !== "moonraker" && initialPrinter?.provider !== "moonraker") return;
    setLoadingConfig(true);
    try {
      setMoonrakerConfig(await getMoonrakerConfig(printerId));
    } catch (e) {
      toast.error(e);
    } finally {
      setLoadingConfig(false);
    }
  }

  function connect() {
    try {
      const ws = openPrinterWS(printerId);
      wsRef.current = ws;
      ws.onopen = () => setWsConnected(true);
      ws.onclose = () => {
        setWsConnected(false);
        if (reconnectRef.current) clearTimeout(reconnectRef.current);
        reconnectRef.current = setTimeout(connect, 3000);
      };
      ws.onerror = () => {};
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === "snapshot") {
            setSnapshot(msg.data || {});
          } else if (msg.type === "update") {
            setSnapshot((prev) => deepMerge(prev, msg.data || {}));
          }
          const state = msg?.data?.print_stats?.state;
          if (state) loadJobs();
        } catch {
          /* ignore */
        }
      };
    } catch (e: any) {
      setError(`WS error: ${e.message}`);
    }
  }

  useEffect(() => {
    // Server-rendered pages pass the printer down; WS keeps it live after that.
    if (!initialPrinter || initialPrinter.id !== printerId) loadPrinter();
    loadDiagnostics();
    if (initialPrinter?.provider === "moonraker") loadMoonrakerConfig();
    loadJobs();
    loadPrinterFiles();
    connect();
    return () => {
      if (reconnectRef.current) clearTimeout(reconnectRef.current);
      wsRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [printerId]);

  async function control(
    action: "pause" | "resume" | "cancel",
    fn: () => Promise<void>,
  ) {
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
    setBusy(action);
    try {
      await fn();
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(null);
    }
  }

  async function syncFiles() {
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
    setSyncingFiles(true);
    setError(null);
    try {
      setPrinterFiles(await syncPrinterFiles(printerId));
      await loadPrinter();
      toast.success("Printer files synced");
    } catch (e) {
      toast.error(e);
      await loadPrinter();
    } finally {
      setSyncingFiles(false);
    }
  }

  async function startRemoteFile(file: PrinterFileRead) {
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
    setStartingFileId(file.id);
    setError(null);
    try {
      const job = await startPrinterFile(printerId, {
        remote_filename: file.remote_filename,
        file_id: file.file_id,
      });
      await loadJobs();
      toast.success(`Print started (job #${job.id})`);
      setActiveTab("status");
    } catch (e) {
      toast.error(e);
    } finally {
      setStartingFileId(null);
    }
  }

  async function deleteRemoteFile(file: PrinterFileRead) {
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
    if (!window.confirm(`Delete ${file.remote_filename} from ${printer?.name ?? "printer"}?`)) return;
    setDeletingFileId(file.id);
    setError(null);
    try {
      setPrinterFiles(await deletePrinterFile(printerId, file.id));
      await loadPrinter();
      toast.success("Printer file deleted");
    } catch (e) {
      toast.error(e);
      await loadPrinter();
    } finally {
      setDeletingFileId(null);
    }
  }

  const ps = snapshot.print_stats || {};
  const vs = snapshot.virtual_sdcard || {};
  const ext = snapshot.extruder || {};
  const bed = snapshot.heater_bed || {};
  const toolhead = snapshot.toolhead || {};
  const webhook = snapshot.webhooks || {};
  const progress = typeof vs.progress === "number" ? vs.progress * 100 : null;

  if (!printer) {
    return (
      <div className="w-full space-y-4">
        <div className="h-8 w-48 animate-pulse rounded bg-muted" />
        <div className="h-64 w-full animate-pulse rounded bg-muted" />
      </div>
    );
  }

  return (
    <div className="w-full space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <Link
          href="/printers"
          className="inline-flex items-center gap-2 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted"
        >
          <ArrowLeft className="h-4 w-4" /> Printers
        </Link>
        <div className="flex items-center gap-1.5 rounded-md border border-border bg-background px-2.5 py-1.5">
          {wsConnected ? (
            <>
              <Wifi className="h-3 w-3 text-emerald-500" />
              <span className="font-mono text-[10px] font-semibold uppercase tracking-wider text-emerald-600">
                Live
              </span>
            </>
          ) : (
            <>
              <WifiOff className="h-3 w-3 text-amber-500" />
              <span className="font-mono text-[10px] uppercase tracking-wider text-amber-500">
                Reconnecting…
              </span>
            </>
          )}
        </div>
      </div>

      {/* Title */}
      <div className="space-y-2">
        <div className="flex items-center gap-3 flex-wrap">
          <h1 className="text-2xl font-bold tracking-tight text-foreground">
            {printer.name}
          </h1>
          <span className="rounded border border-border px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            {providerLabel(printer.provider)}
          </span>
          {printer.capabilities.support_level === "beta" && (
            <span className="rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-amber-600">
              Beta
            </span>
          )}
          <span className="flex items-center gap-1.5 rounded border border-border bg-background px-2 py-1">
            <span
              className={`w-2 h-2 rounded-full ${STATUS_COLORS[printer.status] || "bg-slate-400"}`}
            />
            <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              {printer.status}
            </span>
          </span>
        </div>
        <p className="font-mono text-xs text-muted-foreground break-all">
          {printer.provider === "moonraker"
            ? printer.moonraker_url
            : printer.bambu_host || "Bambu LAN"}
        </p>
      </div>

      {printer.capabilities.support_notes.length > 0 && (
        <div className="rounded border border-border bg-muted/40 p-3 text-xs leading-5 text-muted-foreground">
          {printer.capabilities.support_notes.join(" ")}
        </div>
      )}

      {error && (
        <div className="rounded border border-red-300/50 bg-red-50/30 p-3 font-mono text-sm text-red-600 dark:bg-red-950/20">
          {error}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatusMetric label="Klipper" value={webhook.state || printer.status} />
        <StatusMetric label="File" value={ps.filename || "Idle"} truncate />
        <StatusMetric label="Progress" value={progress != null ? `${progress.toFixed(1)}%` : "—"} />
        <StatusMetric label="Homed" value={toolhead.homed_axes || "—"} />
      </div>

      <div className="border-b border-border">
        <div className="flex gap-1 overflow-x-auto -mb-px">
        <TabButton active={activeTab === "status"} onClick={() => setActiveTab("status")}>
          Status
        </TabButton>
        <TabButton active={activeTab === "files"} onClick={() => setActiveTab("files")}>
          Files
        </TabButton>
        <TabButton active={activeTab === "jobs"} onClick={() => setActiveTab("jobs")}>
          Jobs
        </TabButton>
        {printer.provider === "moonraker" && (
          <TabButton active={activeTab === "config"} onClick={() => {
            setActiveTab("config");
            if (!moonrakerConfig) loadMoonrakerConfig();
          }}>
            Config
          </TabButton>
        )}
        <TabButton active={activeTab === "diagnostics"} onClick={() => setActiveTab("diagnostics")}>
          Diagnostics
        </TabButton>
        </div>
      </div>

      {activeTab === "status" && (
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Current print */}
        <section className={`${SECTION_CLASS} lg:col-span-2`}>
          <div className={SECTION_HEADER_CLASS}>
            <h2 className="text-sm font-semibold text-foreground">
              Current print
            </h2>
          </div>
          <div className="p-6 space-y-5">
            <Row label="FILE" value={ps.filename || "—"} truncate />
            <Row label="STATE" value={ps.state || "—"} capitalize />

            <div>
              <div className="flex items-center justify-between mb-2">
                <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                  Progress
                </span>
                <span className="font-mono text-xs text-foreground font-semibold">
                  {progress != null ? `${progress.toFixed(1)}%` : "—"}
                </span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded bg-muted">
                <div
                  className="h-full bg-blue-600 transition-all duration-500 dark:bg-orange-600"
                  style={{ width: `${Math.min(100, progress ?? 0)}%` }}
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <Row label="ELAPSED" value={formatDuration(ps.print_duration)} stack />
              <Row label="TOTAL" value={formatDuration(ps.total_duration)} stack />
            </div>

            <div className="border-t border-border pt-4 space-y-3">
              <div className="flex flex-wrap gap-2">
                <ControlButton
                  onClick={() => control("pause", () => pausePrinter(printerId))}
                  disabled={!auth.isAuthenticated || !printer.capabilities.can_pause || ps.state !== "printing" || busy !== null}
                  busy={busy === "pause"}
                  icon={Pause}
                  label={auth.isAuthenticated ? "Pause" : "Sign in"}
                />
                <ControlButton
                  onClick={() => control("resume", () => resumePrinter(printerId))}
                  disabled={!auth.isAuthenticated || !printer.capabilities.can_resume || ps.state !== "paused" || busy !== null}
                  busy={busy === "resume"}
                  icon={Play}
                  label={auth.isAuthenticated ? "Resume" : "Sign in"}
                />
                <ControlButton
                  onClick={() => control("cancel", () => cancelPrinter(printerId))}
                  disabled={
                    !auth.isAuthenticated ||
                    !printer.capabilities.can_cancel ||
                    (ps.state !== "printing" && ps.state !== "paused") ||
                    busy !== null
                  }
                  busy={busy === "cancel"}
                  icon={Square}
                  label={auth.isAuthenticated ? "Cancel" : "Sign in"}
                  destructive
                />
              </div>
              <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                {auth.isAuthenticated
                  ? "Controls use your signed-in user session."
                  : "Sign in to control printers."}
              </p>
            </div>
          </div>
        </section>

        {/* Temperatures */}
        <section className={SECTION_CLASS}>
          <div className={SECTION_HEADER_CLASS}>
            <div className="flex items-center gap-2">
            <Thermometer className="h-4 w-4 text-muted-foreground" />
            <h2 className="text-sm font-semibold text-foreground">
              Temperatures
            </h2>
            </div>
          </div>
          <div className="p-6 space-y-5">
            <TempRow label="Hotend" cur={ext.temperature} tgt={ext.target} />
            <TempRow label="Bed" cur={bed.temperature} tgt={bed.target} />
          </div>
        </section>
      </div>
      )}

      {activeTab === "files" && (
      <section className={SECTION_CLASS}>
        <div className={SECTION_HEADER_CLASS}>
          <div className="flex items-center gap-2">
            <FileText className="h-4 w-4 text-muted-foreground" />
            <h2 className="text-sm font-semibold text-foreground">
              Printer files
            </h2>
            <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-semibold text-muted-foreground">
              {printerFiles.length}
            </span>
          </div>
          <button
            onClick={syncFiles}
            disabled={syncingFiles || !auth.isAuthenticated || !printer.capabilities.can_list_files}
            title={auth.blockReason ?? "Sync printer files"}
            className={BTN_SECONDARY}
          >
            {syncingFiles ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCcw className="h-3.5 w-3.5" />
            )}
            {syncingFiles ? "Syncing" : "Sync"}
          </button>
        </div>
        {printer.last_error && printer.status === "offline" && (
          <div className="border-b border-border bg-red-50/30 px-6 py-3 font-mono text-[11px] text-red-600 break-words dark:bg-red-950/20">
            {printer.last_error}
          </div>
        )}
        {printerFiles.length === 0 ? (
          <div className="p-10 text-center font-mono text-xs text-muted-foreground">
            {printer.capabilities.can_list_files
              ? "No printer files synced yet."
              : "Printer file inventory is not supported by this provider."}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                  <th className="py-3 px-4 font-medium">Remote file</th>
                  <th className="py-3 px-4 font-medium">Vault match</th>
                  <th className="py-3 px-4 font-medium text-right">Size</th>
                  <th className="py-3 px-4 font-medium">Status</th>
                  <th className="py-3 px-4 font-medium">Last seen</th>
                  <th className="py-3 px-4 font-medium text-right">Action</th>
                </tr>
              </thead>
              <tbody>
                {printerFiles.map((f) => (
                  <tr
                    key={f.id}
                    className="border-b border-border transition-colors last:border-0 hover:bg-muted/30"
                  >
                    <td className="py-3 px-4 font-mono text-xs text-foreground max-w-[320px] truncate" title={f.remote_filename}>
                      {f.remote_filename}
                    </td>
                    <td className="py-3 px-4">
                      {f.model_id ? (
                        <Link
                          href={`/models/${f.model_id}`}
                          className="font-mono text-xs text-foreground hover:text-blue-600 hover:underline dark:hover:text-orange-500"
                        >
                          {f.model_name ?? f.original_filename}
                        </Link>
                      ) : (
                        <span className="font-mono text-xs text-muted-foreground">
                          External
                        </span>
                      )}
                    </td>
                    <td className="py-3 px-4 text-right font-mono text-xs text-foreground whitespace-nowrap">
                      {f.size_bytes != null ? formatBytes(f.size_bytes) : "—"}
                    </td>
                    <td className="py-3 px-4">
                      <span className="font-mono text-[10px] uppercase tracking-wider px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-600">
                        {f.matched_by}
                      </span>
                    </td>
                    <td className="py-3 px-4 font-mono text-xs text-muted-foreground whitespace-nowrap">
                      {new Date(f.last_seen_at).toLocaleString()}
                    </td>
                    <td className="py-3 px-4 text-right">
                      <div className="flex justify-end gap-2">
                      <button
                        onClick={() => startRemoteFile(f)}
                        disabled={
                          !auth.isAuthenticated ||
                          !printer.capabilities.can_start ||
                          startingFileId !== null ||
                          deletingFileId !== null
                        }
                        title={auth.blockReason ?? "Start this printer file"}
                        className={BTN_SECONDARY}
                      >
                        {startingFileId === f.id ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : (
                          <Play className="h-3 w-3" />
                        )}
                        Start
                      </button>
                      <button
                        onClick={() => deleteRemoteFile(f)}
                        disabled={
                          !auth.isAuthenticated ||
                          printer.provider !== "moonraker" ||
                          deletingFileId !== null ||
                          startingFileId !== null ||
                          ps.filename === f.remote_filename
                        }
                        title={
                          ps.filename === f.remote_filename
                            ? "Cannot delete active print file"
                            : auth.blockReason ?? "Delete this printer file"
                        }
                        className={BTN_DANGER}
                      >
                        {deletingFileId === f.id ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : (
                          <Trash2 className="h-3 w-3" />
                        )}
                        Delete
                      </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      )}

      {/* History */}
      {activeTab === "jobs" && (
      <section className={SECTION_CLASS}>
        <div className={SECTION_HEADER_CLASS}>
          <h2 className="text-sm font-semibold text-foreground">
            Print history
          </h2>
          {jobs.length > 0 && (
            <span className="rounded-full bg-muted px-2 py-0.5 font-mono text-[10px] font-semibold text-muted-foreground">
              {jobs.length} {jobs.length === 1 ? "job" : "jobs"}
            </span>
          )}
        </div>
        {jobs.length === 0 ? (
          <div className="p-10 text-center font-mono text-xs text-muted-foreground">
            No print jobs yet.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                  <th className="py-3 px-4 font-medium">When</th>
                  <th className="py-3 px-4 font-medium">File</th>
                  <th className="py-3 px-4 font-medium">State</th>
                  <th className="py-3 px-4 font-medium text-right">Progress</th>
                  <th className="py-3 px-4 font-medium">Started</th>
                  <th className="py-3 px-4 font-medium">Finished</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((j) => (
                  <tr
                    key={j.id}
                    className="border-b border-border transition-colors last:border-0 hover:bg-muted/30"
                  >
                    <td className="py-3 px-4 font-mono text-xs text-muted-foreground whitespace-nowrap">
                      {new Date(j.created_at).toLocaleString()}
                    </td>
                    <td className="py-3 px-4 max-w-[260px] truncate">
                      <Link
                        href={`/models/${j.model_id}`}
                        className="font-mono text-xs text-foreground hover:text-blue-600 hover:underline dark:hover:text-orange-500"
                        title={j.remote_filename}
                      >
                        {j.remote_filename}
                      </Link>
                    </td>
                    <td className="py-3 px-4">
                      <span className="rounded bg-muted px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider text-foreground">
                        {j.state}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-right font-mono text-xs text-foreground">
                      {(j.progress * 100).toFixed(0)}%
                    </td>
                    <td className="py-3 px-4 font-mono text-xs text-muted-foreground whitespace-nowrap">
                      {j.started_at
                        ? new Date(j.started_at).toLocaleTimeString()
                        : "—"}
                    </td>
                    <td className="py-3 px-4 font-mono text-xs text-muted-foreground whitespace-nowrap">
                      {j.finished_at
                        ? new Date(j.finished_at).toLocaleTimeString()
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      )}

      {activeTab === "config" && printer.provider === "moonraker" && (
      <section className={SECTION_CLASS}>
        <div className={SECTION_HEADER_CLASS}>
          <div className="flex items-center gap-2">
            <Settings className="h-4 w-4 text-muted-foreground" />
            <h2 className="text-sm font-semibold text-foreground">
              Moonraker and Klipper config
            </h2>
          </div>
          <button
            onClick={loadMoonrakerConfig}
            disabled={loadingConfig}
            className={BTN_SECONDARY}
          >
            {loadingConfig ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCcw className="h-3.5 w-3.5" />
            )}
            {loadingConfig ? "Loading" : "Refresh"}
          </button>
        </div>
        {!moonrakerConfig ? (
          <div className="p-10 text-center font-mono text-xs text-muted-foreground">
            {loadingConfig ? "Loading config…" : "Config not loaded."}
          </div>
        ) : (
          <div className="p-4 sm:p-6 space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <ConfigSummary title="Moonraker" data={moonrakerConfig.server_info} />
              <ConfigSummary title="Klipper" data={moonrakerConfig.printer_info} />
            </div>
            <ConfigBlock title="Moonraker config" data={moonrakerConfig.moonraker_config} />
            <ConfigBlock title="Klipper config" data={moonrakerConfig.klipper_config} />
          </div>
        )}
      </section>
      )}

      {activeTab === "diagnostics" && (
      <section className={SECTION_CLASS}>
        <div className={SECTION_HEADER_CLASS}>
          <div className="flex items-center gap-2">
            <Info className="h-4 w-4 text-muted-foreground" />
            <h2 className="text-sm font-semibold text-foreground">
              Provider diagnostics
            </h2>
          </div>
          <button
            onClick={loadDiagnostics}
            disabled={checkingDiagnostics}
            className={BTN_SECONDARY}
          >
            {checkingDiagnostics ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCcw className="h-3.5 w-3.5" />
            )}
            {checkingDiagnostics ? "Checking" : "Refresh"}
          </button>
        </div>

        {!diagnostics ? (
          <div className="p-10 text-center font-mono text-xs text-muted-foreground">
            Diagnostics have not loaded yet.
          </div>
        ) : (
          <div className="p-4 sm:p-6 space-y-5">
            <div className={`rounded border p-4 ${
              diagnostics.ok
                ? "border-emerald-500/30 bg-emerald-500/10"
                : "border-amber-500/40 bg-amber-500/10"
            }`}>
              <div className="flex items-center gap-2">
                {diagnostics.ok ? (
                  <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                ) : (
                  <AlertTriangle className="h-4 w-4 text-amber-600" />
                )}
                <span className={`font-mono text-xs uppercase tracking-wider font-semibold ${
                  diagnostics.ok ? "text-emerald-600" : "text-amber-600"
                }`}>
                  {diagnostics.ok ? "Provider reachable" : "Needs attention"}
                </span>
              </div>
              <p className="mt-2 text-sm text-muted-foreground">
                {providerLabel(diagnostics.provider)} is marked {diagnostics.support_level}.
              </p>
            </div>

            {diagnostics.notes.length > 0 && (
              <div className="rounded border border-border bg-muted/40 p-4 space-y-2">
                {diagnostics.notes.map((note) => (
                  <p key={note} className="text-sm text-muted-foreground leading-relaxed">
                    {note}
                  </p>
                ))}
              </div>
            )}

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="rounded border border-border overflow-hidden">
                <div className="border-b border-border px-4 py-3">
                  <h3 className="text-sm font-semibold text-foreground">
                    Checks
                  </h3>
                </div>
                <div className="divide-y divide-border">
                  {diagnostics.checks.map((check) => (
                    <div key={check.name} className="px-4 py-3 flex items-start gap-3">
                      {check.ok ? (
                        <CheckCircle2 className="mt-0.5 h-4 w-4 flex-shrink-0 text-emerald-600" />
                      ) : (
                        <XCircle className="mt-0.5 h-4 w-4 flex-shrink-0 text-red-600" />
                      )}
                      <div className="min-w-0 flex-1">
                        <div className="font-mono text-xs uppercase tracking-wider text-foreground">
                          {checkLabel(check.name)}
                        </div>
                        {!check.ok && (
                          <div className="mt-1 font-mono text-[11px] text-red-600 break-words">
                            {check.code ?? "provider_error"}
                            {check.detail ? `: ${check.detail}` : ""}
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="rounded border border-border overflow-hidden">
                <div className="border-b border-border px-4 py-3">
                  <h3 className="text-sm font-semibold text-foreground">
                    Capabilities
                  </h3>
                </div>
                <div className="grid grid-cols-2 gap-px bg-border">
                  {Object.entries(diagnostics.capabilities).map(([name, enabled]) => (
                    <div key={name} className="bg-background px-4 py-3">
                      <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                        {checkLabel(name.replace(/^can_/, ""))}
                      </div>
                      <div className={`mt-1 font-mono text-xs font-semibold ${
                        enabled ? "text-emerald-600" : "text-muted-foreground"
                      }`}>
                        {enabled ? "Supported" : "Unavailable"}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {diagnostics.unsupported_actions.length > 0 && (
              <div className="rounded border border-amber-500/40 bg-amber-500/10 p-4">
                <div className="font-mono text-[10px] uppercase tracking-wider text-amber-600 font-semibold">
                  Unsupported in this provider
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  {diagnostics.unsupported_actions.map((action) => (
                    <span
                      key={action}
                      className="rounded border border-amber-500/40 px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-amber-600"
                    >
                      {actionLabel(action)}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </section>
      )}
    </div>
  );
}

function ConfigSummary({
  title,
  data,
}: {
  title: string;
  data: Record<string, any>;
}) {
  const rows = Object.entries(data)
    .filter(([, value]) => value == null || ["string", "number", "boolean"].includes(typeof value))
    .slice(0, 8);

  return (
    <div className="rounded border border-border overflow-hidden">
      <div className="border-b border-border px-4 py-3">
        <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      </div>
      <div className="divide-y divide-border">
        {rows.length === 0 ? (
          <div className="px-4 py-3 font-mono text-xs text-muted-foreground">
            —
          </div>
        ) : (
          rows.map(([key, value]) => (
            <div key={key} className="grid grid-cols-[minmax(120px,0.45fr)_1fr] gap-3 px-4 py-3">
              <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground truncate">
                {checkLabel(key)}
              </div>
              <div className="font-mono text-xs text-foreground break-words">
                {String(value ?? "—")}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function ConfigBlock({
  title,
  data,
}: {
  title: string;
  data: Record<string, any>;
}) {
  return (
    <div className="rounded border border-border overflow-hidden">
      <div className="border-b border-border px-4 py-3">
        <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      </div>
      <pre className="max-h-[420px] overflow-auto bg-muted/40 p-4 font-mono text-[11px] leading-5 text-foreground">
        {JSON.stringify(data, null, 2)}
      </pre>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`border-b-2 px-3 py-2 font-mono text-xs uppercase tracking-wider transition-colors ${
        active
          ? "border-blue-600 text-blue-600 dark:border-orange-500 dark:text-orange-500"
          : "border-transparent text-muted-foreground hover:text-foreground"
      }`}
    >
      {children}
    </button>
  );
}

function StatusMetric({
  label,
  value,
  truncate,
}: {
  label: string;
  value: string;
  truncate?: boolean;
}) {
  return (
    <div className="rounded border border-border bg-background p-3">
      <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div
        className={`mt-1 font-mono text-sm font-semibold text-foreground ${truncate ? "truncate" : ""}`}
        title={value}
      >
        {value}
      </div>
    </div>
  );
}

function Row({
  label,
  value,
  truncate,
  capitalize,
  stack,
}: {
  label: string;
  value: string;
  truncate?: boolean;
  capitalize?: boolean;
  stack?: boolean;
}) {
  if (stack) {
    return (
      <div className="space-y-1">
        <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
          {label}
        </div>
        <div className="font-mono text-sm text-foreground font-semibold">
          {value}
        </div>
      </div>
    );
  }
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground flex-shrink-0">
        {label}
      </span>
      <span
        className={`font-mono text-xs text-foreground font-medium ${truncate ? "truncate" : ""} ${capitalize ? "capitalize" : ""}`}
      >
        {value}
      </span>
    </div>
  );
}

function ControlButton({
  onClick,
  disabled,
  busy,
  icon: Icon,
  label,
  destructive,
}: {
  onClick: () => void;
  disabled?: boolean;
  busy?: boolean;
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  destructive?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`flex items-center gap-1.5 px-3 py-2 rounded font-mono text-xs uppercase tracking-wider transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
        destructive
          ? "bg-red-600 text-white hover:opacity-90"
          : "border border-border text-foreground hover:bg-muted"
      }`}
    >
      {busy ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
      ) : (
        <Icon className="h-3.5 w-3.5" />
      )}
      {label}
    </button>
  );
}

function TempRow({
  label,
  cur,
  tgt,
}: {
  label: string;
  cur?: number;
  tgt?: number;
}) {
  const pct = tgt != null && tgt > 0 && cur != null
    ? Math.min(100, Math.max(0, (cur / tgt) * 100))
    : null;
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
          {label}
        </span>
        <span className="font-mono text-xs text-foreground font-semibold">
          {cur != null ? cur.toFixed(1) : "—"}°C
          {tgt != null && tgt > 0 && (
            <span className="ml-1.5 text-muted-foreground font-normal">
              / {tgt.toFixed(0)}°C
            </span>
          )}
        </span>
      </div>
      {pct != null && (
        <div className="h-1 w-full overflow-hidden rounded bg-muted">
          <div
            className="h-full bg-blue-500 dark:bg-orange-500 transition-all duration-500"
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
    </div>
  );
}
