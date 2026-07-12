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
  PrinterUpdate,
} from "@/types";
import {
  cancelPrinter,
  deletePrinterFile,
  emergencyStopPrinter,
  getMoonrakerConfig,
  getPrinterDiagnostics,
  getPrinter,
  homePrinter,
  listPrinterFiles,
  listPrinterJobs,
  openPrinterWS,
  pausePrinter,
  resumePrinter,
  setPrinterTemperature,
  startPrinterFile,
  syncPrinterFiles,
  updatePrinter,
} from "@/lib/api";
import { toast } from "@/lib/toast";
import { useRequireAuth } from "@/lib/use-require-auth";
import { formatBytes, formatDuration } from "@/lib/format";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { TabBar } from "@/components/ui/tabs";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { cn } from "@/lib/utils";
import { PRINTER_MODEL_OPTIONS, providerAddress, providerLabel } from "@/lib/printer-providers";
import {
  ArrowLeft,
  AlertTriangle,
  CheckCircle2,
  FileText,
  Info,
  Loader2,
  Home,
  Pause,
  Play,
  Power,
  Square,
  RefreshCcw,
  Settings,
  Thermometer,
  Trash2,
  Wifi,
  WifiOff,
  XCircle,
} from "lucide-react";

const PREHEAT_PRESETS: { name: string; hotend: number; bed: number }[] = [
  { name: "PLA", hotend: 200, bed: 60 },
  { name: "PETG", hotend: 240, bed: 80 },
  { name: "ABS", hotend: 245, bed: 100 },
];

const STATUS_COLORS: Record<PrinterStatus, string> = {
  ready: "bg-success",
  printing: "bg-primary",
  paused: "bg-warning",
  offline: "bg-muted-foreground",
  unknown: "bg-muted-foreground",
  error: "bg-destructive",
};

