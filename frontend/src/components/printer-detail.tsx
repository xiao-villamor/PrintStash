"use client";

import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";

import { usePrinterSettingsCommand } from "@/features/printers/settings-edit";
import { PrinterSettingsReview } from "@/features/printers/settings-review";

import { knownUiText, uiMessage, uiText, type MessageDescriptor } from "@/lib/locale";
import { formatNumber } from "@/lib/format";
import { printerProfilesOptions } from "@/lib/queries/profiles";
import { useUiLocale } from "@/lib/i18n";

import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { Link } from "@/lib/link";
import { usePathname, useRouter, useSearchParams } from "@/lib/navigation";
import type { ProviderJsonObject, ProviderJsonValue } from "@/types/printers";
import {
  PrinterFileRead,
  PrinterRead,
  PrinterSnapshot,
  PrinterUpdate,
  PrinterDiagnostics,
} from "@/types";
import {
  cancelPrinter,
  emergencyStopPrinter,
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
import { formatDuration } from "@/lib/format";
import { Button, buttonVariants } from "@/components/ui/button";
import { PageFailure } from "@/components/page-failure";
import { PrinterConfigWorkspace } from "@/features/printers/config-workspace";
import { FormField } from "@/components/ui/form-field";
import { Input, inputClasses } from "@/components/ui/input";
import { TabBar } from "@/components/ui/tabs";
import { PageHeader } from "@/components/ui/page-header";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { PrinterServiceWorkspace } from "@/components/printer-service-workspace";
import { PrinterDiagnosticsPanel } from "@/components/printer-diagnostics";
import {
  PrinterFiles,
  PrinterJobHistory,
  PrinterRecentRuns,
} from "@/components/printer-file-history";
import {
  identifyPrintJob,
  identifyRunningPrint,
  printerArtifactInventory,
} from "@/features/printers/artifact-identity";
import { cn } from "@/lib/utils";
import { Localized } from "@/components/ui/localized";
import { HeaterChart, PartCoolingFan, PrinterProgress } from "@/components/printer-telemetry";
import {
  CommandReceipt,
  HeaterTarget,
  PrinterControls,
  ThermalPresets,
  type CommandReceipts,
} from "@/components/printer-controls";
import { PrinterConsole } from "./printer-console";
import { MachineIdentity, MachineMaterials } from "@/components/printer-machine";
import {
  PREHEAT_PRESETS,
  printerControlAvailability,
  type PrinterCommandPanel,
} from "@/features/printers/controls";
import { useMachineSetup } from "@/features/printers/materials";
import { usePrinterHeaterHistory } from "@/features/printers/inspection";
import { PrinterBedSurface } from "@/components/printer-bed-surface";
import {
  activePrintState,
  emptyHeaterHistory,
  finiteReading,
  observeHeaters,
} from "@/features/printers/telemetry";
import "./printer-detail-content.css";
import "./printer-detail.css";
import "./printer-sections.css";
import { PRINTER_MODEL_OPTIONS, providerAddress, providerLabel } from "@/lib/printer-providers";
import { Pause, Play, Square, RefreshCcw } from "lucide-react";

const FleetAddJobDialog = lazy(() =>
  import("./fleet-add-job").then((module) => ({ default: module.FleetAddJobDialog })),
);

const PRINTER_TABS = [
  "status",
  "files",
  "jobs",
  "materials",
  "maintenance",
  "config",
  "diagnostics",
  "settings",
] as const;
type PrinterTab = (typeof PRINTER_TABS)[number];

function allowedPrinterTab(tab: string | null, printer: PrinterRead | null): PrinterTab {
  const requested = PRINTER_TABS.find((candidate) => candidate === tab);
  switch (requested) {
    case "materials":
      return printer?.access.can_print || printer?.access.can_admin ? requested : "status";
    case "config":
      return printer?.provider === "moonraker" && printer.access.can_admin ? requested : "status";
    case "diagnostics":
    case "maintenance":
    case "settings":
      return printer?.access.can_admin ? requested : "status";
    default:
      return requested ?? "status";
  }
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
function mergeSnapshot(previous: PrinterSnapshot, patch: PrinterSnapshot): PrinterSnapshot {
  const previousState = previous.print_stats?.state;
  const startsNewPrint =
    previousState !== undefined &&
    !activePrintState(previousState) &&
    activePrintState(patch.print_stats?.state) !== null;
  // An idle frame may still name the preceding job and report its final layer.
  const base: PrinterSnapshot = startsNewPrint
    ? { ...previous, print_stats: undefined, virtual_sdcard: undefined }
    : previous;
  const out: PrinterSnapshot = { ...base };
  for (const key of Object.keys(patch)) out[key] = mergeSnapshotField(base[key], patch[key]);
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
  const [telemetry, setTelemetry] = useState<"waiting" | "live" | "offline">("waiting");
  const [heaterHistory, setHeaterHistory] = useState(emptyHeaterHistory);
  const displayHeaterHistory = usePrinterHeaterHistory(printer, heaterHistory);
  const telemetryConnection = useRef(0);
  const [addJobOpen, setAddJobOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [machineBusy, setMachineBusy] = useState<PrinterCommandPanel | null>(null);
  const [receipts, setReceipts] = useState<CommandReceipts>({});
  const commandPending = useRef<symbol | null>(null);
  const emergencyStopPending = useRef<symbol | null>(null);
  const [emergencyStopBusy, setEmergencyStopBusy] = useState(false);
  const searchParams = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();
  const activeTab = allowedPrinterTab(searchParams.get("tab"), printer);
  const [settingsVisited, setSettingsVisited] = useState(
    () => searchParams.get("tab") === "settings",
  );

  const reportsMaterials =
    printer?.provider === "bambu_lan" || Boolean(printer?.capabilities.can_report_material_state);
  const machineSetup = useMachineSetup(
    printerId,
    activeTab === "status" &&
      printer?.access.can_view === true &&
      (printer.provider === "moonraker" || reportsMaterials),
  );

  function tabUrl(tab: PrinterTab) {
    const next = new URLSearchParams(searchParams);
    if (tab === "status") next.delete("tab");
    else next.set("tab", tab);
    const query = next.toString();
    return query ? `${pathname}?${query}` : pathname;
  }

  function setActiveTab(tab: PrinterTab) {
    if (tab === activeTab) return;
    if (tab === "settings") setSettingsVisited(true);
    router.push(tabUrl(tab), { resetScroll: false });
  }

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

    function disconnect(state: "waiting" | "offline") {
      telemetryConnection.current += 1;
      setSnapshot({});
      setTelemetry(state);
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
          disconnect("waiting");
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
            if (msg.type === "offline") {
              disconnect("offline");
              return;
            }
            if (msg.type !== "snapshot" && msg.type !== "update") return;
            if (!isSnapshotSection(msg.data)) return;
            const data: PrinterSnapshot = msg.data;
            const connection = telemetryConnection.current;
            const at = Date.now();
            setTelemetry("live");
            setSnapshot((previous) =>
              msg.type === "snapshot" ? data : mergeSnapshot(previous, data),
            );
            setHeaterHistory((history) =>
              observeHeaters(history, data, at, connection, msg.type === "snapshot"),
            );
            if (data.print_stats?.state) void loadJobs();
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
      disconnect("waiting");
      setHeaterHistory(emptyHeaterHistory);
      setError(null);
      if (isLoggedIn()) void connect();
    });
    return () => {
      disposed = true;
      stopAuth();
      retire();
    };
  }, [accessDenied, printerId, loadJobs, refreshAll]);

  const printControl = {
    pause: () =>
      machineAction("job", () => pausePrinter(printerId), uiMessage("Pause command sent")),
    resume: () =>
      machineAction("job", () => resumePrinter(printerId), uiMessage("Resume command sent")),
    cancel: () =>
      machineAction("job", () => cancelPrinter(printerId), uiMessage("Cancel command sent")),
  };

  async function machineAction<T>(
    key: PrinterCommandPanel,
    fn: () => Promise<T>,
    success: MessageDescriptor,
    report?: (kind: "success" | "error", message: string) => void,
  ) {
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return;
    }
    if (commandPending.current || emergencyStopPending.current) return;
    const operation = Symbol(key);
    commandPending.current = operation;
    const version = lifetime.current;
    setMachineBusy(key);
    setReceipts((previous) => ({ ...previous, [key]: undefined }));
    try {
      await fn();
      if (version !== lifetime.current) return;
      const message = uiText(success.key, success.values);
      if (report) report("success", message);
      else setReceipts((previous) => ({ ...previous, [key]: { kind: "success", message } }));
    } catch (e) {
      if (version !== lifetime.current) return;
      const message = userMessage(e);
      if (report) report("error", message);
      else setReceipts((previous) => ({ ...previous, [key]: { kind: "error", message } }));
    } finally {
      if (commandPending.current === operation) {
        commandPending.current = null;
        setMachineBusy(null);
      }
    }
  }

  function preheat(p: (typeof PREHEAT_PRESETS)[number]) {
    void machineAction(
      "thermal-presets",
      async () => {
        const version = lifetime.current;
        await setPrinterTemperature(printerId, "extruder", p.hotend);
        if (version === lifetime.current) await setPrinterTemperature(printerId, "bed", p.bed);
      },
      uiMessage("{value1} preheat command sent", { value1: p.name }),
    );
  }

  function cooldown() {
    void machineAction(
      "thermal-presets",
      async () => {
        const version = lifetime.current;
        await setPrinterTemperature(printerId, "extruder", 0);
        if (version === lifetime.current) await setPrinterTemperature(printerId, "bed", 0);
      },
      uiMessage("Cooldown command sent"),
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
    if (emergencyStopPending.current) return;
    if (
      !auth.isAuthenticated ||
      telemetry !== "live" ||
      !printer?.access.can_control ||
      !printer.capabilities.can_send_gcode
    ) {
      setConfirmEmergencyStop(false);
      return;
    }
    // Emergency stop deliberately bypasses the lock for normal printer commands.
    const operation = Symbol("emergency stop");
    emergencyStopPending.current = operation;
    const version = lifetime.current;
    setEmergencyStopBusy(true);
    setReceipts((previous) => ({ ...previous, emergency: undefined }));
    try {
      await emergencyStopPrinter(printerId);
      if (version === lifetime.current) {
        const message = uiText("Emergency stop command sent");
        setReceipts((previous) => ({ ...previous, emergency: { kind: "success", message } }));
      }
    } catch (error) {
      if (version === lifetime.current) {
        const message = userMessage(error);
        setReceipts((previous) => ({ ...previous, emergency: { kind: "error", message } }));
      }
    } finally {
      if (emergencyStopPending.current === operation) {
        emergencyStopPending.current = null;
        setEmergencyStopBusy(false);
        setConfirmEmergencyStop(false);
      }
    }
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

  const ps = snapshot.print_stats;
  const elapsed = finiteReading(ps?.total_duration);
  const printTime = finiteReading(ps?.print_duration);
  const activeState = activePrintState(ps?.state);
  const artifactInventory = useMemo(() => printerArtifactInventory(printerFiles), [printerFiles]);
  const runningPrint = activeState
    ? identifyRunningPrint(printerId, ps?.filename, jobs, artifactInventory)
    : null;
  const progressFraction = finiteReading(snapshot.virtual_sdcard?.progress);
  const progress = progressFraction === null ? null : progressFraction * 100;
  const progressLabel =
    progress === null ? "—" : `${formatNumber(progress, { maximumFractionDigits: 1 })}%`;
  const currentLayer = finiteReading(ps?.external_current_layer);
  const totalLayers = finiteReading(ps?.external_total_layers);
  const lastJob = jobs.find((job) => job.printer_id === printerId && job.finished_at !== null);

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

  const bambu = printer.provider === "bambu_lan";
  const availability = printerControlAvailability(printer, snapshot, telemetry === "live");
  const commandsBusy = machineBusy !== null || emergencyStopBusy;
  const connectionLabel =
    telemetry === "live"
      ? uiText("Live")
      : telemetry === "offline" || printer.status === "offline"
        ? uiText("Offline")
        : uiText("Reconnecting…");
  const nextJob = jobs
    .filter((job) => job.printer_id === printerId && job.state === "queued")
    .sort((left, right) => left.queue_position - right.queue_position)[0];

  const currentWork = (
    <section aria-label={uiText("Current job")} className="printer-desk-section">
      <div className="printer-desk-heading">
        <h2>{uiText("Current job")}</h2>
        <span>
          {activeState
            ? knownUiText(activeState)
            : availability.readyIdle
              ? uiText("Ready")
              : connectionLabel}
        </span>
      </div>
      <div className="printer-current-work-row flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="min-w-0">
          <p className="break-words text-base font-semibold">
            {activeState
              ? (runningPrint?.title ?? uiText("Print name not reported"))
              : ps?.state
                ? uiText("No active print")
                : uiText("Waiting for printer status")}
          </p>
          {runningPrint && runningPrint.title !== runningPrint.filename && (
            <p className="break-all text-xs text-muted-foreground">{runningPrint.filename}</p>
          )}
          {!bambu && !activeState && lastJob && (
            <p className="break-words text-xs text-muted-foreground">
              {uiText("Last job")} · {identifyPrintJob(lastJob, artifactInventory).title} ·{" "}
              {knownUiText(lastJob.state)}
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {activeState ? (
            <>
              {activeState === "printing" && printer.capabilities.can_pause && (
                <ControlButton
                  onClick={printControl.pause}
                  disabled={!availability.connected || commandsBusy}
                  icon={Pause}
                  label={uiText("Pause")}
                />
              )}
              {activeState === "paused" && printer.capabilities.can_resume && (
                <ControlButton
                  onClick={printControl.resume}
                  disabled={!availability.connected || commandsBusy}
                  icon={Play}
                  label={uiText("Resume")}
                />
              )}
              {printer.capabilities.can_cancel && (
                <ControlButton
                  onClick={printControl.cancel}
                  disabled={!availability.connected || commandsBusy}
                  icon={Square}
                  label={uiText("Cancel")}
                  destructive
                />
              )}
            </>
          ) : (
            <>
              {printer.access.can_print &&
                printer.capabilities.can_upload &&
                printer.capabilities.can_start && (
                  <Button size="xs" onClick={() => setAddJobOpen(true)}>
                    {uiText("Add to queue")}
                  </Button>
                )}
              {printer.capabilities.can_list_files && (
                <Button variant="outline" size="xs" onClick={() => setActiveTab("files")}>
                  {uiText("Browse files")}
                </Button>
              )}
            </>
          )}
        </div>
      </div>
      <CommandReceipt receipt={receipts.job} pending={machineBusy === "job"} />
      {activeState && (
        <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
          <div className="min-w-32 space-y-1">
            <StatusMetric label={uiText("Progress")} value={progressLabel} />
            {progress !== null && <PrinterProgress value={progress} />}
          </div>
          {currentLayer !== null && (
            <StatusMetric
              label={uiText("Layer")}
              value={
                totalLayers !== null && totalLayers > 0
                  ? `${formatNumber(currentLayer)} / ${formatNumber(totalLayers)}`
                  : formatNumber(currentLayer)
              }
            />
          )}
          {ps?.remaining_minutes != null && (
            <StatusMetric
              label={uiText("Time remaining")}
              value={uiText("{value1} min", { value1: formatNumber(ps.remaining_minutes) })}
            />
          )}
          {elapsed !== null && (
            <StatusMetric label={uiText("Elapsed")} value={formatDuration(elapsed)} />
          )}
          {printTime !== null && (
            <StatusMetric
              label={uiText(
                printer.provider === "moonraker" || elapsed !== null ? "Print time" : "Elapsed",
              )}
              value={formatDuration(printTime)}
            />
          )}
        </div>
      )}
      <div className="printer-current-work-row flex flex-wrap items-center justify-between gap-2 border-t border-border pt-2">
        <p className="min-w-0 break-words text-xs text-muted-foreground">
          {nextJob
            ? uiText("Up next: {value1}", {
                value1: identifyPrintJob(nextJob, artifactInventory).title,
              })
            : uiText("No queued job for this printer")}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          {activeState && printer.capabilities.can_list_files && (
            <Button variant="outline" size="xs" onClick={() => setActiveTab("files")}>
              {uiText("Browse files")}
            </Button>
          )}
          {printer.access.can_print && (
            <Link
              href="/printers?tab=queue"
              className={buttonVariants({ variant: "outline", size: "xs" })}
            >
              {uiText("View queue")}
            </Link>
          )}
          {!bambu && (
            <Button size="xs" variant="outline" onClick={() => setActiveTab("jobs")}>
              {uiText("Print history")}
            </Button>
          )}
        </div>
      </div>
    </section>
  );

  const statusRail = (
    <PrinterStatusRail
      printer={printer}
      setup={machineSetup}
      chamber={snapshot["temperature_sensor chamber"]?.temperature}
      cardPresent={snapshot.bambu_storage?.card_present}
      reportsMaterials={reportsMaterials}
      connectionLabel={connectionLabel}
      live={telemetry === "live"}
      openMaterials={() => setActiveTab("materials")}
      openSettings={() => setActiveTab("settings")}
      inspection={
        printer.provider === "moonraker" ? (
          <>
            <PrinterBedSurface
              printerId={printer.id}
              control={{
                printer,
                snapshot,
                live: telemetry === "live",
                busy: commandsBusy,
                run: machineAction,
              }}
            />
            {availability.moonraker && printer.access.can_admin && (
              <PrinterConsole
                key={printer.id}
                printerId={printer.id}
                live={telemetry === "live"}
                busy={commandsBusy}
              />
            )}
          </>
        ) : undefined
      }
      fan={
        !availability.moonraker && !bambu ? (
          <PartCoolingFan speed={snapshot.fan?.speed} />
        ) : undefined
      }
    />
  );

  return (
    <Localized>
      <>
        {addJobOpen && printer.access.can_print && (
          <Suspense fallback={null}>
            <FleetAddJobDialog
              open
              onClose={() => setAddJobOpen(false)}
              printers={[printer]}
              fixedPrinterId={printerId}
            />
          </Suspense>
        )}
        <ConfirmModal
          open={confirmEmergencyStop}
          onClose={() => {
            if (!emergencyStopBusy) setConfirmEmergencyStop(false);
          }}
          onConfirm={confirmEmergencyStopAction}
          busy={emergencyStopBusy}
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
                {providerLabel(printer)} · {connectionLabel}
                {printer.capabilities.support_level === "beta" && (
                  <span className="ml-2 inline-flex border border-border bg-muted px-1.5 py-0.5 text-2xs font-medium text-foreground">
                    {uiText("Beta")}
                  </span>
                )}
              </>
            }
            actions={
              activeTab === "materials" ||
              activeTab === "maintenance" ||
              activeTab === "diagnostics" ||
              (activeTab === "files" && printer.capabilities.can_list_files) ? null : (
                <>
                  {activeTab === "status" &&
                    availability.moonraker &&
                    printer.access.can_control && (
                      <div className="flex flex-wrap items-center gap-2">
                        <CommandReceipt receipt={receipts.emergency} pending={emergencyStopBusy} />
                        <Button
                          size="sm"
                          variant="danger"
                          disabled={!availability.connected || emergencyStopBusy}
                          onClick={emergencyStop}
                        >
                          {uiText("Emergency stop")}
                        </Button>
                      </div>
                    )}
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
                </>
              )
            }
          />

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
                ...(printer.access.can_print || printer.access.can_admin
                  ? [
                      {
                        key: "materials" as const,
                        get label() {
                          return uiText("Materials & maintenance");
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
              active={activeTab === "maintenance" ? "materials" : activeTab}
              onChange={setActiveTab}
              className="gap-1 overflow-x-auto -mb-px"
              tabClassName="px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              activeTabClassName="bg-transparent font-semibold text-foreground hover:bg-transparent"
            />
          </div>

          {activeTab === "status" && (
            <div
              className="printer-workbench"
              data-layout={bambu ? "bambu" : availability.moonraker ? "klipper" : "overview"}
            >
              {bambu && statusRail}
              <div className="printer-workbench-column">
                {currentWork}
                <section className="printer-desk-section" aria-label={uiText("Temperatures")}>
                  <div className="printer-desk-heading">
                    <h2>{uiText("Temperatures")}</h2>
                    {bambu && <PartCoolingFan speed={snapshot.fan?.speed} variant="reading" />}
                  </div>
                  {telemetry !== "live" && printer.status === "offline" && (
                    <p className="text-xs text-muted-foreground">
                      {uiText(
                        "Printer is offline. Check its address and connection before sending commands.",
                      )}
                    </p>
                  )}
                  <HeaterChart
                    snapshot={snapshot}
                    history={displayHeaterHistory.history}
                    recorded={displayHeaterHistory.recorded}
                    live={telemetry === "live"}
                    renderTarget={
                      availability.moonraker && printer.access.can_control
                        ? (series) => {
                            const heater = series === "extruder" ? "extruder" : "bed";
                            const panel = heater === "extruder" ? "hotend" : "bed";
                            return (
                              <HeaterTarget
                                label={uiText(
                                  heater === "extruder" ? "Hotend target" : "Bed target",
                                )}
                                disabled={!availability.tuning}
                                busy={commandsBusy}
                                pending={machineBusy === panel}
                                receipt={receipts[panel]}
                                onApply={(target, clear) =>
                                  machineAction(
                                    panel,
                                    () => setPrinterTemperature(printerId, heater, target),
                                    uiMessage("Target command sent for {value1} °C", {
                                      value1: formatNumber(target),
                                    }),
                                    (kind, message) => {
                                      if (kind === "success") clear();
                                      setReceipts((previous) => ({
                                        ...previous,
                                        [panel]: { kind, message },
                                      }));
                                    },
                                  )
                                }
                              />
                            );
                          }
                        : undefined
                    }
                  />
                  {availability.moonraker && printer.access.can_control && (
                    <ThermalPresets
                      disabled={!availability.idle || commandsBusy}
                      activePrint={activeState !== null}
                      preheat={preheat}
                      cooldown={cooldown}
                      pending={machineBusy === "thermal-presets"}
                      receipt={receipts["thermal-presets"]}
                    />
                  )}
                </section>
                {bambu && (
                  <PrinterRecentRuns
                    printerId={printer.id}
                    jobs={jobs}
                    inventory={artifactInventory}
                    jobsHref={tabUrl("jobs")}
                  />
                )}
              </div>
              {availability.moonraker && (
                <PrinterControls
                  printer={printer}
                  snapshot={snapshot}
                  live={telemetry === "live"}
                  busy={commandsBusy}
                  pending={machineBusy}
                  receipts={receipts}
                  setup={machineSetup}
                  materialsHref={
                    printer.access.can_print || printer.access.can_admin
                      ? tabUrl("materials")
                      : undefined
                  }
                  run={machineAction}
                />
              )}
              {!bambu && statusRail}
            </div>
          )}

          {activeTab === "files" && (
            <PrinterFiles
              printer={printer}
              files={printerFiles}
              activeFilename={runningPrint?.filename}
              authBlockReason={auth.blockReason}
              startingId={startingFileId}
              deletingId={deletingFileId}
              syncing={syncingFiles}
              onSync={syncFiles}
              onStart={startRemoteFile}
              onDelete={deleteRemoteFile}
              onOpenJobs={() => setActiveTab("jobs")}
              notes={
                ((!bambu && printer.capabilities.support_notes.length > 0) ||
                  (printer.last_error && printer.status === "offline")) && (
                  <div className="printer-explorer-provider-notes">
                    {!bambu && <PrinterProviderNotes printer={printer} />}
                    {printer.last_error && printer.status === "offline" && (
                      <p className="text-sm text-destructive">
                        {uiText(
                          "Could not connect to the printer. Check its address and network connection, then retry.",
                        )}
                      </p>
                    )}
                  </div>
                )
              }
            />
          )}

          {(settingsVisited || activeTab === "settings") && printer.access.can_admin && (
            <div hidden={activeTab !== "settings"}>
              <PrinterSettings
                printer={printer}
                canEdit={printer.access.can_admin}
                key={printer.id}
                connectionLabel={connectionLabel}
                onDiagnostics={() => setActiveTab("diagnostics")}
                onMaintenance={() => setActiveTab("maintenance")}
              />
            </div>
          )}

          {(activeTab === "materials" || activeTab === "maintenance") && (
            <PrinterServiceWorkspace
              printer={printer}
              section={activeTab}
              onRoutingChanged={() => void resources.refresh(printerKeys.detail(printerId))}
            />
          )}

          {activeTab === "jobs" && (
            <PrinterJobHistory printer={printer} jobs={jobs} files={printerFiles} />
          )}

          {activeTab === "config" &&
            printer.provider === "moonraker" &&
            printer.access.can_admin && (
              <PrinterConfigWorkspace
                key={printerId}
                printerId={printerId}
                data={moonrakerConfig}
                error={resources.configError ? userMessage(resources.configError) : null}
                loading={loadingConfig}
                onRefresh={() => void loadMoonrakerConfig()}
                onCopy={(data) => void copyProviderJson(data)}
              />
            )}

          {activeTab === "diagnostics" && (
            <PrinterDiagnosticsPanel
              printer={printer}
              result={diagnostics}
              checkedAt={resources.diagnosticsCheckedAt}
              error={resources.diagnosticsError ? userMessage(resources.diagnosticsError) : null}
              running={checkingDiagnostics}
              onRun={() => void loadDiagnostics()}
              onCopy={diagnostics ? () => void copyProviderJson(diagnostics) : undefined}
            />
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
    toast.error(uiText("Could not copy JSON."));
  }
}

export function PrinterSettings({
  printer: initialPrinter,
  canEdit,
  modalActions,
  onCancel,
  onSaved,
  onRemove,
  connectionLabel,
  onDiagnostics,
  onMaintenance,
}: {
  printer: PrinterRead;
  canEdit: boolean;
  modalActions?: { formId: string; target: HTMLElement | null };
  onCancel?: () => void;
  onSaved?: () => void;
  onRemove?: () => void;
  connectionLabel?: string;
  onDiagnostics?: () => void;
  onMaintenance?: () => void;
}) {
  useUiLocale();
  const [printer, setPrinter] = useState(initialPrinter);
  const command = usePrinterSettingsCommand(printer.id);
  const active = useRef(true);
  const form = useRef<HTMLFormElement>(null);
  const hintId = useId();
  const compact = modalActions !== undefined;
  const Field = compact ? FormField : SettingsField;
  const setup = useMachineSetup(initialPrinter.id, !compact);
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
  const [costProfile, setCostProfile] = useState(printer.cost_profile_id ?? null);
  const profiles = useQuery({ ...printerProfilesOptions(), enabled: canEdit });
  const costProfiles = profiles.data?.filter((profile) => profile.expenses != null) ?? [];
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
  const dirty =
    name.trim() !== printer.name ||
    modelName !== (printer.model_name ?? "") ||
    group !== (printer.group ?? "") ||
    notes !== (printer.notes ?? "") ||
    costProfile !== (printer.cost_profile_id ?? null) ||
    address !== providerAddress(printer) ||
    serial !== (printer.bambu_serial ?? "") ||
    username !== (printer.prusalink_username ?? "") ||
    mainboardId !== (printer.elegoo_centauri_mainboard_id ?? "") ||
    secret !== "" ||
    providerMaterialSync !== printer.provider_material_sync_enabled ||
    operatorReleaseRequired !== printer.operator_release_required;
  const saving = command.busy;
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [discarded, setDiscarded] = useState(false);
  const saveLabel = saving
    ? "Saving…"
    : dirty
      ? "Unsaved changes"
      : discarded
        ? "Changes discarded"
        : saved
          ? "Saved"
          : "No unsaved changes";

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
    setCostProfile(snapshot.cost_profile_id ?? null);
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
    if (!canEdit || saving || (!revised && (command.blocked || (!compact && !dirty)))) return;
    if (form.current && !form.current.reportValidity()) return;
    const session = getSessionVersion();
    const current = () => active.current && session === getSessionVersion();
    setError(null);
    const payload: PrinterUpdate = {};
    if (name.trim() !== printer.name) payload.name = name.trim();
    if (modelName !== (printer.model_name ?? "")) payload.model_name = modelName;
    if (group !== (printer.group ?? "")) payload.group = group;
    if (notes !== (printer.notes ?? "")) payload.notes = notes;
    if (costProfile !== (printer.cost_profile_id ?? null)) payload.cost_profile_id = costProfile;
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
      setSaved(true);
      setDiscarded(false);
      toast.success(uiText("Printer settings saved"));
      onSaved?.();
    } catch (err) {
      if (!current()) return;
      const message = err instanceof Error ? err.message : "Could not save printer settings";
      setError(message);
      toast.error(err);
    }
  }

  const materialSyncField = (
    <label className="flex items-start gap-3">
      <input
        type="checkbox"
        checked={providerMaterialSync}
        onChange={(event) => setProviderMaterialSync(event.target.checked)}
        disabled={!canEdit || saving}
        className="mt-0.5 h-4 w-4"
      />
      <span>
        <span
          className={cn("block font-medium text-foreground", compact ? "text-field" : "text-sm")}
        >
          {uiText("Provider material sync")}
        </span>
        <span className={cn("block text-xs text-muted-foreground", compact ? "mt-1" : "mt-0.5")}>
          {uiText(
            "Import supported AMS or active-Spoolman state. Turn off to use manual state exclusively.",
          )}
        </span>
      </span>
    </label>
  );

  const notesField = (
    <Field label={uiText("Notes")}>
      <textarea
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        rows={compact ? undefined : 2}
        disabled={!canEdit || saving}
        className={
          compact
            ? inputClasses
            : "w-full resize-y rounded-md border border-input bg-card px-3 py-2 text-sm text-foreground ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-disabled disabled:opacity-50"
        }
      />
    </Field>
  );

  const actions = (
    <>
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
        disabled={saving || (!compact && (!dirty || command.blocked))}
        onClick={
          onCancel ??
          (() => {
            adopt(printer);
            setDiscarded(true);
          })
        }
      >
        {uiText(compact ? "Cancel" : "Discard changes")}
      </Button>
      <Button
        type="submit"
        form={modalActions?.formId}
        loading={saving}
        disabled={!canEdit || command.blocked || !name.trim() || (!compact && !dirty)}
      >
        {uiText("Save changes")}
      </Button>
    </>
  );

  return (
    <Localized>
      <div className={compact ? undefined : "printer-desk printer-settings-desk"}>
        {!compact && (
          <aside className="min-w-0">
            <MachineIdentity
              printer={initialPrinter}
              status={connectionLabel ?? knownUiText(initialPrinter.status)}
              nozzle={setup.nozzle}
            />
            <dl className="printer-machine-facts printer-settings-context">
              <div>
                <dt>{uiText("Scheduling")}</dt>
                <dd>
                  {uiText(
                    initialPrinter.drain_mode ? "Soft drain active" : "Accepting scheduled work",
                  )}
                </dd>
              </div>
              <div>
                <dt>{uiText("Operator release")}</dt>
                <dd>
                  {uiText(initialPrinter.operator_release_required ? "Required" : "Optional")}
                </dd>
              </div>
            </dl>
          </aside>
        )}
        <form
          id={modalActions?.formId}
          ref={form}
          onSubmit={save}
          className={compact ? "space-y-5" : "min-w-0 divide-y divide-border"}
        >
          {!compact && (
            <header className="printer-settings-actions">
              <h2 className="mr-auto text-sm font-semibold">{uiText("Printer settings")}</h2>
              <span role="status" className="text-xs text-muted-foreground">
                {uiText(saveLabel)}
              </span>
            </header>
          )}
          <PrinterSettingsSection compact={compact} title={uiText("Identity & routing")}>
            <Field label={uiText("Name")}>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                disabled={!canEdit || saving}
              />
            </Field>
            {compact && (
              <Field label={uiText("Integration")}>
                <Input value={providerLabel(printer)} readOnly />
              </Field>
            )}
            <Field
              label={uiText("Model")}
              hint={uiText("Pick a known model or type your own")}
              hintId={`${hintId}-model`}
            >
              {/* ponytail: native datalist — suggestions from the catalog, free text still allowed */}
              <Input
                list="printer-model-options"
                aria-describedby={compact ? `${hintId}-model` : undefined}
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
            </Field>
            <Field
              label={uiText("Group")}
              hint={uiText("Optional farm grouping")}
              hintId={`${hintId}-group`}
            >
              <Input
                value={group}
                aria-describedby={compact ? `${hintId}-group` : undefined}
                onChange={(e) => setGroup(e.target.value)}
                placeholder={uiText("Workshop")}
                disabled={!canEdit || saving}
              />
            </Field>
          </PrinterSettingsSection>
          <PrinterSettingsSection
            compact={compact}
            title={uiText("Connection")}
            description={providerLabel(printer)}
            action={
              !compact &&
              onDiagnostics && (
                <Button size="xs" variant="outline" type="button" onClick={onDiagnostics}>
                  {uiText("Connection checks")}
                </Button>
              )
            }
          >
            <Field
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
            </Field>
            {printer.provider === "bambu_lan" && (
              <Field label={uiText("Printer serial")}>
                <Input
                  value={serial}
                  onChange={(e) => setSerial(e.target.value)}
                  required
                  disabled={!canEdit || saving}
                />
              </Field>
            )}
            {printer.provider === "prusalink" && printer.prusalink_auth_mode !== "api_key" && (
              <Field label={uiText("Username")}>
                <Input
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  required
                  disabled={!canEdit || saving}
                />
              </Field>
            )}
            {printer.provider === "elegoo_centauri" &&
              printer.provider_variant === "elegoo_centauri_carbon" && (
                <Field
                  label={uiText("Mainboard ID")}
                  hintId={`${hintId}-mainboard`}
                  hint={uiText(
                    "Needed for reliable printer commands while idle, paused, or errored.",
                  )}
                >
                  <Input
                    value={mainboardId}
                    aria-describedby={compact ? `${hintId}-mainboard` : undefined}
                    onChange={(e) => setMainboardId(e.target.value)}
                    disabled={!canEdit || saving}
                  />
                </Field>
              )}
            <Field
              label={secretLabel}
              hint={uiText("Leave blank to keep current value")}
              hintId={`${hintId}-secret`}
            >
              <Input
                type="password"
                autoComplete="new-password"
                value={secret}
                aria-describedby={compact ? `${hintId}-secret` : undefined}
                onChange={(e) => setSecret(e.target.value)}
                placeholder={uiText("Unchanged")}
                disabled={!canEdit || saving}
              />
            </Field>
            {!compact &&
              (printer.provider === "moonraker" ||
                printer.provider === "bambu_lan" ||
                printer.capabilities.can_report_material_state) &&
              materialSyncField}
          </PrinterSettingsSection>
          {compact && notesField}
          {canEdit && (
            <PrinterSettingsSection compact={compact} title={uiText("Print costing")}>
              <Field label={uiText("Cost profile")}>
                <select
                  className={inputClasses}
                  value={costProfile ?? ""}
                  disabled={saving || profiles.isPending || profiles.isError}
                  onChange={(event) =>
                    setCostProfile(event.target.value ? Number(event.target.value) : null)
                  }
                >
                  <option value="">{uiText("No cost profile")}</option>
                  {costProfile != null &&
                    !costProfiles.some((profile) => profile.id === costProfile) && (
                      <option value={costProfile}>
                        {profiles.isSuccess
                          ? uiText("Profile without expenses")
                          : uiText("Checking…")}
                      </option>
                    )}
                  {costProfiles.map((profile) => (
                    <option key={profile.id} value={profile.id}>
                      {profile.name}
                    </option>
                  ))}
                </select>
              </Field>
              {profiles.isError && (
                <p role="alert" className="text-xs text-muted-foreground">
                  {uiText("Could not load printer profiles.")}
                </p>
              )}
              {profiles.isSuccess && costProfiles.length === 0 && (
                <p className="text-xs text-muted-foreground">
                  {uiText("Add expenses to a printer profile to use it here.")}
                </p>
              )}
            </PrinterSettingsSection>
          )}
          <PrinterSettingsSection
            compact={compact}
            title={uiText("Scheduling & release")}
            action={
              !compact &&
              onMaintenance && (
                <Button size="xs" variant="outline" type="button" onClick={onMaintenance}>
                  {uiText("Manage maintenance")}
                </Button>
              )
            }
          >
            {compact && materialSyncField}
            <label className="flex items-start gap-3">
              <input
                type="checkbox"
                checked={operatorReleaseRequired}
                onChange={(event) => setOperatorReleaseRequired(event.target.checked)}
                disabled={!canEdit || saving}
                className="mt-0.5 h-4 w-4"
              />
              <span>
                <span
                  className={cn(
                    "block font-medium text-foreground",
                    compact ? "text-field" : "text-sm",
                  )}
                >
                  {uiText("Require operator release")}
                </span>
                <span
                  className={cn("block text-xs text-muted-foreground", compact ? "mt-1" : "mt-0.5")}
                >
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
          </PrinterSettingsSection>
          {!compact && (
            <PrinterSettingsSection compact={false} title={uiText("Operator notes")}>
              {notesField}
            </PrinterSettingsSection>
          )}
          {modalActions ? (
            modalActions.target && createPortal(actions, modalActions.target)
          ) : (
            <div className="printer-settings-actions">
              <div className="ml-auto flex flex-wrap gap-2">{actions}</div>
            </div>
          )}
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
      </div>
    </Localized>
  );
}

function PrinterSettingsSection({
  compact,
  title,
  description,
  action,
  children,
}: {
  compact: boolean;
  title: string;
  description?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className={compact ? "space-y-4" : "printer-desk-section"}>
      {(!compact || description) && (
        <div className={compact ? "space-y-1" : "printer-desk-heading"}>
          <h2 className="text-sm font-semibold">{title}</h2>
          {action ||
            (description && <span className="text-xs text-muted-foreground">{description}</span>)}
        </div>
      )}
      <div className={compact ? "space-y-4" : "printer-settings-fields"}>{children}</div>
    </section>
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

function PrinterProviderNotes({ printer }: { printer: PrinterRead }) {
  if (!printer.capabilities.support_notes.length) return null;
  return (
    <section
      aria-label={uiText("Provider support")}
      className="space-y-1 border-l-2 border-border pl-3 text-xs text-muted-foreground"
    >
      {printer.capabilities.support_notes.map((note) => (
        <p key={note}>{knownUiText(note)}</p>
      ))}
    </section>
  );
}

function PrinterStatusRail({
  printer,
  chamber,
  cardPresent,
  setup,
  reportsMaterials,
  connectionLabel,
  live,
  openMaterials,
  openSettings,
  inspection,
  fan,
}: {
  printer: PrinterRead;
  chamber?: number;
  cardPresent?: boolean;
  setup: ReturnType<typeof useMachineSetup>;
  reportsMaterials: boolean;
  connectionLabel: string;
  live: boolean;
  openMaterials: () => void;
  openSettings: () => void;
  inspection: React.ReactNode;
  fan: React.ReactNode;
}) {
  return (
    <aside className="printer-workbench-column">
      <MachineIdentity
        printer={printer}
        status={connectionLabel}
        nozzle={setup.nozzle}
        summary={{
          chamber,
          cardPresent,
          live,
          openSettings: printer.access.can_admin ? openSettings : undefined,
        }}
      />
      {inspection}
      {printer.provider !== "moonraker" && (reportsMaterials || printer.access.can_print) && (
        <MachineMaterials
          setup={setup}
          reportsMaterials={reportsMaterials}
          openMaterials={
            printer.access.can_print || printer.access.can_admin ? openMaterials : undefined
          }
        />
      )}
      {fan && <div className="printer-desk-section">{fan}</div>}
    </aside>
  );
}

function StatusMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="break-words font-mono text-sm font-semibold tabular-nums">{value}</div>
    </div>
  );
}

function ControlButton({
  onClick,
  disabled,
  icon: Icon,
  label,
  destructive,
}: {
  onClick: () => void;
  disabled?: boolean;
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  destructive?: boolean;
}) {
  useUiLocale();
  return (
    <Button
      size="sm"
      variant={destructive ? "danger" : "default"}
      onClick={onClick}
      disabled={disabled}
    >
      <Icon className="size-3.5" aria-hidden="true" />
      {label}
    </Button>
  );
}
