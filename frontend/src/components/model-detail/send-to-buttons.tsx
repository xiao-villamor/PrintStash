"use client";

import { useQueryClient } from "@tanstack/react-query";
import { refreshFleet } from "@/features/printers/queries";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";

import { knownUiText } from "@/lib/locale";
import { uiMessage } from "@/lib/locale";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "@/lib/link";
import { Loader2, Printer as PrinterIcon, Send, WifiOff } from "lucide-react";

import {
  checkFleetCompatibility,
  createFleetBatch,
  enqueueFleetJob,
  sendToPrinter,
} from "@/lib/api";
import { usePrinters, useSpoolmanStatus, useSpools } from "@/lib/queries";
import { formatGrams } from "@/lib/format";
import { createTask, updateTask } from "@/lib/task-center";
import { toast } from "@/lib/toast";
import { useRequireAuth } from "@/lib/use-require-auth";
import { useAuth } from "@/lib/auth-context";
import {
  CompatibilityRead,
  FileRead,
  JobPriority,
  ModelPrinterFileRead,
  RoutingStrategy,
} from "@/types";
import { ChoiceField } from "./choice-field";
import { providerLabel } from "@/lib/printer-providers";
import { Button, buttonVariants } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Modal } from "@/components/ui/modal";
import { Localized } from "@/components/ui/localized";
import { ConfirmModal } from "@/components/ui/confirm-modal";

const selectClassName =
  "h-10 w-full rounded-md border border-input bg-background px-3 text-sm text-foreground ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-disabled disabled:opacity-50";

function printArtifactFormat(filename: string): "gcode_text" | "bgcode_binary" {
  return filename.toLowerCase().endsWith(".bgcode") ? "bgcode_binary" : "gcode_text";
}

/**
 * The four fleet commands this panel issues. Named as a seam so a test can
 * drive the panel and observe the submitted payload without a network; app
 * code never passes it and gets the real API client.
 */
export interface SendToCommands {
  checkFleetCompatibility: typeof checkFleetCompatibility;
  createFleetBatch: typeof createFleetBatch;
  enqueueFleetJob: typeof enqueueFleetJob;
  sendToPrinter: typeof sendToPrinter;
}

const API_COMMANDS: SendToCommands = {
  checkFleetCompatibility,
  createFleetBatch,
  enqueueFleetJob,
  sendToPrinter,
};

