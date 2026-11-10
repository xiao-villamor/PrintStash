"use client";

import { createPortal } from "react-dom";

import { usePrinterSettingsCommand } from "@/features/printers/settings-edit";
import { PrinterSettingsReview } from "@/features/printers/settings-review";

import {
  currentLocale,
  knownUiText,
  uiMessage,
  uiText,
  type MessageDescriptor,
} from "@/lib/locale";
import { formatNumber } from "@/lib/format";
import { useUiLocale } from "@/lib/i18n";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Link } from "@/lib/link";
import { useSearchParams } from "@/lib/navigation";
import type { ProviderJsonObject, ProviderJsonValue } from "@/types/printers";
import {
  PrinterFileRead,
  PrinterRead,
  PrinterSnapshot,
  PrinterUpdate,
  PrintJobRead,
  PrinterDiagnostics,
} from "@/types";
import {
  cancelPrinter,
  emergencyStopPrinter,
  homePrinter,
  openPrinterWS,
  pausePrinter,
  resumePrinter,
  setPrinterTemperature,
} from "@/lib/api";
import {
  printerKeys,
  usePrinterResources,
  usePrinterFileMutation,
} from "@/features/printers/queries";
import { getSessionVersion } from "@/lib/session-transport";
import { ApiError, userMessage } from "@/lib/errors";
import { isLoggedIn, onAuthChange } from "@/lib/auth-store";
import { toast } from "@/lib/toast";
import { useRequireAuth } from "@/lib/use-require-auth";
import { formatBytes, formatDuration } from "@/lib/format";
import { Button, buttonVariants } from "@/components/ui/button";
import { PageFailure } from "@/components/page-failure";
import { Input } from "@/components/ui/input";
import { TabBar } from "@/components/ui/tabs";
import { Modal } from "@/components/ui/modal";
import { PageHeader } from "@/components/ui/page-header";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { FleetMaintenancePanel } from "@/components/fleet-panels";
import { PrinterMaterials } from "@/components/printer-materials";
import { PrintJobReproducibility } from "@/components/print-job-reproducibility";
import { cn } from "@/lib/utils";
import { printJobArtifactLabel } from "@/lib/print-job-reproducibility";
import { Localized } from "@/components/ui/localized";
import "./printer-detail-content.css";
import "./printer-detail.css";
import { PRINTER_MODEL_OPTIONS, providerAddress, providerLabel } from "@/lib/printer-providers";
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  Pause,
  Play,
  Square,
  RefreshCcw,
  Trash2,
} from "lucide-react";

const PREHEAT_PRESETS: { name: string; hotend: number; bed: number }[] = [
  { name: "PLA", hotend: 200, bed: 60 },
  { name: "PETG", hotend: 240, bed: 80 },
  { name: "ABS", hotend: 245, bed: 100 },
];

const BTN_SECONDARY = cn(buttonVariants({ variant: "outline", size: "xs" }), "hover:bg-muted");
const BTN_DANGER = cn(
  buttonVariants({ variant: "outline", size: "xs" }),
  "border-red-300/50 text-red-600 hover:bg-red-50 dark:hover:bg-red-950/40",
);

function checkLabel(name: string): string {
  return knownUiText(name);
}

function actionLabel(name: string): string {
  return knownUiText(name);
}

/**
 * A nested printer object inside a snapshot. `instanceof Object` is false for every
 * JSON scalar and for null, and `Array.isArray` peels off `toolhead.position` and
 * friends, leaving exactly the values that are worth merging into.
 */
function isSnapshotSection(value: ProviderJsonValue): value is ProviderJsonObject {
  return value instanceof Object && !Array.isArray(value);
}

/**
 * Fold one field of a Moonraker `update` patch over the field the snapshot already
 * holds. A patch carries only what changed, so two nested objects merge key-by-key;
 * arrays and scalars replace the previous value wholesale.
 */
function mergeSnapshotField(
  previous: ProviderJsonValue,
  next: ProviderJsonValue,
): ProviderJsonValue {
  if (!isSnapshotSection(previous) || !isSnapshotSection(next)) return next;
  const merged: ProviderJsonObject = { ...previous };
  for (const k of Object.keys(next)) merged[k] = mergeSnapshotField(previous[k], next[k]);
  return merged;
}

/** Apply a Moonraker `update` patch to the held snapshot without dropping untouched fields. */
function mergeSnapshot(a: PrinterSnapshot, b: PrinterSnapshot): PrinterSnapshot {
  const out: PrinterSnapshot = { ...a };
  for (const k of Object.keys(b)) out[k] = mergeSnapshotField(a[k], b[k]);
  return out;
}

export function PrinterDetailPage({
  printerId,
  initialPrinter,
}: {
  printerId: number;
  initialPrinter?: PrinterRead;
}) {
  const session = useSyncExternalStore(onAuthChange, getSessionVersion, getSessionVersion);
  const [initialSession] = useState(session);
  return (
    <PrinterDetailView
      key={`${printerId}:${session}`}
      printerId={printerId}
      initialPrinter={session === initialSession ? initialPrinter : undefined}
    />
  );
}

