"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  PrintJobRead,
  PrinterFileRead,
  PrinterRead,
  PrinterSnapshot,
  PrinterStatus,
} from "@/types";
import {
  cancelPrinter,
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
import {
  ArrowLeft,
  FileText,
  Loader2,
  Pause,
  Play,
  Square,
  RefreshCcw,
  Thermometer,
  Wifi,
  WifiOff,
} from "lucide-react";

const STATUS_COLORS: Record<PrinterStatus, string> = {
  ready: "bg-emerald-500",
  printing: "bg-[var(--primary)]",
  paused: "bg-amber-500",
  offline: "bg-[var(--outline)]",
  unknown: "bg-[var(--outline)]",
  error: "bg-[var(--error)]",
};

function formatDuration(s?: number | null): string {
  if (!s || s <= 0) return "—";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    units.length - 1,
  );
  return `${(bytes / Math.pow(1024, index)).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function providerLabel(provider: PrinterRead["provider"]): string {
  return provider === "bambu_lan" ? "Bambu LAN" : "Moonraker";
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

export function PrinterDetailPage({ printerId }: { printerId: number }) {
  const auth = useRequireAuth();
  const [printer, setPrinter] = useState<PrinterRead | null>(null);
  const [snapshot, setSnapshot] = useState<PrinterSnapshot>({});
  const [jobs, setJobs] = useState<PrintJobRead[]>([]);
  const [printerFiles, setPrinterFiles] = useState<PrinterFileRead[]>([]);
  const [wsConnected, setWsConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"pause" | "resume" | "cancel" | null>(null);
  const [activeTab, setActiveTab] = useState<"status" | "files" | "jobs">("status");
  const [startingFileId, setStartingFileId] = useState<number | null>(null);
  const [syncingFiles, setSyncingFiles] = useState(false);
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
    loadPrinter();
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

  const ps = snapshot.print_stats || {};
  const vs = snapshot.virtual_sdcard || {};
  const ext = snapshot.extruder || {};
  const bed = snapshot.heater_bed || {};
  const toolhead = snapshot.toolhead || {};
  const webhook = snapshot.webhooks || {};
  const progress = typeof vs.progress === "number" ? vs.progress * 100 : null;

  if (!printer) {
    return (
      <div className="max-w-6xl mx-auto w-full space-y-4">
        <div className="h-8 w-48 rounded bg-[var(--surface-container)] animate-pulse" />
        <div className="h-64 w-full rounded bg-[var(--surface-container)] animate-pulse" />
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto w-full space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <Link
          href="/printers"
          className="inline-flex items-center gap-2 px-3 py-1.5 rounded text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)] transition-colors font-mono text-[13px]"
        >
          <ArrowLeft className="h-4 w-4" /> Printers
        </Link>
        <div className="flex items-center gap-1.5 px-2 py-1 bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded">
          {wsConnected ? (
            <>
              <Wifi className="h-3 w-3 text-emerald-500" />
              <span className="font-mono text-[10px] uppercase tracking-wider text-emerald-500 font-bold">
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
          <h1 className="text-2xl font-semibold text-[var(--on-surface)]">
            {printer.name}
          </h1>
          <span className="rounded border border-[var(--outline-variant)] px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)]">
            {providerLabel(printer.provider)}
          </span>
          {printer.capabilities.support_level === "beta" && (
            <span className="rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-amber-600">
              Beta
            </span>
          )}
          <span className="flex items-center gap-1.5 px-2 py-1 bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded">
            <span
              className={`w-2 h-2 rounded-full ${STATUS_COLORS[printer.status] || "bg-[var(--outline)]"}`}
            />
            <span className="font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)]">
              {printer.status}
            </span>
          </span>
        </div>
        <p className="font-mono text-xs text-[var(--on-surface-variant)] break-all">
          {printer.provider === "moonraker"
            ? printer.moonraker_url
            : printer.bambu_host || "Bambu LAN"}
        </p>
      </div>

      {printer.capabilities.support_notes.length > 0 && (
        <div className="rounded border border-[var(--outline-variant)] bg-[var(--surface-container-lowest)] p-3 text-xs leading-5 text-[var(--on-surface-variant)]">
          {printer.capabilities.support_notes.join(" ")}
        </div>
      )}

      {error && (
        <div className="rounded border border-[var(--error)]/40 bg-[var(--error-container)]/30 p-3 text-sm text-[var(--error)] font-mono">
          {error}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatusMetric label="Klipper" value={webhook.state || printer.status} />
        <StatusMetric label="File" value={ps.filename || "Idle"} truncate />
        <StatusMetric label="Progress" value={progress != null ? `${progress.toFixed(1)}%` : "—"} />
        <StatusMetric label="Homed" value={toolhead.homed_axes || "—"} />
      </div>

      <div className="flex flex-wrap gap-2 border-b border-[var(--outline-variant)]">
        <TabButton active={activeTab === "status"} onClick={() => setActiveTab("status")}>
          Status
        </TabButton>
        <TabButton active={activeTab === "files"} onClick={() => setActiveTab("files")}>
          Files
        </TabButton>
        <TabButton active={activeTab === "jobs"} onClick={() => setActiveTab("jobs")}>
          Jobs
        </TabButton>
      </div>

      {activeTab === "status" && (
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Current print */}
        <section className="lg:col-span-2 bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded overflow-hidden">
          <div className="px-6 py-4 border-b border-[var(--outline-variant)]">
            <h2 className="text-sm font-semibold text-[var(--on-surface)]">
              Current print
            </h2>
          </div>
          <div className="p-6 space-y-5">
            <Row label="FILE" value={ps.filename || "—"} truncate />
            <Row label="STATE" value={ps.state || "—"} capitalize />

            <div>
              <div className="flex items-center justify-between mb-2">
                <span className="font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)]">
                  Progress
                </span>
                <span className="font-mono text-xs text-[var(--on-surface)] font-semibold">
                  {progress != null ? `${progress.toFixed(1)}%` : "—"}
                </span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded bg-[var(--surface-container-high)]">
                <div
                  className="h-full bg-[var(--primary)] transition-all duration-500"
                  style={{ width: `${Math.min(100, progress ?? 0)}%` }}
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <Row label="ELAPSED" value={formatDuration(ps.print_duration)} stack />
              <Row label="TOTAL" value={formatDuration(ps.total_duration)} stack />
            </div>

            <div className="border-t border-[var(--surface-container-high)] pt-4 space-y-3">
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
              <p className="font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)]">
                {auth.isAuthenticated
                  ? "Controls use the API key from Settings."
                  : "Sign in or add an API key in Settings to control printers."}
              </p>
            </div>
          </div>
        </section>

        {/* Temperatures */}
        <section className="bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded overflow-hidden">
          <div className="px-6 py-4 border-b border-[var(--outline-variant)] flex items-center gap-2">
            <Thermometer className="h-4 w-4 text-[var(--on-surface-variant)]" />
            <h2 className="text-sm font-semibold text-[var(--on-surface)]">
              Temperatures
            </h2>
          </div>
          <div className="p-6 space-y-5">
            <TempRow label="Hotend" cur={ext.temperature} tgt={ext.target} />
            <TempRow label="Bed" cur={bed.temperature} tgt={bed.target} />
          </div>
        </section>
      </div>
      )}

      {activeTab === "files" && (
      <section className="bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded overflow-hidden">
        <div className="px-6 py-4 border-b border-[var(--outline-variant)] flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <FileText className="h-4 w-4 text-[var(--on-surface-variant)]" />
            <h2 className="text-sm font-semibold text-[var(--on-surface)]">
              Printer files
            </h2>
          </div>
          <button
            onClick={syncFiles}
            disabled={syncingFiles || !auth.isAuthenticated || !printer.capabilities.can_list_files}
            title={auth.blockReason ?? "Sync printer files"}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded border border-[var(--outline-variant)] text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)] transition-colors font-mono text-[10px] uppercase tracking-wider disabled:opacity-50 disabled:cursor-not-allowed"
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
          <div className="border-b border-[var(--outline-variant)] bg-[var(--error-container)]/20 px-6 py-3 text-[11px] text-[var(--error)] font-mono break-words">
            {printer.last_error}
          </div>
        )}
        {printerFiles.length === 0 ? (
          <div className="p-10 text-center font-mono text-xs text-[var(--on-surface-variant)]">
            {printer.capabilities.can_list_files
              ? "No printer files synced yet."
              : "Printer file inventory is not supported by this provider."}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)] border-b border-[var(--surface-variant)]">
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
                    className="border-b border-[var(--surface-variant)] last:border-0 hover:bg-[var(--surface-container-low)] transition-colors"
                  >
                    <td className="py-3 px-4 font-mono text-xs text-[var(--on-surface)] max-w-[320px] truncate" title={f.remote_filename}>
                      {f.remote_filename}
                    </td>
                    <td className="py-3 px-4">
                      {f.model_id ? (
                        <Link
                          href={`/models/${f.model_id}`}
                          className="text-[var(--on-surface)] hover:text-[var(--primary)] hover:underline font-mono text-xs"
                        >
                          {f.model_name ?? f.original_filename}
                        </Link>
                      ) : (
                        <span className="font-mono text-xs text-[var(--on-surface-variant)]">
                          External
                        </span>
                      )}
                    </td>
                    <td className="py-3 px-4 text-right font-mono text-xs text-[var(--on-surface)] whitespace-nowrap">
                      {f.size_bytes != null ? formatBytes(f.size_bytes) : "—"}
                    </td>
                    <td className="py-3 px-4">
                      <span className={`font-mono text-[10px] uppercase tracking-wider px-2 py-0.5 rounded ${
                        f.missing_since
                          ? "bg-amber-500/10 text-amber-600"
                          : "bg-emerald-500/10 text-emerald-600"
                      }`}>
                        {f.missing_since ? "missing" : f.matched_by}
                      </span>
                    </td>
                    <td className="py-3 px-4 font-mono text-xs text-[var(--on-surface-variant)] whitespace-nowrap">
                      {new Date(f.last_seen_at).toLocaleString()}
                    </td>
                    <td className="py-3 px-4 text-right">
                      <button
                        onClick={() => startRemoteFile(f)}
                        disabled={
                          !auth.isAuthenticated ||
                          !!f.missing_since ||
                          !printer.capabilities.can_start ||
                          startingFileId !== null
                        }
                        title={auth.blockReason ?? "Start this printer file"}
                        className="inline-flex items-center gap-1.5 rounded border border-[var(--outline-variant)] px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)] transition-colors hover:bg-[var(--surface-container-low)] disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {startingFileId === f.id ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : (
                          <Play className="h-3 w-3" />
                        )}
                        Start
                      </button>
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
      <section className="bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded overflow-hidden">
        <div className="px-6 py-4 border-b border-[var(--outline-variant)] flex items-center justify-between">
          <h2 className="text-sm font-semibold text-[var(--on-surface)]">
            Print history
          </h2>
          {jobs.length > 0 && (
            <span className="font-mono text-xs text-[var(--on-surface-variant)]">
              {jobs.length} {jobs.length === 1 ? "job" : "jobs"}
            </span>
          )}
        </div>
        {jobs.length === 0 ? (
          <div className="p-10 text-center font-mono text-xs text-[var(--on-surface-variant)]">
            No print jobs yet.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)] border-b border-[var(--surface-variant)]">
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
                    className="border-b border-[var(--surface-variant)] last:border-0 hover:bg-[var(--surface-container-low)] transition-colors"
                  >
                    <td className="py-3 px-4 font-mono text-xs text-[var(--on-surface-variant)] whitespace-nowrap">
                      {new Date(j.created_at).toLocaleString()}
                    </td>
                    <td className="py-3 px-4 max-w-[260px] truncate">
                      <Link
                        href={`/models/${j.model_id}`}
                        className="text-[var(--on-surface)] hover:text-[var(--primary)] hover:underline font-mono text-xs"
                        title={j.remote_filename}
                      >
                        {j.remote_filename}
                      </Link>
                    </td>
                    <td className="py-3 px-4">
                      <span className="font-mono text-[10px] uppercase tracking-wider px-2 py-0.5 rounded bg-[var(--surface-container)] text-[var(--on-surface)]">
                        {j.state}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-right font-mono text-xs text-[var(--on-surface)]">
                      {(j.progress * 100).toFixed(0)}%
                    </td>
                    <td className="py-3 px-4 font-mono text-xs text-[var(--on-surface-variant)] whitespace-nowrap">
                      {j.started_at
                        ? new Date(j.started_at).toLocaleTimeString()
                        : "—"}
                    </td>
                    <td className="py-3 px-4 font-mono text-xs text-[var(--on-surface-variant)] whitespace-nowrap">
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
          ? "border-[var(--primary)] text-[var(--primary)]"
          : "border-transparent text-[var(--on-surface-variant)] hover:text-[var(--on-surface)]"
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
    <div className="rounded border border-[var(--outline-variant)] bg-[var(--surface-container-lowest)] p-3">
      <div className="font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)]">
        {label}
      </div>
      <div
        className={`mt-1 font-mono text-sm font-semibold text-[var(--on-surface)] ${truncate ? "truncate" : ""}`}
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
        <div className="font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)]">
          {label}
        </div>
        <div className="font-mono text-sm text-[var(--on-surface)] font-semibold">
          {value}
        </div>
      </div>
    );
  }
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)] flex-shrink-0">
        {label}
      </span>
      <span
        className={`font-mono text-xs text-[var(--on-surface)] font-medium ${truncate ? "truncate" : ""} ${capitalize ? "capitalize" : ""}`}
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
          ? "bg-[var(--error)] text-[var(--primary-foreground)] hover:opacity-90"
          : "border border-[var(--outline-variant)] text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)]"
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
        <span className="font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)]">
          {label}
        </span>
        <span className="font-mono text-xs text-[var(--on-surface)] font-semibold">
          {cur != null ? cur.toFixed(1) : "—"}°C
          {tgt != null && tgt > 0 && (
            <span className="ml-1.5 text-[var(--on-surface-variant)] font-normal">
              / {tgt.toFixed(0)}°C
            </span>
          )}
        </span>
      </div>
      {pct != null && (
        <div className="h-1 w-full overflow-hidden rounded bg-[var(--surface-container-high)]">
          <div
            className="h-full bg-orange-500 transition-all duration-500"
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
    </div>
  );
}