const BTN_SECONDARY = cn(buttonVariants({ variant: "outline", size: "xs" }), "hover:bg-muted");
const BTN_DANGER = cn(
  buttonVariants({ variant: "outline", size: "xs" }),
  "border-red-300/50 text-red-600 hover:bg-red-50 dark:hover:bg-red-950/40",
);
const SECTION_CLASS = "overflow-hidden rounded-lg border border-border bg-background";
const SECTION_HEADER_CLASS = "flex items-center justify-between gap-3 border-b border-border bg-muted/40 px-5 py-4";

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
  const [machineBusy, setMachineBusy] = useState<string | null>(null);
  const [hotendTarget, setHotendTarget] = useState("");
  const [bedTarget, setBedTarget] = useState("");
  const [activeTab, setActiveTab] = useState<"status" | "files" | "jobs" | "config" | "diagnostics" | "settings">("status");
  const [startingFileId, setStartingFileId] = useState<number | null>(null);
  const [deletingFileId, setDeletingFileId] = useState<number | null>(null);
  const [syncingFiles, setSyncingFiles] = useState(false);
  const [checkingDiagnostics, setCheckingDiagnostics] = useState(false);
  const [loadingConfig, setLoadingConfig] = useState(false);
  const [confirmEmergencyStop, setConfirmEmergencyStop] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<PrinterFileRead | null>(null);
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

  async function machineAction(
    key: string,
    fn: () => Promise<unknown>,
    successMsg: string,
  ) {
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
    setMachineBusy(key);
    try {
      await fn();
      toast.success(successMsg);
    } catch (e) {
      toast.error(e);
    } finally {
      setMachineBusy(null);
    }
  }

  function applyTemp(heater: "extruder" | "bed", raw: string, clear: () => void) {
    const t = Number(raw);
    if (!Number.isFinite(t) || t < 0 || t > 500) {
      toast.error("Enter a temperature between 0 and 500.");
      return;
    }
    void machineAction(
      `set-${heater}`,
      () => setPrinterTemperature(printerId, heater, t).then(clear),
      `${heater === "extruder" ? "Hotend" : "Bed"} set to ${t}°C`,
    );
  }

  function preheat(p: (typeof PREHEAT_PRESETS)[number]) {
    void machineAction(
      `preheat-${p.name}`,
      async () => {
        await setPrinterTemperature(printerId, "extruder", p.hotend);
        await setPrinterTemperature(printerId, "bed", p.bed);
      },
      `Preheating for ${p.name}`,
    );
  }

  function cooldown() {
    void machineAction(
      "cooldown",
      async () => {
        await setPrinterTemperature(printerId, "extruder", 0);
        await setPrinterTemperature(printerId, "bed", 0);
      },
      "Cooling down",
    );
  }

  function emergencyStop() {
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
    setConfirmEmergencyStop(true);
  }

  async function confirmEmergencyStopAction() {
    await machineAction(
      "estop",
      () => emergencyStopPrinter(printerId),
      "Emergency stop sent",
    );
    setConfirmEmergencyStop(false);
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

  function deleteRemoteFile(file: PrinterFileRead) {
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
    setDeleteTarget(file);
  }

  async function confirmDeleteRemoteFile() {
    if (!deleteTarget) return;
    const file = deleteTarget;
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
      setDeleteTarget(null);
    }
  }

  const ps = snapshot.print_stats || {};
  const vs = snapshot.virtual_sdcard || {};
  const ext = snapshot.extruder || {};
  const bed = snapshot.heater_bed || {};
  const toolhead = snapshot.toolhead || {};
  const webhook = snapshot.webhooks || {};
  const progress = typeof vs.progress === "number" ? vs.progress * 100 : null;
  const hasCurrentPrint = Boolean(ps.filename || ps.state === "printing" || ps.state === "paused");

  if (!printer) {
    return (
      <div className="w-full space-y-4">
        <div className="h-8 w-48 animate-pulse rounded bg-muted" />
        <div className="h-64 w-full animate-pulse rounded bg-muted" />
      </div>
    );
  }

  return (
    <>
      <ConfirmModal
        open={confirmEmergencyStop}
        onClose={() => { if (machineBusy !== "estop") setConfirmEmergencyStop(false); }}
        onConfirm={confirmEmergencyStopAction}
        busy={machineBusy === "estop"}
        title="Emergency stop printer?"
        description="This halts the printer immediately and requires a firmware restart."
        confirmLabel="Emergency stop"
      />
      <ConfirmModal
        open={!!deleteTarget}
        onClose={() => { if (deletingFileId === null) setDeleteTarget(null); }}
        onConfirm={confirmDeleteRemoteFile}
        busy={deletingFileId !== null}
        title="Delete printer file?"
        description={deleteTarget ? `${deleteTarget.remote_filename} will be deleted from ${printer.name}.` : "This file will be deleted from the printer."}
      />
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
              <span className="font-mono text-3xs font-semibold uppercase tracking-wider text-emerald-600">
                Live
              </span>
            </>
          ) : (
            <>
              <WifiOff className="h-3 w-3 text-amber-500" />
              <span className="font-mono text-3xs uppercase tracking-wider text-amber-500">
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
          <span className="rounded border border-border px-2 py-1 font-mono text-3xs uppercase tracking-wider text-muted-foreground">
            {providerLabel(printer)}
          </span>
          {printer.capabilities.support_level === "beta" && (
            <span className="rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 font-mono text-3xs uppercase tracking-wider text-amber-600">
              Beta
            </span>
          )}
          <span className="flex items-center gap-1.5 rounded border border-border bg-background px-2 py-1">
            <span
              className={`w-2 h-2 rounded-full ${STATUS_COLORS[printer.status] || "bg-slate-400"}`}
            />
            <span className="font-mono text-3xs uppercase tracking-wider text-muted-foreground">
              {printer.status}
            </span>
          </span>
        </div>
        <p className="font-mono text-xs text-muted-foreground break-all">
          {providerAddress(printer)}
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
        <TabBar
          tabs={[
            { key: "status" as const, label: "Status" },
            { key: "files" as const, label: "Files" },
            { key: "jobs" as const, label: "Jobs" },
            ...(printer.provider === "moonraker"
              ? [{ key: "config" as const, label: "Config" }]
              : []),
            { key: "diagnostics" as const, label: "Diagnostics" },
            { key: "settings" as const, label: "Settings" },
          ]}
          active={activeTab}
          onChange={(k) => {
            setActiveTab(k);
            if (k === "config" && !moonrakerConfig) loadMoonrakerConfig();
          }}
          className="gap-1 overflow-x-auto -mb-px"
          tabClassName="px-3 py-2 font-mono text-xs uppercase tracking-wider transition-colors text-muted-foreground hover:text-foreground"
          activeTabClassName="text-primary"
        />
      </div>

      {activeTab === "status" && (
      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-3 animate-panel-in">
        {/* Current print */}
        <section className={`${SECTION_CLASS} lg:col-span-2`}>
          <div className={SECTION_HEADER_CLASS}>
            <h2 className="text-sm font-semibold text-foreground">
              Current print
            </h2>
          </div>
          {hasCurrentPrint ? (
          <div className="space-y-5 p-6">
            <Row label="FILE" value={ps.filename || "—"} truncate />
            <Row label="STATE" value={ps.state || "—"} capitalize />

            <div>
              <div className="flex items-center justify-between mb-2">
                <span className="font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                  Progress
                </span>
                <span className="font-mono text-xs text-foreground font-semibold">
                  {progress != null ? `${progress.toFixed(1)}%` : "—"}
                </span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded bg-muted">
                <div
                  className="h-full w-full origin-left bg-primary transition-transform duration-slow ease-linear"
                  style={{ transform: `scaleX(${Math.min(100, progress ?? 0) / 100})` }}
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
              <p className="font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                {auth.isAuthenticated
                  ? "Controls use your signed-in user session."
                  : "Sign in to control printers."}
              </p>
            </div>
          </div>
          ) : (
            <div className="flex min-h-64 flex-col items-center justify-center px-6 py-10 text-center">
              <span className="mb-4 inline-flex rounded-full bg-muted p-3 text-muted-foreground">
                <FileText className="h-5 w-5" />
              </span>
              <h3 className="text-sm font-semibold text-foreground">No active print</h3>
              <p className="mt-1 max-w-sm text-xs leading-5 text-muted-foreground">
                {printer.status === "offline"
                  ? "Printer is offline. Print details will appear after it reconnects."
                  : "Start a file from the Files tab to see live progress and controls here."}
              </p>
              {printer.status !== "offline" && printer.capabilities.can_list_files && (
                <button
                  type="button"
                  onClick={() => setActiveTab("files")}
                  className={cn(BTN_SECONDARY, "mt-5")}
                >
                  <FileText className="h-3.5 w-3.5" />
                  Browse files
                </button>
              )}
            </div>
          )}
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
          <div className="space-y-5 p-5">
            <div className="grid grid-cols-2 gap-3">
              <TempRow label="Hotend" cur={ext.temperature} tgt={ext.target} />
              <TempRow label="Bed" cur={bed.temperature} tgt={bed.target} />
            </div>

            {printer.capabilities.can_send_gcode && (
              <div className="border-t border-border pt-4 space-y-4">
                <div className="space-y-2">
                  <span className="font-mono text-3xs uppercase tracking-wider text-muted-foreground">
                    Preheat
                  </span>
                  <div className="flex flex-wrap gap-2">
                    {PREHEAT_PRESETS.map((p) => (
                      <button
                        key={p.name}
                        onClick={() => preheat(p)}
                        disabled={!auth.isAuthenticated || machineBusy !== null}
                        title={`Hotend ${p.hotend}°C · Bed ${p.bed}°C`}
                        className={BTN_SECONDARY}
                      >
                        {machineBusy === `preheat-${p.name}` && (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        )}
                        {p.name}
                      </button>
                    ))}
                    <button
                      onClick={cooldown}
                      disabled={!auth.isAuthenticated || machineBusy !== null}
                      className={BTN_SECONDARY}
                    >
                      {machineBusy === "cooldown" && (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      )}
                      Cooldown
                    </button>
                  </div>
                </div>

                <SetTempInput
                  label="Hotend target"
                  value={hotendTarget}
                  onChange={setHotendTarget}
                  onApply={() =>
                    applyTemp("extruder", hotendTarget, () => setHotendTarget(""))
                  }
                  busy={machineBusy === "set-extruder"}
                  disabled={!auth.isAuthenticated || machineBusy !== null}
                />
                <SetTempInput
                  label="Bed target"
                  value={bedTarget}
                  onChange={setBedTarget}
                  onApply={() =>
                    applyTemp("bed", bedTarget, () => setBedTarget(""))
                  }
                  busy={machineBusy === "set-bed"}
                  disabled={!auth.isAuthenticated || machineBusy !== null}
                />

                <div className="border-t border-border pt-4 flex flex-wrap gap-2">
                  <button
                    onClick={() =>
                      void machineAction(
                        "home",
                        () => homePrinter(printerId),
                        "Homing all axes",
                      )
                    }
                    disabled={!auth.isAuthenticated || machineBusy !== null}
                    className={BTN_SECONDARY}
                  >
                    {machineBusy === "home" ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Home className="h-3.5 w-3.5" />
                    )}
                    Home all
                  </button>
                  <button
                    onClick={emergencyStop}
                    disabled={!auth.isAuthenticated || machineBusy !== null}
                    className={BTN_DANGER}
                  >
                    {machineBusy === "estop" ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Power className="h-3.5 w-3.5" />
                    )}
                    E-stop
                  </button>
                </div>
              </div>
            )}
          </div>
        </section>
      </div>
      )}

      {activeTab === "files" && (
      <section className={`${SECTION_CLASS} animate-panel-in`}>
        <div className={SECTION_HEADER_CLASS}>
          <div className="flex items-center gap-2">
            <FileText className="h-4 w-4 text-muted-foreground" />
            <h2 className="text-sm font-semibold text-foreground">
              Printer files
            </h2>
            <span className="rounded-full bg-muted px-2 py-0.5 text-3xs font-semibold text-muted-foreground">
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
          <div className="border-b border-border bg-red-50/30 px-6 py-3 font-mono text-2xs text-red-600 break-words dark:bg-red-950/20">
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
                <tr className="border-b border-border text-left font-mono text-3xs uppercase tracking-wider text-muted-foreground">
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
                          className="font-mono text-xs text-foreground hover:text-primary hover:underline"
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
                      <span className="font-mono text-3xs uppercase tracking-wider px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-600">
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

      {activeTab === "settings" && (
        <PrinterSettings
          printer={printer}
          canEdit={auth.isAuthenticated}
          onSaved={(updated) => setPrinter(updated)}
        />
      )}

      {/* History */}
      {activeTab === "jobs" && (
      <section className={`${SECTION_CLASS} animate-panel-in`}>
        <div className={SECTION_HEADER_CLASS}>
          <h2 className="text-sm font-semibold text-foreground">
            Print history
          </h2>
          {jobs.length > 0 && (
            <span className="rounded-full bg-muted px-2 py-0.5 font-mono text-3xs font-semibold text-muted-foreground">
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
                <tr className="border-b border-border text-left font-mono text-3xs uppercase tracking-wider text-muted-foreground">
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
                        className="font-mono text-xs text-foreground hover:text-primary hover:underline"
                        title={j.remote_filename}
                      >
                        {j.remote_filename}
                      </Link>
                    </td>
                    <td className="py-3 px-4">
                      <span className="rounded bg-muted px-2 py-0.5 font-mono text-3xs uppercase tracking-wider text-foreground">
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
      <section className={`${SECTION_CLASS} animate-panel-in`}>
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
      <section className={`${SECTION_CLASS} animate-panel-in`}>
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
                          <div className="mt-1 font-mono text-2xs text-red-600 break-words">
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
                      <div className="font-mono text-3xs uppercase tracking-wider text-muted-foreground">
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
                <div className="font-mono text-3xs uppercase tracking-wider text-amber-600 font-semibold">
                  Unsupported in this provider
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  {diagnostics.unsupported_actions.map((action) => (
                    <span
                      key={action}
                      className="rounded border border-amber-500/40 px-2 py-1 font-mono text-3xs uppercase tracking-wider text-amber-600"
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
    </>
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
              <div className="font-mono text-3xs uppercase tracking-wider text-muted-foreground truncate">
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

function PrinterSettings({
  printer,
  canEdit,
  onSaved,
}: {
  printer: PrinterRead;
  canEdit: boolean;
  onSaved: (printer: PrinterRead) => void;
}) {
  const [name, setName] = useState(printer.name);
  const [modelName, setModelName] = useState(printer.model_name ?? "");
  const [group, setGroup] = useState(printer.group ?? "");
  const [notes, setNotes] = useState(printer.notes ?? "");
  const [address, setAddress] = useState(providerAddress(printer));
  const [serial, setSerial] = useState(printer.bambu_serial ?? "");
  const [username, setUsername] = useState(printer.prusalink_username ?? "");
  const [mainboardId, setMainboardId] = useState(printer.elegoo_centauri_mainboard_id ?? "");
  const [secret, setSecret] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const secretLabel = printer.provider === "bambu_lan"
    ? "LAN access code"
    : printer.provider === "prusalink"
      ? printer.prusalink_auth_mode === "api_key" ? "API key" : "Password"
      : printer.provider === "elegoo_centauri"
        ? "Printer access code"
        : printer.provider === "octoprint"
          ? "API key"
          : "Moonraker API key";

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!canEdit || saving) return;
    setSaving(true);
    setError(null);
    const payload: PrinterUpdate = {
      name: name.trim(),
      model_name: modelName,
      group,
      notes,
    };
    if (printer.provider === "moonraker") {
      payload.moonraker_url = address;
      if (secret) payload.api_key = secret;
    } else if (printer.provider === "bambu_lan") {
      payload.bambu_host = address;
      payload.bambu_serial = serial;
      if (secret) payload.bambu_access_code = secret;
    } else if (printer.provider === "prusalink") {
      payload.prusalink_url = address;
      payload.prusalink_username = username;
      if (secret) {
        if (printer.prusalink_auth_mode === "api_key") payload.prusalink_api_key = secret;
        else payload.prusalink_password = secret;
      }
    } else if (printer.provider === "elegoo_centauri") {
      payload.elegoo_centauri_host = address;
      payload.elegoo_centauri_mainboard_id = mainboardId;
      if (secret) payload.elegoo_centauri_access_code = secret;
    } else if (printer.provider === "octoprint") {
      payload.octoprint_url = address;
      if (secret) payload.octoprint_api_key = secret;
    }
    try {
      const updated = await updatePrinter(printer.id, payload);
      onSaved(updated);
      setSecret("");
      toast.success("Printer settings saved");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Could not save printer settings";
      setError(message);
      toast.error(err);
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={save} className={`${SECTION_CLASS} animate-panel-in`}>
      <div className={SECTION_HEADER_CLASS}>
        <div className="flex items-center gap-2">
          <Settings className="h-4 w-4 text-muted-foreground" />
          <div>
            <h2 className="text-sm font-semibold text-foreground">Printer settings</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">Connection and display details</p>
          </div>
        </div>
        <Button type="submit" size="sm" loading={saving} disabled={!canEdit || !name.trim()}>
          Save changes
        </Button>
      </div>
      <div className="grid gap-6 p-5 lg:grid-cols-2 lg:p-6">
        <div className="space-y-4">
          <SettingsField label="Name">
            <Input value={name} onChange={(e) => setName(e.target.value)} required disabled={!canEdit} />
          </SettingsField>
          <SettingsField label="Model" hint="Pick a known model or type your own">
            {/* ponytail: native datalist — suggestions from the catalog, free text still allowed */}
            <Input
              list="printer-model-options"
              value={modelName}
              onChange={(e) => setModelName(e.target.value)}
              placeholder={printer.detected_model ?? "Auto-detected"}
              disabled={!canEdit}
            />
            <datalist id="printer-model-options">
              {PRINTER_MODEL_OPTIONS.map((model) => (
                <option key={model} value={model} />
              ))}
            </datalist>
          </SettingsField>
          <SettingsField label="Group" hint="Optional farm grouping">
            <Input value={group} onChange={(e) => setGroup(e.target.value)} placeholder="Workshop" disabled={!canEdit} />
          </SettingsField>
          <SettingsField label="Notes">
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={4}
              disabled={!canEdit}
              className="w-full resize-y rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
            />
          </SettingsField>
        </div>
        <div className="space-y-4 rounded-lg border border-border bg-muted/20 p-4">
          <div>
            <p className="text-sm font-semibold text-foreground">Connection</p>
            <p className="mt-1 text-xs text-muted-foreground">{providerLabel(printer)} configuration</p>
          </div>
          <SettingsField label={printer.provider === "moonraker" || printer.provider === "prusalink" || printer.provider === "octoprint" ? "URL" : "Host or IP"}>
            <Input value={address} onChange={(e) => setAddress(e.target.value)} required disabled={!canEdit} />
          </SettingsField>
          {printer.provider === "bambu_lan" && (
            <SettingsField label="Printer serial">
              <Input value={serial} onChange={(e) => setSerial(e.target.value)} required disabled={!canEdit} />
            </SettingsField>
          )}
          {printer.provider === "prusalink" && printer.prusalink_auth_mode !== "api_key" && (
            <SettingsField label="Username">
              <Input value={username} onChange={(e) => setUsername(e.target.value)} required disabled={!canEdit} />
            </SettingsField>
          )}
          {printer.provider === "elegoo_centauri" && printer.provider_variant === "elegoo_centauri_carbon" && (
            <SettingsField label="Mainboard ID" hint="Used for reliable reconnection">
              <Input value={mainboardId} onChange={(e) => setMainboardId(e.target.value)} disabled={!canEdit} />
            </SettingsField>
          )}
          <SettingsField label={secretLabel} hint="Leave blank to keep current value">
            <Input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="Unchanged" disabled={!canEdit} />
          </SettingsField>
          {!canEdit && <p className="text-xs text-warning">Sign in to edit printer settings.</p>}
          {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
        </div>
      </div>
    </form>
  );
}

function SettingsField({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block space-y-1.5">
      <span className="flex items-center justify-between gap-3 text-xs font-medium text-foreground">
        {label}
        {hint && <span className="font-normal text-muted-foreground">{hint}</span>}
      </span>
      {children}
    </label>
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
      <pre className="max-h-[420px] overflow-auto bg-muted/40 p-4 font-mono text-2xs leading-5 text-foreground">
        {JSON.stringify(data, null, 2)}
      </pre>
    </div>
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
      <div className="font-mono text-3xs uppercase tracking-wider text-muted-foreground">
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
        <div className="font-mono text-3xs uppercase tracking-wider text-muted-foreground">
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
      <span className="font-mono text-3xs uppercase tracking-wider text-muted-foreground flex-shrink-0">
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

function SetTempInput({
  label,
  value,
  onChange,
  onApply,
  busy,
  disabled,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  onApply: () => void;
  busy?: boolean;
  disabled?: boolean;
}) {
  return (
    <div className="flex items-end gap-2">
      <label className="flex-1 space-y-1">
        <span className="font-mono text-3xs uppercase tracking-wider text-muted-foreground">
          {label}
        </span>
        <input
          type="number"
          min={0}
          max={500}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !disabled) onApply();
          }}
          placeholder="°C"
          className="w-full rounded-md border border-border bg-background px-2.5 py-1.5 font-mono text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
        />
      </label>
      <button
        onClick={onApply}
        disabled={disabled || value === ""}
        className={BTN_SECONDARY}
      >
        {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
        Set
      </button>
    </div>
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
    <div className="rounded-md border border-border bg-muted/30 p-3">
      <div className="flex items-center gap-1.5">
        <Thermometer className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="font-mono text-3xs uppercase tracking-wider text-muted-foreground">
          {label}
        </span>
      </div>
      <div className="mt-2 font-mono text-xl font-semibold leading-none text-foreground">
        {cur != null ? cur.toFixed(1) : "—"}
        <span className="ml-0.5 text-xs font-normal text-muted-foreground">°C</span>
      </div>
      <div className="mt-1 min-h-4 font-mono text-3xs text-muted-foreground">
        {tgt != null && tgt > 0 ? `Target ${tgt.toFixed(0)}°C` : "No target"}
      </div>
      {pct != null && (
        <div className="mt-2 h-1 w-full overflow-hidden rounded bg-muted">
          <div
            className="h-full w-full origin-left bg-primary transition-transform duration-slow ease-linear"
            style={{ transform: `scaleX(${pct / 100})` }}
          />
        </div>
      )}
    </div>
  );
}