function PrinterDetailView({
  printerId,
  initialPrinter,
}: {
  printerId: number;
  initialPrinter?: PrinterRead;
}) {
  useUiLocale();
  const auth = useRequireAuth();
  const resources = usePrinterResources(printerId, initialPrinter);
  const fileMutation = usePrinterFileMutation();
  const { refresh, refreshAll } = resources;
  const {
    printer,
    accessDenied,
    jobs,
    files: printerFiles,
    diagnostics,
    config: moonrakerConfig,
    checkingDiagnostics,
    loadingConfig,
  } = resources;
  const [snapshot, setSnapshot] = useState<PrinterSnapshot>({});
  const [wsConnected, setWsConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"pause" | "resume" | "cancel" | null>(null);
  const [machineBusy, setMachineBusy] = useState<string | null>(null);
  const [temperatureOpen, setTemperatureOpen] = useState(false);
  const [hotendTarget, setHotendTarget] = useState("");
  const [bedTarget, setBedTarget] = useState("");
  const searchParams = useSearchParams();
  const [activeTab, setActiveTab] = useState<
    | "status"
    | "files"
    | "jobs"
    | "materials"
    | "maintenance"
    | "config"
    | "diagnostics"
    | "settings"
  >(() => (searchParams.get("tab") === "settings" ? "settings" : "status"));
  const [printerActionsHost, setPrinterActionsHost] = useState<HTMLDivElement | null>(null);
  const [evidenceJob, setEvidenceJob] = useState<PrintJobRead | null>(null);
  const [startingFileId, setStartingFileId] = useState<number | null>(null);
  const [deletingFileId, setDeletingFileId] = useState<number | null>(null);
  const [syncingFiles, setSyncingFiles] = useState(false);
  const [confirmEmergencyStop, setConfirmEmergencyStop] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<PrinterFileRead | null>(null);
  const lifetime = useRef(0);

  const viewRequests = useRef(new AbortController());
  const loadJobs = useCallback(() => refresh(printerKeys.jobs(printerId)), [refresh, printerId]);
  const loadDiagnostics = () => resources.refresh(printerKeys.diagnostics(printerId));
  const loadMoonrakerConfig = () => resources.refresh(printerKeys.config(printerId));

  useEffect(() => {
    let disposed = false;
    let generation = 0;
    let socket: WebSocket | null = null;
    let controller: AbortController | null = null;
    let reconnect: ReturnType<typeof setTimeout> | null = null;
    let opened = false;

    function retire() {
      generation += 1;
      lifetime.current += 1;
      viewRequests.current.abort();
      viewRequests.current = new AbortController();
      controller?.abort();
      controller = null;
      if (reconnect !== null) clearTimeout(reconnect);
      reconnect = null;
      const previous = socket;
      socket = null;
      if (previous) {
        previous.onopen = null;
        previous.onclose = null;
        previous.onmessage = null;
        previous.onerror = null;
        previous.close();
      }
    }

    async function connect() {
      if (disposed || accessDenied || !isLoggedIn()) return;
      const version = generation;
      const ticket = new AbortController();
      controller = ticket;
      try {
        const ws = await openPrinterWS(printerId, ticket.signal);
        if (disposed || version !== generation) {
          ws.close();
          return;
        }
        controller = null;
        socket = ws;
        const current = () => !disposed && version === generation && socket === ws;
        ws.onopen = () => {
          if (!current()) return;
          setWsConnected(true);
          if (opened) void refreshAll();
          opened = true;
        };
        ws.onclose = () => {
          if (!current()) return;
          socket = null;
          ws.onopen = null;
          ws.onclose = null;
          ws.onmessage = null;
          ws.onerror = null;
          setWsConnected(false);
          reconnect = setTimeout(() => {
            reconnect = null;
            void connect();
          }, 3000);
        };
        ws.onerror = () => {};
        ws.onmessage = (ev) => {
          if (!current()) return;
          try {
            const msg = JSON.parse(ev.data);
            if (msg.type === "snapshot") {
              setSnapshot(msg.data || {});
            } else if (msg.type === "update") {
              setSnapshot((prev) => mergeSnapshot(prev, msg.data || {}));
            }
            if (msg?.data?.print_stats?.state) void loadJobs();
          } catch {
            /* Ignore malformed frames. */
          }
        };
      } catch (e) {
        if (disposed || version !== generation) return;
        controller = null;
        setError(
          uiText("WS error: {value1}", { value1: String(e instanceof Error ? e.message : e) }),
        );
      }
    }

    retire();
    void connect();
    const stopAuth = onAuthChange(() => {
      retire();
      setSnapshot({});
      setWsConnected(false);
      setError(null);
      if (isLoggedIn()) void connect();
    });
    return () => {
      disposed = true;
      stopAuth();
      retire();
    };
  }, [accessDenied, printerId, loadJobs, refreshAll]);

  async function control(action: "pause" | "resume" | "cancel", fn: () => Promise<void>) {
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return;
    }
    const version = lifetime.current;
    setBusy(action);
    try {
      await fn();
    } catch (e) {
      if (version === lifetime.current) toast.error(e);
    } finally {
      if (version === lifetime.current) setBusy(null);
    }
  }

  async function machineAction<T>(key: string, fn: () => Promise<T>, success: MessageDescriptor) {
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return;
    }
    const version = lifetime.current;
    setMachineBusy(key);
    try {
      await fn();
      if (version === lifetime.current) toast.success(uiText(success.key, success.values));
    } catch (e) {
      if (version === lifetime.current) toast.error(e);
    } finally {
      if (version === lifetime.current) setMachineBusy(null);
    }
  }

  function applyTemp(heater: "extruder" | "bed", raw: string, clear: () => void) {
    const t = Number(raw);
    if (!Number.isFinite(t) || t < 0 || t > 500) {
      toast.error(uiText("Enter a temperature between 0 and 500."));
      return;
    }
    void machineAction(
      `set-${heater}`,
      () => setPrinterTemperature(printerId, heater, t).then(clear),
      uiMessage("{value1} set to {value2}°C", {
        value1: String(heater === "extruder" ? "Hotend" : "Bed"),
        value2: String(t),
      }),
    );
  }

  function preheat(p: (typeof PREHEAT_PRESETS)[number]) {
    void machineAction(
      `preheat-${p.name}`,
      async () => {
        await setPrinterTemperature(printerId, "extruder", p.hotend);
        await setPrinterTemperature(printerId, "bed", p.bed);
      },
      uiMessage("Preheating for {value1}", { value1: String(p.name) }),
    );
  }

  function cooldown() {
    void machineAction(
      "cooldown",
      async () => {
        await setPrinterTemperature(printerId, "extruder", 0);
        await setPrinterTemperature(printerId, "bed", 0);
      },
      uiMessage("printer.cooldownSuccess"),
    );
  }

  function emergencyStop() {
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return;
    }
    setConfirmEmergencyStop(true);
  }

  async function confirmEmergencyStopAction() {
    const version = lifetime.current;
    await machineAction(
      "estop",
      () => emergencyStopPrinter(printerId),
      uiMessage("printer.emergencyStopSuccess"),
    );
    if (version === lifetime.current) setConfirmEmergencyStop(false);
  }

  async function syncFiles() {
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return;
    }
    const version = lifetime.current;
    setSyncingFiles(true);
    setError(null);
    try {
      await fileMutation.mutateAsync({
        kind: "sync",
        printerId,
        signal: viewRequests.current.signal,
      });
      if (version !== lifetime.current) return;
      toast.success(uiText("Printer files synced"));
    } catch (e) {
      if (version === lifetime.current) toast.error(e);
    } finally {
      if (version === lifetime.current) setSyncingFiles(false);
    }
  }

  async function startRemoteFile(file: PrinterFileRead) {
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return;
    }
    const version = lifetime.current;
    setStartingFileId(file.id);
    setError(null);
    try {
      const job = await fileMutation.mutateAsync({
        kind: "start",
        printerId,
        signal: viewRequests.current.signal,
        payload: {
          remote_filename: file.remote_filename,
          file_id: file.file_id,
        },
      });
      if (version !== lifetime.current || !job) return;
      toast.success(uiText("Print started (job #{value1})", { value1: String(job.id) }));
      setActiveTab("status");
    } catch (e) {
      if (version === lifetime.current) toast.error(e);
    } finally {
      if (version === lifetime.current) setStartingFileId(null);
    }
  }

  function deleteRemoteFile(file: PrinterFileRead) {
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return;
    }
    setDeleteTarget(file);
  }

  async function confirmDeleteRemoteFile() {
    if (!deleteTarget) return;
    const file = deleteTarget;
    const version = lifetime.current;
    setDeletingFileId(file.id);
    setError(null);
    try {
      await fileMutation.mutateAsync({
        kind: "delete",
        printerId,
        fileId: file.id,
        signal: viewRequests.current.signal,
      });
      if (version !== lifetime.current) return;
      toast.success(uiText("Printer file deleted"));
    } catch (e) {
      if (version === lifetime.current) toast.error(e);
    } finally {
      if (version === lifetime.current) {
        setDeletingFileId(null);
        setDeleteTarget(null);
      }
    }
  }

  const ps = snapshot.print_stats || {};
  const vs = snapshot.virtual_sdcard || {};
  const ext = snapshot.extruder || {};
  const bed = snapshot.heater_bed || {};
  const toolhead = snapshot.toolhead || {};
  const progress = vs.progress == null ? null : vs.progress * 100;
  const hasCurrentPrint = Boolean(ps.filename || ps.state === "printing" || ps.state === "paused");

  if (!printer) {
    if (resources.error)
      return (
        <PageFailure
          kind={
            resources.error instanceof ApiError && resources.error.status === 401
              ? "authentication"
              : resources.error instanceof ApiError && resources.error.status === 403
                ? "forbidden"
                : resources.error instanceof ApiError && resources.error.status === 404
                  ? "not-found"
                  : resources.error instanceof ApiError &&
                      resources.error.code === "network_unreachable"
                    ? "offline"
                    : "unavailable"
          }
          description={userMessage(resources.error)}
          onRetry={() => void resources.refreshAll()}
        />
      );
    return (
      <div className="w-full space-y-4">
        <div className="h-8 w-48 animate-pulse rounded bg-muted" />
        <div className="h-64 w-full animate-pulse rounded bg-muted" />
      </div>
    );
  }

  return (
    <Localized>
      <>
        <ConfirmModal
          open={confirmEmergencyStop}
          onClose={() => {
            if (machineBusy !== "estop") setConfirmEmergencyStop(false);
          }}
          onConfirm={confirmEmergencyStopAction}
          busy={machineBusy === "estop"}
          title={uiText("Emergency stop printer?")}
          description={uiText(
            "This halts the printer immediately and requires a firmware restart.",
          )}
          confirmLabel={uiText("Emergency stop")}
        />
        <ConfirmModal
          open={!!deleteTarget}
          onClose={() => {
            if (deletingFileId === null) setDeleteTarget(null);
          }}
          onConfirm={confirmDeleteRemoteFile}
          busy={deletingFileId !== null}
          title={uiText("Delete printer file?")}
          description={
            deleteTarget
              ? uiText("{value1} will be deleted from {value2}.", {
                  value1: String(deleteTarget.remote_filename),
                  value2: String(printer.name),
                })
              : uiText("This file will be deleted from the printer.")
          }
        />
        <div className="w-full space-y-6">
          <PageHeader
            title={printer.name}
            description={
              <>
                {providerLabel(printer)}
                {printer.group ? ` · ${printer.group}` : ""} ·{" "}
                {wsConnected ? uiText("Live") : uiText("Reconnecting…")}
              </>
            }
            actions={
              activeTab === "materials" ||
              activeTab === "maintenance" ||
              activeTab === "settings" ? (
                <div ref={setPrinterActionsHost} className="flex flex-wrap gap-2" />
              ) : activeTab === "files" && printer.capabilities.can_list_files ? (
                <Button
                  size="sm"
                  onClick={syncFiles}
                  disabled={syncingFiles || !printer.access.can_admin}
                  title={auth.blockReason ?? uiText("Sync printer files")}
                >
                  {syncingFiles ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <RefreshCcw className="h-3.5 w-3.5" />
                  )}
                  {uiText("Sync printer files")}
                </Button>
              ) : activeTab === "diagnostics" ? (
                <>
                  <Button size="sm" onClick={loadDiagnostics} disabled={checkingDiagnostics}>
                    {checkingDiagnostics ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <RefreshCcw className="h-3.5 w-3.5" />
                    )}
                    {uiText("Run checks")}
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!diagnostics}
                    onClick={() => {
                      if (diagnostics) void copyProviderJson(diagnostics);
                    }}
                  >
                    {uiText("Copy diagnostics")}
                  </Button>
                </>
              ) : (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    if (activeTab === "config") void loadMoonrakerConfig();
                    else void refreshAll();
                  }}
                  disabled={activeTab === "config" && loadingConfig}
                >
                  <RefreshCcw className="h-3.5 w-3.5" />
                  {uiText("Refresh")}
                </Button>
              )
            }
          />

          {printer.capabilities.support_notes.length > 0 && (
            <div className="rounded border border-border bg-muted/40 p-3 text-xs leading-5 text-muted-foreground">
              {printer.capabilities.support_notes.join(" ")}
            </div>
          )}

          {(error || resources.error) && (
            <div className="rounded border border-red-300/50 bg-red-50/30 p-3 font-mono text-sm text-red-600 dark:bg-red-950/20">
              {error ?? userMessage(resources.error)}
              {resources.error && (
                <Button size="xs" onClick={() => void resources.refreshAll()}>
                  {uiText("Retry")}
                </Button>
              )}
            </div>
          )}

          <div className="border-b border-border">
            <TabBar
              tabs={[
                {
                  key: "status" as const,
                  get label() {
                    return uiText("Status");
                  },
                },
                {
                  key: "files" as const,
                  get label() {
                    return uiText("Files");
                  },
                },
                {
                  key: "jobs" as const,
                  get label() {
                    return uiText("Jobs");
                  },
                },
                ...(printer.access.can_print
                  ? [
                      {
                        key: "materials" as const,
                        get label() {
                          return uiText("Materials & tools");
                        },
                      },
                    ]
                  : []),
                ...(printer.access.can_admin
                  ? [
                      {
                        key: "maintenance" as const,
                        get label() {
                          return uiText("Maintenance");
                        },
                      },
                    ]
                  : []),
                ...(printer.provider === "moonraker" && printer.access.can_admin
                  ? [
                      {
                        key: "config" as const,
                        get label() {
                          return uiText("Config");
                        },
                      },
                    ]
                  : []),
                ...(printer.access.can_admin
                  ? [
                      {
                        key: "diagnostics" as const,
                        get label() {
                          return uiText("Diagnostics");
                        },
                      },
                      {
                        key: "settings" as const,
                        get label() {
                          return uiText("Settings");
                        },
                      },
                    ]
                  : []),
              ]}
              active={activeTab}
              onChange={(k) => {
                setActiveTab(k);
              }}
              className="gap-1 overflow-x-auto -mb-px"
              tabClassName="px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              activeTabClassName="bg-transparent font-semibold text-foreground hover:bg-transparent"
            />
          </div>

          {activeTab === "status" && (
            <section className="border border-border bg-card p-5">
              <div
                className={`mb-5 grid grid-cols-2 gap-y-4 border-b border-border pb-5 ${hasCurrentPrint ? "md:grid-cols-4" : "md:grid-cols-3"}`}
              >
                <StatusMetric
                  label={uiText("State")}
                  value={knownUiText(ps.state || printer.status)}
                />
                {hasCurrentPrint ? (
                  <>
                    <StatusMetric
                      label={uiText("Progress")}
                      value={
                        progress != null
                          ? `${formatNumber(progress, { maximumFractionDigits: 1 })}%`
                          : "—"
                      }
                    />
                    <StatusMetric
                      label={uiText("Elapsed")}
                      value={formatDuration(ps.print_duration)}
                    />
                    <StatusMetric
                      label={uiText("Total")}
                      value={formatDuration(ps.total_duration)}
                    />
                  </>
                ) : (
                  <>
                    <StatusMetric
                      label={uiText("Connection")}
                      value={wsConnected ? uiText("Live") : knownUiText(printer.status)}
                    />
                    <StatusMetric label={uiText("Group")} value={printer.group || "—"} />
                  </>
                )}
              </div>
              {ps.filename && (
                <p className="mb-4 break-all text-[13px] text-muted-foreground">{ps.filename}</p>
              )}
              <div className="divide-y divide-border">
                <div className="flex min-h-20 flex-wrap items-center justify-between gap-4 py-4">
                  <div className="min-w-0">
                    <h2 className="text-sm font-semibold">{uiText("Current job")}</h2>
                    <p className="mt-1 text-[13px] text-muted-foreground">
                      {hasCurrentPrint
                        ? knownUiText(ps.state || printer.status)
                        : uiText("No active print")}
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    {hasCurrentPrint ? (
                      <>
                        <ControlButton
                          onClick={() => control("pause", () => pausePrinter(printerId))}
                          disabled={
                            !printer.access.can_control ||
                            !printer.capabilities.can_pause ||
                            ps.state !== "printing" ||
                            busy !== null
                          }
                          busy={busy === "pause"}
                          icon={Pause}
                          label={uiText("Pause")}
                        />
                        {ps.state === "paused" && (
                          <ControlButton
                            onClick={() => control("resume", () => resumePrinter(printerId))}
                            disabled={
                              !printer.access.can_control ||
                              !printer.capabilities.can_resume ||
                              busy !== null
                            }
                            busy={busy === "resume"}
                            icon={Play}
                            label={uiText("Resume")}
                          />
                        )}
                        <ControlButton
                          onClick={() => control("cancel", () => cancelPrinter(printerId))}
                          disabled={
                            !printer.access.can_control ||
                            !printer.capabilities.can_cancel ||
                            (ps.state !== "printing" && ps.state !== "paused") ||
                            busy !== null
                          }
                          busy={busy === "cancel"}
                          icon={Square}
                          label={uiText("Cancel")}
                          destructive
                        />
                      </>
                    ) : (
                      printer.status !== "offline" &&
                      printer.capabilities.can_list_files && (
                        <Button variant="outline" size="sm" onClick={() => setActiveTab("files")}>
                          {uiText("Browse files")}
                        </Button>
                      )
                    )}
                  </div>
                </div>
                <div className="flex min-h-20 flex-wrap items-center justify-between gap-4 py-4">
                  <div>
                    <h2 className="text-sm font-semibold">{uiText("Temperatures")}</h2>
                    <p className="mt-1 text-[13px] text-muted-foreground">
                      {uiText("Hotend")} {ext.temperature ?? "—"} / {ext.target ?? "—"}{" "}
                      {uiText("°C ·")} {uiText("Bed")} {bed.temperature ?? "—"} /{" "}
                      {bed.target ?? "—"} °C
                    </p>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setTemperatureOpen(true)}
                    disabled={
                      !printer.access.can_control ||
                      !printer.capabilities.can_send_gcode ||
                      machineBusy !== null
                    }
                  >
                    {uiText("Set temperatures")}
                  </Button>
                </div>
                <div className="flex min-h-20 flex-wrap items-center justify-between gap-4 py-4">
                  <div>
                    <h2 className="text-sm font-semibold">{uiText("Motion and control")}</h2>
                    <p className="mt-1 text-[13px] text-muted-foreground">
                      {uiText("Homed")}: {toolhead.homed_axes || "—"}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        void machineAction(
                          "home",
                          () => homePrinter(printerId),
                          uiMessage("printer.homeSuccess"),
                        )
                      }
                      disabled={
                        !printer.access.can_control ||
                        !printer.capabilities.can_send_gcode ||
                        machineBusy !== null
                      }
                    >
                      {machineBusy === "home" && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                      {uiText("Home all")}
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={emergencyStop}
                      disabled={
                        !printer.access.can_control ||
                        !printer.capabilities.can_send_gcode ||
                        machineBusy !== null
                      }
                    >
                      {machineBusy === "estop" && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                      {uiText("Emergency stop")}
                    </Button>
                  </div>
                </div>
              </div>
              <p className="mt-4 text-xs leading-relaxed text-muted-foreground">
                {printer.access.can_control
                  ? uiText("Controls permitted by your printer role.")
                  : uiText("Control role required.")}
              </p>
            </section>
          )}
          <Modal
            open={temperatureOpen}
            onClose={() => {
              if (machineBusy === null) setTemperatureOpen(false);
            }}
            title={uiText("Set temperatures")}
            footer={
              <Button
                variant="outline"
                onClick={() => setTemperatureOpen(false)}
                disabled={machineBusy !== null}
              >
                {uiText("Close")}
              </Button>
            }
          >
            <div className="space-y-5">
              {printer.status === "offline" && (
                <p className="text-sm text-muted-foreground">
                  {uiText(
                    "Printer is offline. Check its address and connection before sending commands.",
                  )}
                </p>
              )}
              <SetTempInput
                label={uiText("Hotend target")}
                value={hotendTarget}
                onChange={setHotendTarget}
                onApply={() => applyTemp("extruder", hotendTarget, () => setHotendTarget(""))}
                busy={machineBusy === "set-extruder"}
                disabled={
                  !printer.access.can_control ||
                  !printer.capabilities.can_send_gcode ||
                  machineBusy !== null
                }
              />
              <SetTempInput
                label={uiText("Bed target")}
                value={bedTarget}
                onChange={setBedTarget}
                onApply={() => applyTemp("bed", bedTarget, () => setBedTarget(""))}
                busy={machineBusy === "set-bed"}
                disabled={
                  !printer.access.can_control ||
                  !printer.capabilities.can_send_gcode ||
                  machineBusy !== null
                }
              />
              <div className="flex flex-wrap gap-2 border-t border-border pt-4">
                {PREHEAT_PRESETS.map((p) => (
                  <Button
                    key={p.name}
                    variant="outline"
                    size="sm"
                    onClick={() => preheat(p)}
                    disabled={
                      !printer.access.can_control ||
                      !printer.capabilities.can_send_gcode ||
                      machineBusy !== null
                    }
                  >
                    {p.name}
                  </Button>
                ))}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={cooldown}
                  disabled={
                    !printer.access.can_control ||
                    !printer.capabilities.can_send_gcode ||
                    machineBusy !== null
                  }
                >
                  {uiText("Cooldown")}
                </Button>
              </div>
            </div>
          </Modal>

          {activeTab === "files" && (
            <section className="printer-reference-surface">
              {printer.last_error && printer.status === "offline" && (
                <p className="printer-reference-note mb-5 text-destructive">
                  {uiText(
                    "Could not connect to the printer. Check its address and network connection, then retry.",
                  )}
                </p>
              )}
              {!printer.capabilities.can_list_files ? (
                <>
                  <p className="printer-reference-note">
                    {uiText("Printer file inventory is not supported by this provider.")}
                  </p>
                  <div className="printer-reference-row">
                    <div>
                      <h3>{uiText("Jobs")}</h3>
                      <p>{uiText("Files you send appear in Jobs.")}</p>
                    </div>
                    <div className="printer-reference-actions">
                      <Button size="xs" variant="outline" onClick={() => setActiveTab("jobs")}>
                        {uiText("Open in Jobs")}
                      </Button>
                    </div>
                  </div>
                </>
              ) : printerFiles.length === 0 ? (
                <p className="py-8 text-sm text-muted-foreground">
                  {uiText("No printer files synced yet.")}
                </p>
              ) : (
                <div className="printer-reference-list">
                  {printerFiles.map((f) => (
                    <div key={f.id} className="printer-reference-row">
                      <div>
                        <h3>{f.remote_filename}</h3>
                        <p>
                          {f.model_id
                            ? `${uiText("Vault match")}: ${f.model_name ?? f.original_filename}`
                            : uiText("External")}{" "}
                          · {f.size_bytes != null ? formatBytes(f.size_bytes) : "—"} ·{" "}
                          {knownUiText(f.matched_by)} · {uiText("Last seen")}{" "}
                          {new Date(f.last_seen_at).toLocaleString(currentLocale())}
                        </p>
                      </div>
                      <div className="printer-reference-actions">
                        {f.model_id && (
                          <Button asChild size="xs" variant="outline">
                            <Link href={`/models/${f.model_id}`}>{uiText("Open Model")}</Link>
                          </Button>
                        )}
                        <button
                          onClick={() => startRemoteFile(f)}
                          disabled={
                            !printer.access.can_print ||
                            !printer.capabilities.can_start ||
                            startingFileId !== null ||
                            deletingFileId !== null
                          }
                          title={auth.blockReason ?? uiText("Start this printer file")}
                          className={BTN_SECONDARY}
                        >
                          {startingFileId === f.id ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <Play className="h-3 w-3" />
                          )}
                          {uiText("Start")}
                        </button>
                        <button
                          onClick={() => deleteRemoteFile(f)}
                          disabled={
                            !printer.access.can_admin ||
                            printer.provider !== "moonraker" ||
                            deletingFileId !== null ||
                            startingFileId !== null ||
                            ps.filename === f.remote_filename
                          }
                          title={
                            ps.filename === f.remote_filename
                              ? uiText("Cannot delete active print file")
                              : (auth.blockReason ?? uiText("Delete this printer file"))
                          }
                          className={BTN_DANGER}
                        >
                          {deletingFileId === f.id ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <Trash2 className="h-3 w-3" />
                          )}
                          {uiText("Delete")}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
              {printer.capabilities.can_list_files && (
                <p className="printer-reference-footnote">
                  {uiText(
                    "An active print file cannot be deleted. Start checks the loaded material; it does not check that the G-code is safe for this printer.",
                  )}
                </p>
              )}
            </section>
          )}

          {activeTab === "settings" && (
            <PrinterSettings
              actionTarget={printerActionsHost}
              printer={printer}
              canEdit={printer.access.can_admin}
              key={printer.id}
            />
          )}

          {activeTab === "materials" && (
            <PrinterMaterials printer={printer} actionsHost={printerActionsHost} />
          )}

          {activeTab === "maintenance" && (
            <div className="animate-panel-in">
              <FleetMaintenancePanel
                compact
                actionsHost={printerActionsHost}
                printers={[printer]}
                onPrintersChanged={() => {
                  void resources.refresh(printerKeys.detail(printerId));
                }}
              />
            </div>
          )}

          {activeTab === "jobs" && (
            <section className="printer-reference-surface">
              {jobs.length === 0 ? (
                <p className="py-8 text-sm text-muted-foreground">{uiText("No print jobs yet.")}</p>
              ) : (
                <div className="printer-reference-list">
                  {jobs.map((job) => {
                    const archived =
                      job.artifact_evidence === "vault" ||
                      job.artifact_evidence.endsWith("_archived");
                    return (
                      <div key={job.id} className="printer-reference-row">
                        <div>
                          <h3>{printJobArtifactLabel(job)}</h3>
                          <p>
                            {new Date(job.created_at).toLocaleString(currentLocale())} ·{" "}
                            {knownUiText(job.state)}{" "}
                            {formatNumber(job.progress * 100, { maximumFractionDigits: 0 })}% ·{" "}
                            {uiText("Started")}:{" "}
                            {job.started_at
                              ? new Date(job.started_at).toLocaleString(currentLocale())
                              : uiText("Not recorded")}{" "}
                            · {uiText("Finished")}:{" "}
                            {job.finished_at
                              ? new Date(job.finished_at).toLocaleString(currentLocale())
                              : uiText("Not recorded")}
                            {job.source === "external"
                              ? ` · ${knownUiText(job.artifact_evidence)}`
                              : ""}
                          </p>
                        </div>
                        <div className="printer-reference-actions">
                          {archived && (
                            <Button asChild size="xs" variant="outline">
                              <Link href={`/models/${job.model_id}`}>{uiText("Open Model")}</Link>
                            </Button>
                          )}
                          <Button size="xs" variant="outline" onClick={() => setEvidenceJob(job)}>
                            {uiText("Evidence")}
                          </Button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
              <p className="printer-reference-footnote">
                {uiText(
                  "Each entry retains state, progress, start and finish times, file links, archive evidence and reproducibility.",
                )}
              </p>
              <Modal
                open={evidenceJob !== null}
                onClose={() => setEvidenceJob(null)}
                title={evidenceJob ? printJobArtifactLabel(evidenceJob) : uiText("Evidence")}
                className="max-w-3xl"
              >
                {evidenceJob && (
                  <PrintJobReproducibility
                    job={evidenceJob}
                    previewHref={
                      evidenceJob.artifact_evidence === "vault" ||
                      evidenceJob.artifact_evidence.endsWith("_archived")
                        ? `/models/${evidenceJob.model_id}`
                        : null
                    }
                  />
                )}
              </Modal>
            </section>
          )}

          {activeTab === "config" && printer.provider === "moonraker" && (
            <section className="printer-reference-surface">
              {!moonrakerConfig ? (
                <p className="py-8 text-sm text-muted-foreground">
                  {uiText(loadingConfig ? "Loading config…" : "Config not loaded.")}
                </p>
              ) : (
                <div className="space-y-5">
                  <ConfigBlock
                    title={uiText("Klipper config")}
                    data={moonrakerConfig.klipper_config}
                  />
                  <ConfigBlock
                    title={uiText("Moonraker config")}
                    data={moonrakerConfig.moonraker_config}
                  />
                  <ConfigSummary title="Moonraker" data={moonrakerConfig.server_info} />
                  <ConfigSummary title="Klipper" data={moonrakerConfig.printer_info} />
                </div>
              )}
              <p className="printer-reference-footnote">
                {uiText(
                  "Read-only values reported by Moonraker. They do not verify G-code compatibility.",
                )}
              </p>
            </section>
          )}

          {activeTab === "diagnostics" && (
            <section className="printer-reference-surface">
              {printer.last_error && printer.status === "offline" && (
                <p className="printer-reference-note mb-5">{printer.last_error}</p>
              )}
              {!diagnostics ? (
                <p className="py-8 text-sm text-muted-foreground">
                  {uiText("Diagnostics have not loaded yet.")}
                </p>
              ) : (
                <div className="printer-reference-list">
                  <div className="printer-reference-row">
                    <div>
                      <h3 className="flex items-center gap-2">
                        {diagnostics.ok ? (
                          <CheckCircle2 className="h-4 w-4 text-success" />
                        ) : (
                          <AlertTriangle className="h-4 w-4 text-warning" />
                        )}
                        {uiText(diagnostics.ok ? "Provider reachable" : "Needs attention")}
                      </h3>
                      <p>
                        {providerLabel({ provider: diagnostics.provider })} ·{" "}
                        {knownUiText(diagnostics.support_level)}
                      </p>
                      {diagnostics.notes.map((note) => (
                        <p key={note}>{knownUiText(note)}</p>
                      ))}
                    </div>
                  </div>
                  {diagnostics.checks.map((check) => (
                    <div key={check.name} className="printer-reference-row">
                      <div>
                        <h3>{checkLabel(check.name)}</h3>
                        <p>
                          {check.detail ??
                            (check.ok ? uiText("OK") : (check.code ?? uiText("provider_error")))}
                        </p>
                      </div>
                    </div>
                  ))}
                  <div className="printer-reference-row">
                    <div>
                      <h3>{uiText("Supported")}</h3>
                      <p>
                        {Object.entries(diagnostics.capabilities)
                          .filter(([, enabled]) => enabled)
                          .map(([name]) => checkLabel(name.replace(/^can_/, "")))
                          .join(" · ") || uiText("None")}
                      </p>
                    </div>
                  </div>
                  <div className="printer-reference-row">
                    <div>
                      <h3>{uiText("Unavailable")}</h3>
                      <p>
                        {[
                          ...new Set([
                            ...Object.entries(diagnostics.capabilities)
                              .filter(([, enabled]) => !enabled)
                              .map(([name]) => checkLabel(name.replace(/^can_/, ""))),
                            ...diagnostics.unsupported_actions.map(actionLabel),
                          ]),
                        ].join(" · ") || uiText("None")}
                      </p>
                    </div>
                  </div>
                </div>
              )}
            </section>
          )}
        </div>
      </>
    </Localized>
  );
}

async function copyProviderJson(data: ProviderJsonObject | PrinterDiagnostics) {
  try {
    const text = JSON.stringify(data, null, 2);
    if (text === undefined) throw new Error("provider_json_missing");
    await navigator.clipboard.writeText(text);
    toast.success(uiText("migration.copied"));
  } catch {
    toast.error(uiText("Copy failed. Select the text and copy it manually."));
  }
}

function ConfigSummary({ title, data }: { title: string; data: ProviderJsonObject }) {
  useUiLocale();
  const rows = Object.entries(data)
    .filter(([, value]) => !(value instanceof Object))
    .slice(0, 8);
  return (
    <section className="printer-reference-list">
      <div className="printer-reference-row">
        <div>
          <h3>{title}</h3>
          <p>
            {rows
              .map(([key, value]) => `${checkLabel(key)}: ${String(value ?? "—")}`)
              .join(" · ") || "—"}
          </p>
        </div>
      </div>
    </section>
  );
}

export function PrinterSettings({
  printer: initialPrinter,
  canEdit,
  compact = false,
  onCancel,
  onSaved,
  onRemove,
  actionTarget,
}: {
  printer: PrinterRead;
  canEdit: boolean;
  compact?: boolean;
  onCancel?: () => void;
  onSaved?: () => void;
  onRemove?: () => void;
  actionTarget?: HTMLElement | null;
}) {
  useUiLocale();
  const [printer, setPrinter] = useState(initialPrinter);
  const command = usePrinterSettingsCommand(printer.id);
  const active = useRef(true);
  const form = useRef<HTMLFormElement>(null);
  useEffect(() => {
    active.current = true;

    return () => {
      active.current = false;
    };
  }, []);
  const [name, setName] = useState(printer.name);
  const [modelName, setModelName] = useState(printer.model_name ?? "");
  const [group, setGroup] = useState(printer.group ?? "");
  const [notes, setNotes] = useState(printer.notes ?? "");
  const [address, setAddress] = useState(providerAddress(printer));
  const [serial, setSerial] = useState(printer.bambu_serial ?? "");
  const [username, setUsername] = useState(printer.prusalink_username ?? "");
  const [mainboardId, setMainboardId] = useState(printer.elegoo_centauri_mainboard_id ?? "");
  const [secret, setSecret] = useState("");
  const [providerMaterialSync, setProviderMaterialSync] = useState(
    printer.provider_material_sync_enabled,
  );
  const [operatorReleaseRequired, setOperatorReleaseRequired] = useState(
    printer.operator_release_required,
  );
  const saving = command.busy;
  const [error, setError] = useState<string | null>(null);

  const secretLabel =
    printer.provider === "bambu_lan"
      ? "LAN access code"
      : printer.provider === "prusalink"
        ? printer.prusalink_auth_mode === "api_key"
          ? "API key"
          : "Password"
        : printer.provider === "elegoo_centauri"
          ? "Printer access code"
          : printer.provider === "octoprint"
            ? "API key"
            : "Moonraker API key";

  function adopt(snapshot: PrinterRead) {
    setPrinter(snapshot);
    setName(snapshot.name);
    setModelName(snapshot.model_name ?? "");
    setGroup(snapshot.group ?? "");
    setNotes(snapshot.notes ?? "");
    setAddress(providerAddress(snapshot));
    setSerial(snapshot.bambu_serial ?? "");
    setUsername(snapshot.prusalink_username ?? "");
    setMainboardId(snapshot.elegoo_centauri_mainboard_id ?? "");
    setProviderMaterialSync(snapshot.provider_material_sync_enabled);
    setOperatorReleaseRequired(snapshot.operator_release_required);
    setSecret("");
    setError(null);
  }

  async function save(e?: React.FormEvent, revised = false) {
    e?.preventDefault();
    if (!canEdit || saving || (!revised && command.blocked)) return;
    if (form.current && !form.current.reportValidity()) return;
    const session = getSessionVersion();
    const current = () => active.current && session === getSessionVersion();
    setError(null);
    const payload: PrinterUpdate = {};
    if (name.trim() !== printer.name) payload.name = name.trim();
    if (modelName !== (printer.model_name ?? "")) payload.model_name = modelName;
    if (group !== (printer.group ?? "")) payload.group = group;
    if (notes !== (printer.notes ?? "")) payload.notes = notes;
    if (providerMaterialSync !== printer.provider_material_sync_enabled)
      payload.provider_material_sync_enabled = providerMaterialSync;
    if (operatorReleaseRequired !== printer.operator_release_required)
      payload.operator_release_required = operatorReleaseRequired;
    if (printer.provider === "moonraker") {
      if (address !== providerAddress(printer)) payload.moonraker_url = address;
      if (secret) payload.api_key = secret;
    } else if (printer.provider === "bambu_lan") {
      if (address !== providerAddress(printer)) payload.bambu_host = address;
      if (serial !== (printer.bambu_serial ?? "")) payload.bambu_serial = serial;
      if (secret) payload.bambu_access_code = secret;
    } else if (printer.provider === "prusalink") {
      if (address !== providerAddress(printer)) payload.prusalink_url = address;
      if (username !== (printer.prusalink_username ?? "")) payload.prusalink_username = username;
      if (secret) {
        if (printer.prusalink_auth_mode === "api_key") payload.prusalink_api_key = secret;
        else payload.prusalink_password = secret;
      }
    } else if (printer.provider === "elegoo_centauri") {
      if (address !== providerAddress(printer)) payload.elegoo_centauri_host = address;
      if (mainboardId !== (printer.elegoo_centauri_mainboard_id ?? ""))
        payload.elegoo_centauri_mainboard_id = mainboardId;
      if (secret) payload.elegoo_centauri_access_code = secret;
    } else if (printer.provider === "octoprint") {
      if (address !== providerAddress(printer)) payload.octoprint_url = address;
      if (secret) payload.octoprint_api_key = secret;
    }
    try {
      const updated = await command.save(printer, payload, revised);
      if (!current()) return;
      adopt(updated);
      toast.success(uiText("Printer settings saved"));
      onSaved?.();
    } catch (err) {
      if (!current()) return;
      const message = err instanceof Error ? err.message : "Could not save printer settings";
      setError(message);
      toast.error(err);
    }
  }

  return (
    <Localized>
      <form
        ref={form}
        onSubmit={save}
        className={compact ? "space-y-5" : "printer-settings-form border border-border bg-card p-5"}
      >
        {actionTarget &&
          createPortal(
            <>
              <Button
                type="button"
                variant="outline"
                disabled={saving}
                onClick={() => adopt(printer)}
              >
                {uiText("Cancel")}
              </Button>
              <Button
                type="button"
                loading={saving}
                disabled={!canEdit || command.blocked || !name.trim()}
                onClick={() => form.current?.requestSubmit()}
              >
                {uiText("Save changes")}
              </Button>
            </>,
            actionTarget,
          )}
        <div className={compact ? "space-y-5" : "printer-settings-fields"}>
          <div className={compact ? "space-y-4" : "contents"}>
            <SettingsField label={uiText("Name")}>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                disabled={!canEdit || saving}
              />
            </SettingsField>
            <SettingsField label={uiText("Integration")}>
              <Input value={providerLabel(printer)} readOnly />
            </SettingsField>
            <SettingsField
              label={uiText("Model")}
              hint={uiText("Pick a known model or type your own")}
            >
              {/* ponytail: native datalist — suggestions from the catalog, free text still allowed */}
              <Input
                list="printer-model-options"
                value={modelName}
                onChange={(e) => setModelName(e.target.value)}
                placeholder={printer.detected_model ?? uiText("Auto-detected")}
                disabled={!canEdit || saving}
              />
              <datalist id="printer-model-options">
                {PRINTER_MODEL_OPTIONS.map((model) => (
                  <option key={model} value={model} />
                ))}
              </datalist>
            </SettingsField>
            <SettingsField label={uiText("Group")} hint={uiText("Optional farm grouping")}>
              <Input
                value={group}
                onChange={(e) => setGroup(e.target.value)}
                placeholder={uiText("Workshop")}
                disabled={!canEdit || saving}
              />
            </SettingsField>
          </div>
          <div className={compact ? "space-y-4 border-t border-border pt-5" : "contents"}>
            {compact && (
              <div>
                <p className="text-sm font-semibold text-foreground">{uiText("Connection")}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {uiText("{value1} configuration", {
                    value1: String(providerLabel(printer) ?? ""),
                  })}
                </p>
              </div>
            )}
            <SettingsField
              label={
                printer.provider === "moonraker" ||
                printer.provider === "prusalink" ||
                printer.provider === "octoprint"
                  ? "URL"
                  : uiText("Host or IP")
              }
            >
              <Input
                value={address}
                onChange={(e) => setAddress(e.target.value)}
                required
                disabled={!canEdit || saving}
              />
            </SettingsField>
            {printer.provider === "bambu_lan" && (
              <SettingsField label={uiText("Printer serial")}>
                <Input
                  value={serial}
                  onChange={(e) => setSerial(e.target.value)}
                  required
                  disabled={!canEdit || saving}
                />
              </SettingsField>
            )}
            {printer.provider === "prusalink" && printer.prusalink_auth_mode !== "api_key" && (
              <SettingsField label={uiText("Username")}>
                <Input
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  required
                  disabled={!canEdit || saving}
                />
              </SettingsField>
            )}
            {printer.provider === "elegoo_centauri" &&
              printer.provider_variant === "elegoo_centauri_carbon" && (
                <SettingsField
                  label={uiText("Mainboard ID")}
                  hint={uiText(
                    "Needed for reliable printer commands while idle, paused, or errored.",
                  )}
                >
                  <Input
                    value={mainboardId}
                    onChange={(e) => setMainboardId(e.target.value)}
                    disabled={!canEdit || saving}
                  />
                </SettingsField>
              )}
            <SettingsField label={secretLabel} hint={uiText("Leave blank to keep current value")}>
              <Input
                type="password"
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
                placeholder={uiText("Unchanged")}
                disabled={!canEdit || saving}
              />
            </SettingsField>
            <SettingsField label={uiText("Notes")}>
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                rows={4}
                disabled={!canEdit || saving}
                className="w-full resize-y rounded-md border border-input bg-card px-3 py-2 text-sm text-foreground ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-disabled disabled:opacity-50"
              />
            </SettingsField>
            <label className="flex items-start gap-3 py-3">
              <input
                type="checkbox"
                checked={providerMaterialSync}
                onChange={(event) => setProviderMaterialSync(event.target.checked)}
                disabled={!canEdit || saving}
                className="mt-0.5 h-4 w-4"
              />
              <span>
                <span className="block text-sm font-medium text-foreground">
                  {uiText("Provider material sync")}
                </span>
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  {uiText(
                    "Import supported AMS or active-Spoolman state. Turn off to use manual state exclusively.",
                  )}
                </span>
              </span>
            </label>
            <label className="flex items-start gap-3 py-3">
              <input
                type="checkbox"
                checked={operatorReleaseRequired}
                onChange={(event) => setOperatorReleaseRequired(event.target.checked)}
                disabled={!canEdit || saving}
                className="mt-0.5 h-4 w-4"
              />
              <span>
                <span className="block text-sm font-medium text-foreground">
                  {uiText("Require operator release")}
                </span>
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  {uiText(
                    "After a managed print completes, block scheduling until an operator releases or holds the printer.",
                  )}
                </span>
              </span>
            </label>
            {!canEdit && (
              <p className="text-xs text-warning">{uiText("Sign in to edit printer settings.")}</p>
            )}
            {error && (
              <p role="alert" className="text-xs text-destructive">
                {error}
              </p>
            )}
          </div>
        </div>
        {
          <div
            className={
              compact
                ? "-mx-6 -mb-6 flex flex-wrap items-center justify-end gap-2 border-t border-border bg-plate px-6 py-4"
                : "mt-6 flex flex-wrap items-center justify-end gap-2"
            }
          >
            {onRemove && (
              <Button
                type="button"
                variant="ghost"
                className="mr-auto text-destructive"
                disabled={saving}
                onClick={onRemove}
              >
                {uiText("Remove printer")}
              </Button>
            )}
            <Button
              type="button"
              variant="outline"
              disabled={saving}
              onClick={onCancel ?? (() => adopt(printer))}
            >
              {uiText("Cancel")}
            </Button>
            <Button
              type="submit"
              loading={saving}
              disabled={!canEdit || command.blocked || !name.trim()}
            >
              {uiText("Save changes")}
            </Button>
          </div>
        }
        <PrinterSettingsReview
          canSave={Boolean(name.trim())}
          state={command.state}
          original={printer}
          onReview={() => {
            void command.review().catch((err) => {
              if (active.current) setError(userMessage(err));
            });
          }}
          onAdopt={() => adopt(command.adopt())}
          onSave={() => {
            void save(undefined, true);
          }}
        />
      </form>
    </Localized>
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
  useUiLocale();
  return (
    <label className="printer-settings-field block space-y-1.5">
      <span className="flex items-center justify-between gap-3 text-xs font-medium text-foreground">
        {label}
        {hint && <span className="font-normal text-muted-foreground">{hint}</span>}
      </span>
      {children}
    </label>
  );
}

function ConfigBlock({ title, data }: { title: string; data: ProviderJsonObject }) {
  useUiLocale();
  return (
    <section className="printer-reference-list">
      <div className="printer-reference-row">
        <div>
          <h3>{title}</h3>
          <p>{Object.keys(data).join(" · ") || uiText("Not recorded")}</p>
        </div>
        <div className="printer-reference-actions">
          <Button size="xs" variant="outline" onClick={() => void copyProviderJson(data)}>
            {uiText("Copy JSON")}
          </Button>
        </div>
      </div>
      {Object.entries(data).map(([name, value]) => (
        <div key={name} className="printer-reference-row">
          <div>
            <h3>{name}</h3>
            <p className="font-mono">
              {value instanceof Object ? JSON.stringify(value) : String(value ?? "—")}
            </p>
          </div>
        </div>
      ))}
    </section>
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
  useUiLocale();
  return (
    <div className="min-w-0 border-l border-border px-4 first:border-l-0 first:pl-0">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div
        className={`mt-2 text-[28px] font-semibold leading-tight text-foreground ${truncate ? "break-words" : ""}`}
        title={value}
      >
        {value}
      </div>
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
  useUiLocale();
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`flex min-h-9 items-center gap-1.5 px-3 py-2 text-[13px] transition-colors disabled:opacity-50 disabled:cursor-disabled ${
        destructive
          ? "border border-destructive/30 text-destructive hover:bg-destructive/10"
          : "border border-border text-foreground hover:bg-muted"
      }`}
    >
      {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Icon className="h-3.5 w-3.5" />}
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
  useUiLocale();
  return (
    <div className="flex items-end gap-2">
      <label className="flex-1 space-y-1">
        <span className="text-xs text-muted-foreground">{label}</span>
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
      <button onClick={onApply} disabled={disabled || value === ""} className={BTN_SECONDARY}>
        {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
        {uiText("Set")}
      </button>
    </div>
  );
}