export function SendToButtons({
  modelName,
  gcodeFiles,
  printerFiles,
  open,
  onOpenChange,
  preselectFileId,
  preselectPrinterId,
  commands = API_COMMANDS,
  compact = false,
  initialDeliveryMode = "send",
}: {
  modelName?: string;
  gcodeFiles: Pick<
    FileRead,
    | "id"
    | "original_filename"
    | "version"
    | "gcode_revision_number"
    | "revision_label"
    | "is_recommended"
    | "metadata"
  >[];
  printerFiles: ModelPrinterFileRead[];
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  preselectFileId?: number;
  preselectPrinterId?: number;
  commands?: SendToCommands;
  compact?: boolean;
  initialDeliveryMode?: "send" | "queue";
}) {
  useUiLocale();
  const commandClient = useQueryClient();
  const auth = useRequireAuth();
  const { user } = useAuth();
  const [internalOpen, setInternalOpen] = useState(false);
  const showSend = open ?? internalOpen;
  const setShowSend = onOpenChange ?? setInternalOpen;
  const defaultFile = gcodeFiles.find((f) => f.is_recommended) ?? gcodeFiles[gcodeFiles.length - 1];
  const [selectedFile, setSelectedFile] = useState<number>(defaultFile?.id ?? 0);
  const selectedFileDetails = gcodeFiles.find((file) => file.id === selectedFile);
  const selectedFileFormat = printArtifactFormat(selectedFileDetails?.original_filename ?? "");

  useEffect(() => {
    if (showSend && preselectFileId) {
      setSelectedFile(preselectFileId);
      return;
    }
    // The selected revision may have been deleted while the panel was open
    // (or closed and reopened after a revision was trashed elsewhere) — fall
    // back to the current default instead of sending a stale/removed file id.
    if (!gcodeFiles.some((f) => f.id === selectedFile)) {
      setSelectedFile(defaultFile?.id ?? 0);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showSend, preselectFileId, gcodeFiles]);
  const [startPrint, setStartPrint] = useState(false);
  const [deliveryMode, setDeliveryMode] = useState<"send" | "queue">(initialDeliveryMode);
  useEffect(() => {
    if (showSend) setDeliveryMode(initialDeliveryMode);
  }, [showSend, initialDeliveryMode]);
  const [routingStrategy, setRoutingStrategy] = useState<RoutingStrategy>("least_busy");
  const [quantity, setQuantity] = useState(1);
  const [priority, setPriority] = useState<JobPriority>("normal");
  const [targetGroup, setTargetGroup] = useState("");
  const [compatibility, setCompatibility] = useState<CompatibilityRead | null>(null);
  const [confirmMismatch, setConfirmMismatch] = useState(false);
  useEffect(() => {
    if (user && !user.is_superuser) setRoutingStrategy("manual");
  }, [user]);
  // Spoolman inventory — only surfaced when the integration is enabled.
  const spoolmanEnabled = useSpoolmanStatus().data?.enabled ?? false;
  const spools = useSpools({ enabled: spoolmanEnabled }).data ?? [];
  const [selectedSpoolId, setSelectedSpoolId] = useState<number | "">("");
  const printersQuery = usePrinters();
  // Stable ref so the default-select effect / memos below don't rerun each render.
  const printers = useMemo(() => printersQuery.data ?? [], [printersQuery.data]);
  const printersLoading = printersQuery.isLoading;
  const [selectedPrinterIds, setSelectedPrinterIds] = useState<number[]>([]);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Send failures live in local `error`; surface a printers load failure too.
  const displayError =
    error ?? (printersQuery.error instanceof Error ? printersQuery.error.message : null);

  // Default-select a capable printer once printers load (not gated on the panel
  // being open) so the collapsed "x/y online" indicator reflects a selection.
  useEffect(() => {
    setSelectedPrinterIds((current) => {
      const capableIds = printers
        .filter(
          (printer) =>
            printer.access.can_print &&
            printer.capabilities.can_upload &&
            printer.capabilities.accepted_print_formats.includes(selectedFileFormat),
        )
        .map((printer) => printer.id);
      if (capableIds.length === 0) return [];
      const kept = current.filter((id) => capableIds.includes(id));
      return kept.length > 0
        ? kept
        : showSend && preselectPrinterId !== undefined
          ? []
          : [capableIds[0]];
    });
  }, [printers, selectedFileFormat, showSend, preselectPrinterId]);

  const selectedPrinters = useMemo(
    () => printers.filter((printer) => selectedPrinterIds.includes(printer.id)),
    [printers, selectedPrinterIds],
  );
  const availablePrinters = useMemo(
    () =>
      printers.filter(
        (printer) =>
          printer.access.can_print &&
          printer.capabilities.can_upload &&
          printer.capabilities.accepted_print_formats.includes(selectedFileFormat),
      ),
    [printers, selectedFileFormat],
  );
  // Capture an inspector’s explicit target once per opening. Later user choices
  // remain theirs; a denied/format-incompatible requested target stays unselected.
  const appliedPrinterIntent = useRef<number | null>(null);
  useEffect(() => {
    if (!showSend) {
      appliedPrinterIntent.current = null;
      return;
    }
    if (
      preselectPrinterId === undefined ||
      printersLoading ||
      appliedPrinterIntent.current === preselectPrinterId
    )
      return;
    appliedPrinterIntent.current = preselectPrinterId;
    setSelectedPrinterIds(
      availablePrinters.some((printer) => printer.id === preselectPrinterId)
        ? [preselectPrinterId]
        : [],
    );
  }, [showSend, preselectPrinterId, printersLoading, availablePrinters]);
  const selectedPrintersCanStart = selectedPrinters.every(
    (printer) => printer.capabilities.can_start,
  );
  const onlineCount = selectedPrinters.filter(
    (printer) => printer.status !== "offline" && printer.status !== "unknown",
  ).length;
  const selectedUploads = printerFiles.filter(
    (row) =>
      row.file_id === selectedFile &&
      selectedPrinterIds.includes(row.printer_id) &&
      !row.missing_since,
  );

  function togglePrinter(id: number) {
    if (deliveryMode === "queue") {
      setSelectedPrinterIds([id]);
      return;
    }
    setSelectedPrinterIds((current) =>
      current.includes(id) ? current.filter((currentId) => currentId !== id) : [...current, id],
    );
  }

  async function send(allowMismatch = false) {
    const session = getSessionVersion();
    if (!selectedFile) return;
    const targetPrinterIds =
      deliveryMode === "send" || routingStrategy === "manual"
        ? selectedPrinters.map((printer) => printer.id)
        : [];
    if (!allowMismatch && targetPrinterIds.length > 0) {
      try {
        const report = await commands.checkFleetCompatibility(selectedFile, targetPrinterIds);
        requireSessionVersion(session);
        setCompatibility(report);
        if (
          report.printers.some((row) => row.verdict === "mismatch") &&
          (deliveryMode === "queue" || startPrint)
        ) {
          setConfirmMismatch(true);
          return;
        }
      } catch (e: any) {
        if (session !== getSessionVersion()) return;
        setError(e.message || uiText("Compatibility check failed"));
        return;
      }
    }
    if (deliveryMode === "queue") {
      if (routingStrategy === "manual" && selectedPrinters.length === 0) return;
      const spool =
        selectedSpoolId !== ""
          ? spools.find((candidate) => candidate.id === selectedSpoolId)
          : undefined;
      setSending(true);
      setError(null);
      try {
        const payload = {
          file_id: selectedFile,
          strategy: routingStrategy,
          printer_id: routingStrategy === "manual" ? selectedPrinters[0]?.id : undefined,
          spool_id: selectedSpoolId === "" ? null : selectedSpoolId,
          spool_name: spool
            ? spool.filament_name ||
              spool.name ||
              uiText("Spool {value1}", { value1: String(spool.id) })
            : null,
          spool_filament_id: spool?.filament_id ?? null,
          priority,
          target_group: targetGroup.trim() || null,
          compatibility_policy: allowMismatch ? ("allow_mismatch" as const) : ("safe" as const),
        };
        if (quantity > 1) {
          const batch = { ...payload, quantity };
          // An auto-routed batch spreads copies over printers the user never
          // picked, so a spool chosen for one of them cannot travel with it.
          if (routingStrategy !== "manual") {
            batch.spool_id = null;
            batch.spool_name = null;
            batch.spool_filament_id = null;
          }
          await commands.createFleetBatch(batch);
        } else await commands.enqueueFleetJob(payload);
        requireSessionVersion(session);
        refreshFleet(commandClient, session);
        setShowSend(false);
        toast.success(
          quantity > 1
            ? uiText("Created {value1}-copy batch", { value1: String(quantity) })
            : uiText("Added to fleet queue"),
        );
      } catch (e: any) {
        if (session !== getSessionVersion()) return;
        setError(e.message || uiText("Queue failed"));
      } finally {
        if (session === getSessionVersion()) setSending(false);
      }
      return;
    }
    if (selectedPrinters.length === 0) return;
    if (startPrint && !selectedPrintersCanStart) {
      setError(uiText("One or more selected printers support upload only."));
      return;
    }
    const file = gcodeFiles.find((candidate) => candidate.id === selectedFile);
    const taskId = createTask({
      title: uiMessage("Send {value1}", { value1: String(file?.original_filename ?? "G-code") }),
      detail: uiMessage("printers.sending", {
        value1: String(selectedPrinters.length),
        count: Number(selectedPrinters.length),
      }),
      status: "running",
      progress: 5,
    });
    setSending(true);
    setError(null);
    try {
      let completed = 0;
      const results = await Promise.allSettled(
        selectedPrinters.map(async (printer) => {
          const spool =
            selectedSpoolId !== "" ? spools.find((s) => s.id === selectedSpoolId) : undefined;
          const job = await commands.sendToPrinter(printer.id, {
            file_id: selectedFile,
            start_print: startPrint,
            spool_id: selectedSpoolId === "" ? null : selectedSpoolId,
            spool_name: spool
              ? spool.filament_name ||
                spool.name ||
                uiText("Spool {value1}", { value1: String(spool.id) })
              : null,
            spool_filament_id: spool ? spool.filament_id : null,
            compatibility_policy: allowMismatch ? "allow_mismatch" : "safe",
          });
          requireSessionVersion(session);
          refreshFleet(commandClient, session);
          completed += 1;
          updateTask(taskId, {
            detail: uiMessage("{value1}/{value2} printers completed", {
              value1: String(completed),
              value2: String(selectedPrinters.length),
            }),
            status: "running",
            progress: 10 + (completed / selectedPrinters.length) * 85,
          });
          return { printer, job };
        }),
      );

      requireSessionVersion(session);
      const successes = results.filter((result) => result.status === "fulfilled");
      const failures = results
        .map((result, index) => ({ result, printer: selectedPrinters[index] }))
        .filter(
          (
            entry,
          ): entry is {
            result: PromiseRejectedResult;
            printer: (typeof selectedPrinters)[number];
          } => entry.result.status === "rejected",
        );

      if (failures.length > 0) {
        const reasons = failures
          .map(
            ({ printer, result }) =>
              `${printer.name}: ${result.reason?.message ?? "unknown error"}`,
          )
          .join("; ");
        const message = uiText("{value1}/{value2} printers succeeded — {value3}", {
          value1: String(successes.length),
          value2: String(selectedPrinters.length),
          value3: String(reasons),
        });
        setError(message);
        updateTask(taskId, {
          detail: message,
          status: successes.length > 0 ? "completed" : "failed",
          progress: 100,
        });
        toast.warning(uiText("Some sends failed"), reasons);
      } else {
        updateTask(taskId, {
          detail: startPrint
            ? uiMessage("Print started on selected printers")
            : uiMessage("Sent to selected printers"),
          status: "completed",
          progress: 100,
        });
        setShowSend(false);
        toast.success(
          startPrint
            ? uiText("printers.started", {
                value1: String(successes.length),
                count: Number(successes.length),
              })
            : uiText("printers.sent", {
                value1: String(successes.length),
                count: Number(successes.length),
              }),
        );
      }
    } catch (e: any) {
      if (session !== getSessionVersion()) return;
      const message = e.message || "Send failed";
      setError(message);
      updateTask(taskId, {
        detail: message,
        status: "failed",
        progress: 100,
      });
    } finally {
      if (session === getSessionVersion()) setSending(false);
    }
  }

  const selectedSpool =
    selectedSpoolId !== "" ? spools.find((spool) => spool.id === selectedSpoolId) : undefined;
  // ponytail: single-file check only — a multi-plate build evaluated as one set
  // needs a batch-send feature that doesn't exist yet (#64 follow-up).
  const requiredWeightG = selectedFileDetails?.metadata?.filament_weight_g ?? null;
  const spoolCoverageWarning = selectedSpool
    ? requiredWeightG == null
      ? uiText(
          "This revision's filament weight is unknown — can't verify it fits the selected spool.",
        )
      : selectedSpool.remaining_weight == null
        ? uiText(
            "The selected spool has no tracked remaining weight — can't verify it covers this print.",
          )
        : requiredWeightG > selectedSpool.remaining_weight
          ? uiText("This revision needs ~{value1}; the selected spool has ~{value2} left.", {
              value1: String(formatGrams(requiredWeightG)),
              value2: String(formatGrams(selectedSpool.remaining_weight)),
            })
          : null
    : null;

  return (
    <Localized>
      <>
        <ConfirmModal
          open={confirmMismatch}
          onClose={() => setConfirmMismatch(false)}
          onConfirm={() => {
            setConfirmMismatch(false);
            void send(true);
          }}
          title={uiText("Print with a known material mismatch?")}
          description={uiText(
            "The selected G-code material or nozzle does not match the printer’s known loaded state. Continuing records an audited override. Color differences alone do not block printing.",
          )}
          confirmLabel={uiText("Print anyway")}
        />
        <div className={compact ? "detail-printer-status" : "space-y-3"}>
          {compact ? (
            <div className="detail-printer-summary">
              <strong>{uiText("Printer status")}</strong>
              {printersLoading ? (
                <span>{uiText("Checking…")}</span>
              ) : printers.length === 0 ? (
                <span>{uiText("No printers")}</span>
              ) : (
                printers.map((printer) => (
                  <span key={printer.id} data-status={printer.status}>
                    <i aria-hidden="true" />
                    {printer.name} <span>{knownUiText(printer.status)}</span>
                  </span>
                ))
              )}
            </div>
          ) : (
            <div className="flex items-center justify-between gap-3">
              <span className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
                {uiText("Printer status")}
              </span>
              <div className="flex items-center gap-1.5 rounded-md border border-border bg-background px-2 py-1">
                {printersLoading ? (
                  <>
                    <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
                    <span className="font-mono text-xs text-muted-foreground">
                      {uiText("Checking…")}
                    </span>
                  </>
                ) : printers.length === 0 ? (
                  <>
                    <WifiOff className="h-3 w-3 text-muted-foreground" />
                    <span className="font-mono text-xs text-muted-foreground">
                      {uiText("No printers")}
                    </span>
                  </>
                ) : selectedPrinters.length > 0 && onlineCount > 0 ? (
                  <>
                    <span className="h-2 w-2 rounded-full bg-success" />
                    <span className="font-mono text-xs font-bold tracking-wider text-success">
                      {uiText("{value1}/{value2} online", {
                        value1: String(onlineCount ?? ""),
                        value2: String(selectedPrinters.length ?? ""),
                      })}
                    </span>
                  </>
                ) : (
                  <>
                    <WifiOff className="h-3 w-3 text-warning" />
                    <span className="font-mono text-xs capitalize text-warning">
                      {uiText("No selected printer online")}
                    </span>
                  </>
                )}
              </div>
            </div>
          )}
          {displayError && !showSend && (
            <div className="rounded border border-error/30 bg-error-container/20 p-2 text-2xs text-error font-mono break-words">
              {displayError}
            </div>
          )}

          {compact ? (
            <Button asChild variant="ghost" size="sm">
              <Link href="/printers">{uiText("Manage printers")}</Link>
            </Button>
          ) : printers.length === 0 ? (
            <div className="space-y-2 rounded border border-outline-variant bg-surface-container-lowest p-3">
              <div className="flex items-center gap-2">
                <WifiOff className="h-4 w-4 text-on-surface-variant" />
                <span className="font-mono text-xs uppercase tracking-wider text-on-surface">
                  {uiText("No printers configured")}
                </span>
              </div>
              <p className="font-mono text-2xs text-on-surface-variant leading-relaxed">
                {uiText("Connect a supported printer to send files directly from the Vault.")}
              </p>
              <Button asChild size="sm" className="mt-1 w-full">
                <Link href="/printers">
                  <PrinterIcon className="h-4 w-4" />
                  {uiText(" Configure printer")}
                </Link>
              </Button>
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              <Button
                onClick={() => {
                  if (!auth.isAuthenticated) {
                    auth.showAuthRequiredToast();
                    return;
                  }
                  setShowSend(true);
                }}
                disabled={!auth.isAuthenticated}
                className="w-full"
              >
                {!auth.isAuthenticated ? (
                  <>
                    <Send className="h-4 w-4" />
                    {uiText(" Sign in to send")}
                  </>
                ) : (
                  <>
                    <Send className="h-4 w-4" />
                    {uiText(" Send to printer")}
                  </>
                )}
              </Button>
              <Button asChild variant="outline" size="sm" className="w-full">
                <Link href="/printers">{uiText("Manage printers")}</Link>
              </Button>
            </div>
          )}
        </div>

        <Modal
          open={showSend}
          onClose={() => {
            if (!sending) setShowSend(false);
          }}
          title={
            selectedFileDetails
              ? uiText("Send Rev {value1} to printer", {
                  value1: String(
                    selectedFileDetails.gcode_revision_number ?? selectedFileDetails.version,
                  ),
                })
              : uiText("Send to printer")
          }
          description={uiText(
            "Upload the selected G-code Revision. Starting a print requires your explicit choice.",
          )}
          footer={
            <>
              <Button variant="outline" onClick={() => setShowSend(false)} disabled={sending}>
                {uiText("Cancel")}
              </Button>
              <Button
                onClick={() => void send()}
                loading={sending}
                disabled={
                  !selectedFile ||
                  (deliveryMode === "send"
                    ? selectedPrinters.length === 0 || (startPrint && !selectedPrintersCanStart)
                    : quantity < 1 ||
                      (routingStrategy === "manual" && selectedPrinters.length === 0))
                }
              >
                {!sending && <Send className="h-4 w-4" />}
                {sending
                  ? deliveryMode === "queue"
                    ? uiText("Queuing…")
                    : uiText("Sending…")
                  : deliveryMode === "queue"
                    ? uiText("Add to queue")
                    : startPrint
                      ? uiText("Send & start print")
                      : uiText("Send to printer")}
              </Button>
            </>
          }
          className="flex max-h-[calc(100dvh-2rem)] w-full max-w-7xl flex-col overflow-hidden"
        >
          <div
            data-testid="send-dialog-scroll-region"
            className="min-h-0 flex-1 space-y-5 overflow-y-auto px-1"
          >
            <div className="detail-delivery-context">
              <span className="text-muted-foreground">
                {modelName ? uiText("Model") : uiText("G-code revision")}
              </span>
              <strong className="min-w-0 break-all font-medium">
                {modelName ??
                  selectedFileDetails?.original_filename ??
                  uiText("Select G-code revision")}
              </strong>
              <div className="detail-delivery-mode">
                <ChoiceField<"send" | "queue">
                  label={uiText("Action")}
                  value={deliveryMode}
                  inline
                  disabled={sending}
                  options={[
                    { value: "send", label: uiText("Send now") },
                    { value: "queue", label: uiText("Add to queue") },
                  ]}
                  onChange={(mode) => {
                    setDeliveryMode(mode);
                    if (mode === "queue") {
                      setStartPrint(false);
                      if (!user?.is_superuser) setRoutingStrategy("manual");
                    }
                  }}
                />
              </div>
            </div>
            <div className="detail-delivery-layout">
              <div className="min-w-0 space-y-5">
                {deliveryMode === "queue" && (
                  <div className="grid gap-4 sm:grid-cols-2">
                    <ChoiceField<RoutingStrategy>
                      label={uiText("Routing")}
                      value={routingStrategy}
                      options={[
                        ...(user?.is_superuser
                          ? [
                              {
                                value: "least_busy" as const,
                                label: uiText("Least busy eligible printer"),
                              },
                              { value: "default" as const, label: uiText("Default printer") },
                            ]
                          : []),
                        { value: "manual", label: uiText("Choose printer") },
                      ]}
                      onChange={setRoutingStrategy}
                    />
                    <label className="block space-y-1.5 text-sm font-medium text-foreground">
                      {uiText("Copies")}
                      <input
                        className={selectClassName}
                        type="number"
                        min={1}
                        value={quantity || ""}
                        onChange={(event) => setQuantity(Number(event.target.value))}
                      />
                    </label>
                    <ChoiceField<JobPriority>
                      label={uiText("Priority")}
                      value={priority}
                      options={[
                        { value: "low", label: uiText("Low") },
                        { value: "normal", label: uiText("Normal") },
                        { value: "rush", label: uiText("Rush") },
                      ]}
                      onChange={setPriority}
                      inline
                    />
                    <label className="block space-y-1.5 text-sm font-medium text-foreground">
                      {uiText("Printer group")}
                      <input
                        className={selectClassName}
                        value={targetGroup}
                        onChange={(event) => setTargetGroup(event.target.value)}
                        placeholder={uiText("Any group")}
                      />
                    </label>
                  </div>
                )}

                {(deliveryMode === "send" || routingStrategy === "manual") && (
                  <fieldset className="space-y-2">
                    <legend className="mb-2 text-sm font-medium text-foreground">
                      {uiText("Choose printers")}
                    </legend>
                    <div className="divide-y divide-border">
                      {printers.map((printer) => {
                        const formatSupported =
                          printer.capabilities.accepted_print_formats.includes(selectedFileFormat);
                        const disabled =
                          !printer.access.can_print ||
                          !printer.capabilities.can_upload ||
                          !formatSupported;
                        const selected = selectedPrinterIds.includes(printer.id);
                        const offline =
                          printer.status === "offline" || printer.status === "unknown";
                        return (
                          <div
                            key={printer.id}
                            onClick={() => {
                              if (!disabled && !sending) togglePrinter(printer.id);
                            }}
                            className={`flex min-w-0 items-start gap-3 py-4 ${disabled ? "cursor-disabled opacity-60" : "cursor-action"}`}
                          >
                            <Checkbox
                              checked={selected}
                              onChange={() => togglePrinter(printer.id)}
                              disabled={disabled || sending}
                              ariaLabel={uiText("Select {value1}", {
                                value1: String(printer.name),
                              })}
                            />
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-sm font-medium text-foreground">
                                {printer.name} · {knownUiText(printer.status)}
                              </span>
                              <span className="mt-1 flex flex-wrap gap-1">
                                <span
                                  className={
                                    offline
                                      ? "text-xs text-muted-foreground"
                                      : "text-xs text-foreground"
                                  }
                                >
                                  {providerLabel(printer)} ·
                                </span>
                                <span className="text-xs text-muted-foreground">
                                  {!printer.access.can_print
                                    ? uiText("No print access")
                                    : !printer.capabilities.can_upload
                                      ? uiText("Upload unsupported")
                                      : !formatSupported
                                        ? uiText("Format unsupported")
                                        : printer.capabilities.can_start
                                          ? uiText("Upload + start")
                                          : uiText("Upload only")}
                                </span>
                              </span>
                            </span>
                            <span className="flex shrink-0 flex-col items-end gap-2 text-xs text-muted-foreground">
                              {compatibility?.printers.find((row) => row.printer_id === printer.id)
                                ?.verdict && (
                                <span>
                                  {knownUiText(
                                    compatibility.printers.find(
                                      (row) => row.printer_id === printer.id,
                                    )!.verdict,
                                  )}
                                </span>
                              )}
                              <Link
                                href={`/printers/${printer.id}`}
                                onClick={(event) => event.stopPropagation()}
                                className={buttonVariants({ variant: "outline", size: "xs" })}
                              >
                                {uiText("Printer details")}
                              </Link>
                            </span>
                          </div>
                        );
                      })}
                    </div>
                    <p className="pt-2 text-[13px] leading-relaxed text-muted-foreground">
                      {uiText(
                        "Loaded material and nozzle come from recorded printer state. This is not a G-code safety check, and unknown state does not block sending.",
                      )}
                    </p>
                  </fieldset>
                )}

                {availablePrinters.length === 0 && (
                  <div className="rounded-md border border-warning/30 bg-warning/10 p-3 text-sm text-warning">
                    {uiText("No configured printer supports Vault upload/send.")}
                  </div>
                )}
              </div>
              <div className="min-w-0 space-y-5">
                <div className="space-y-4">
                  <ChoiceField
                    label={uiText("G-code revision")}
                    value={selectedFile}
                    onChange={setSelectedFile}
                    options={gcodeFiles.map((file) => ({
                      value: file.id,
                      label: `${uiText("Rev ")}${file.gcode_revision_number ?? file.version}${file.revision_label ? ` · ${file.revision_label}` : ""}${file.id === defaultFile?.id ? uiText(" · Recommended") : ""}`,
                      description:
                        [
                          file.metadata?.material_type,
                          file.metadata?.layer_height_mm != null
                            ? `${file.metadata.layer_height_mm} mm`
                            : null,
                          file.metadata?.nozzle_diameter_mm != null
                            ? `${file.metadata.nozzle_diameter_mm} mm ${uiText("Nozzle")}`
                            : null,
                        ]
                          .filter(Boolean)
                          .join(" · ") || file.original_filename,
                    }))}
                  />
                  <h3 className="border-t border-border pt-5 text-sm font-semibold">
                    {uiText("Delivery")}
                  </h3>
                  {spoolmanEnabled && spools.length > 0 && (
                    <ChoiceField<number | "">
                      label={uiText("Spool")}
                      inline
                      value={selectedSpoolId}
                      onChange={setSelectedSpoolId}
                      options={[
                        { value: "", label: uiText("No spool") },
                        ...spools.map((spool) => ({
                          value: spool.id,
                          label:
                            spool.filament_name ||
                            spool.name ||
                            uiText("Spool {value1}", { value1: String(spool.id) }),
                          description: [
                            spool.vendor_name,
                            spool.location,
                            spool.remaining_weight != null
                              ? uiText(" ({value1} left)", {
                                  value1: formatGrams(spool.remaining_weight),
                                })
                              : null,
                          ]
                            .filter(Boolean)
                            .join(" · "),
                        })),
                      ]}
                    />
                  )}
                </div>

                {spoolCoverageWarning && (
                  <div className="rounded-md border border-warning/30 bg-warning/10 p-3 text-sm text-warning">
                    {spoolCoverageWarning}
                  </div>
                )}

                {deliveryMode === "send" && (
                  <label className="flex items-start gap-3">
                    <Checkbox
                      checked={startPrint}
                      onChange={setStartPrint}
                      disabled={!selectedPrintersCanStart || sending}
                      ariaLabel={uiText("Start print immediately")}
                      className="mt-0.5"
                    />
                    <span>
                      <span className="block text-sm font-medium text-foreground">
                        {uiText("Start after upload")}
                      </span>
                      <span className="mt-0.5 block text-xs leading-relaxed text-muted-foreground">
                        {uiText(
                          "Off by default. When enabled, selected printers begin printing after upload.",
                        )}
                      </span>
                      {!selectedPrintersCanStart && selectedPrinters.length > 0 && (
                        <span className="mt-1 block text-xs text-warning">
                          {uiText("Remove upload-only printers to enable this option.")}
                        </span>
                      )}
                    </span>
                  </label>
                )}

                {selectedUploads.length > 0 && (
                  <div className="border-t border-border pt-3 text-xs text-muted-foreground">
                    {uiText("Already uploaded to {value1}.", {
                      value1: String(
                        selectedUploads
                          .map((upload) => `${upload.printer_name} as ${upload.remote_filename}`)
                          .join(", ") ?? "",
                      ),
                    })}
                  </div>
                )}
              </div>
            </div>
            {displayError && (
              <div
                role="alert"
                className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive"
              >
                {displayError}
              </div>
            )}
          </div>
        </Modal>
      </>
    </Localized>
  );
}
