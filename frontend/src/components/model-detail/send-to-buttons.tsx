"use client";

import { useEffect, useMemo, useState } from "react";
import { Link } from "@/lib/navigation";
import { FileCode2, Loader2, Printer as PrinterIcon, Send, WifiOff } from "lucide-react";

import { enqueueFleetJob, sendToPrinter } from "@/lib/api";
import { usePrinters, useSpoolmanStatus, useSpools } from "@/lib/queries";
import { formatGrams } from "@/lib/format";
import { createTask, updateTask } from "@/lib/task-center";
import { toast } from "@/lib/toast";
import { useRequireAuth } from "@/lib/use-require-auth";
import { useAuth } from "@/lib/auth-context";
import { FileRead, ModelPrinterFileRead, RoutingStrategy } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Modal } from "@/components/ui/modal";
import { Localized } from "@/components/ui/localized";

const selectClassName =
  "h-10 w-full rounded-md border border-input bg-background px-3 text-sm text-foreground ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";

export function SendToButtons({
  gcodeFiles,
  printerFiles,
  open,
  onOpenChange,
  preselectFileId,
}: {
  gcodeFiles: Pick<
    FileRead,
    "id" | "original_filename" | "version" | "gcode_revision_number" | "revision_label" | "is_recommended" | "metadata"
  >[];
  printerFiles: ModelPrinterFileRead[];
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  preselectFileId?: number;
}) {
  const auth = useRequireAuth();
  const { user } = useAuth();
  const [internalOpen, setInternalOpen] = useState(false);
  const showSend = open ?? internalOpen;
  const setShowSend = onOpenChange ?? setInternalOpen;
  const defaultFile = gcodeFiles.find((f) => f.is_recommended) ?? gcodeFiles[gcodeFiles.length - 1];
  const [selectedFile, setSelectedFile] = useState<number>(defaultFile?.id ?? 0);

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
  const [deliveryMode, setDeliveryMode] = useState<"send" | "queue">("send");
  const [routingStrategy, setRoutingStrategy] = useState<RoutingStrategy>("least_busy");
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
    error ??
    (printersQuery.error instanceof Error ? printersQuery.error.message : null);

  // Default-select a capable printer once printers load (not gated on the panel
  // being open) so the collapsed "x/y online" indicator reflects a selection.
  useEffect(() => {
    setSelectedPrinterIds((current) => {
      const capableIds = printers
        .filter((printer) => printer.access.can_print && printer.capabilities.can_upload)
        .map((printer) => printer.id);
      if (capableIds.length === 0) return [];
      const kept = current.filter((id) => capableIds.includes(id));
      return kept.length > 0 ? kept : [capableIds[0]];
    });
  }, [printers]);

  const selectedPrinters = useMemo(
    () => printers.filter((printer) => selectedPrinterIds.includes(printer.id)),
    [printers, selectedPrinterIds],
  );
  const availablePrinters = useMemo(
    () => printers.filter((printer) => printer.access.can_print && printer.capabilities.can_upload),
    [printers],
  );
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
      current.includes(id)
        ? current.filter((currentId) => currentId !== id)
        : [...current, id],
    );
  }

  async function send() {
    if (!selectedFile) return;
    if (deliveryMode === "queue") {
      if (routingStrategy === "manual" && selectedPrinters.length === 0) return;
      const spool = selectedSpoolId !== "" ? spools.find((candidate) => candidate.id === selectedSpoolId) : undefined;
      setSending(true);
      setError(null);
      try {
        await enqueueFleetJob({
          file_id: selectedFile,
          strategy: routingStrategy,
          printer_id: routingStrategy === "manual" ? selectedPrinters[0]?.id : undefined,
          spool_id: selectedSpoolId === "" ? null : selectedSpoolId,
          spool_name: spool ? spool.filament_name || spool.name || `Spool ${spool.id}` : null,
          spool_filament_id: spool?.filament_id ?? null,
        });
        setShowSend(false);
        toast.success("Added to fleet queue");
      } catch (e: any) {
        setError(e.message || "Queue failed");
      } finally {
        setSending(false);
      }
      return;
    }
    if (selectedPrinters.length === 0) return;
    if (startPrint && !selectedPrintersCanStart) {
      setError("One or more selected printers support upload only.");
      return;
    }
    const file = gcodeFiles.find((candidate) => candidate.id === selectedFile);
    const taskId = createTask({
      title: `Send ${file?.original_filename ?? "G-code"}`,
      detail: `Sending to ${selectedPrinters.length} printer${selectedPrinters.length === 1 ? "" : "s"}`,
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
            selectedSpoolId !== ""
              ? spools.find((s) => s.id === selectedSpoolId)
              : undefined;
          const job = await sendToPrinter(printer.id, {
            file_id: selectedFile,
            start_print: startPrint,
            spool_id: selectedSpoolId === "" ? null : (selectedSpoolId as number),
            spool_name: spool
              ? spool.filament_name || spool.name || `Spool ${spool.id}`
              : null,
            spool_filament_id: spool ? spool.filament_id : null,
          });
          completed += 1;
          updateTask(taskId, {
            detail: `${completed}/${selectedPrinters.length} printers completed`,
            status: "running",
            progress: 10 + (completed / selectedPrinters.length) * 85,
          });
          return { printer, job };
        }),
      );

      const successes = results.filter((result) => result.status === "fulfilled");
      const failures = results
        .map((result, index) => ({ result, printer: selectedPrinters[index] }))
        .filter(
          (entry): entry is { result: PromiseRejectedResult; printer: (typeof selectedPrinters)[number] } =>
            entry.result.status === "rejected",
        );

      if (failures.length > 0) {
        const reasons = failures
          .map(({ printer, result }) => `${printer.name}: ${result.reason?.message ?? "unknown error"}`)
          .join("; ");
        const message = `${successes.length}/${selectedPrinters.length} printers succeeded — ${reasons}`;
        setError(message);
        updateTask(taskId, {
          detail: message,
          status: successes.length > 0 ? "completed" : "failed",
          progress: 100,
        });
        toast.warning("Some sends failed", reasons);
      } else {
        updateTask(taskId, {
          detail: startPrint ? "Print started on selected printers" : "Sent to selected printers",
          status: "completed",
          progress: 100,
        });
        setShowSend(false);
        toast.success(
          startPrint
            ? `Print started on ${successes.length} printer${successes.length === 1 ? "" : "s"}`
            : `Sent to ${successes.length} printer${successes.length === 1 ? "" : "s"}`,
        );
      }
    } catch (e: any) {
      const message = e.message || "Send failed";
      setError(message);
      updateTask(taskId, {
        detail: message,
        status: "failed",
        progress: 100,
      });
    } finally {
      setSending(false);
    }
  }

  const selectedFileDetails = gcodeFiles.find((file) => file.id === selectedFile);
  const selectedSpool = selectedSpoolId !== "" ? spools.find((spool) => spool.id === selectedSpoolId) : undefined;
  // ponytail: single-file check only — a multi-plate build evaluated as one set
  // needs a batch-send feature that doesn't exist yet (#64 follow-up).
  const requiredWeightG = selectedFileDetails?.metadata?.filament_weight_g ?? null;
  const spoolCoverageWarning = selectedSpool
    ? requiredWeightG == null
      ? "This revision's filament weight is unknown — can't verify it fits the selected spool."
      : selectedSpool.remaining_weight == null
        ? "The selected spool has no tracked remaining weight — can't verify it covers this print."
        : requiredWeightG > selectedSpool.remaining_weight
          ? `This revision needs ~${formatGrams(requiredWeightG)}; the selected spool has ~${formatGrams(selectedSpool.remaining_weight)} left.`
          : null
    : null;

  return (
    <Localized><>
      <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="font-mono text-xs uppercase tracking-wider text-muted-foreground">Printer status</span>
        <div className="flex items-center gap-1.5 rounded-md border border-border bg-background px-2 py-1">
          {printersLoading ? (
            <>
              <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
              <span className="font-mono text-xs text-muted-foreground">Checking…</span>
            </>
          ) : printers.length === 0 ? (
            <>
              <WifiOff className="h-3 w-3 text-muted-foreground" />
              <span className="font-mono text-xs text-muted-foreground">No printers</span>
            </>
          ) : selectedPrinters.length > 0 && onlineCount > 0 ? (
            <>
              <span className="h-2 w-2 rounded-full bg-success" />
              <span className="font-mono text-xs font-bold tracking-wider text-success">
                {onlineCount}/{selectedPrinters.length} online
              </span>
            </>
          ) : (
            <>
              <WifiOff className="h-3 w-3 text-warning" />
              <span className="font-mono text-xs capitalize text-warning">
                No selected printer online
              </span>
            </>
          )}
        </div>
      </div>
      {displayError && !showSend && (
        <div className="rounded border border-error/30 bg-error-container/20 p-2 text-2xs text-error font-mono break-words">
          {displayError}
        </div>
      )}

      {printers.length === 0 ? (
        <div className="space-y-2 rounded border border-outline-variant bg-surface-container-lowest p-3">
          <div className="flex items-center gap-2">
            <WifiOff className="h-4 w-4 text-on-surface-variant" />
            <span className="font-mono text-xs uppercase tracking-wider text-on-surface">
              No printers configured
            </span>
          </div>
          <p className="font-mono text-2xs text-on-surface-variant leading-relaxed">
            Connect a supported printer to send files directly from the Vault.
          </p>
          <Button asChild size="sm" className="mt-1 w-full">
            <Link href="/printers"><PrinterIcon className="h-4 w-4" /> Configure printer</Link>
          </Button>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <Button
            onClick={() => {
              if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
              setShowSend(true);
            }}
            disabled={!auth.isAuthenticated}
            className="w-full"
          >
            {!auth.isAuthenticated ? (
              <><Send className="h-4 w-4" /> Sign in to send</>
            ) : (
              <><Send className="h-4 w-4" /> Send to printer</>
            )}
          </Button>
          <Button asChild variant="outline" size="sm" className="w-full">
            <Link href="/printers">Manage printers</Link>
          </Button>
        </div>
      )}
      </div>

      <Modal
        open={showSend}
        onClose={() => { if (!sending) setShowSend(false); }}
        title="Send to printer"
        className="flex max-h-[calc(100vh-2rem)] max-w-2xl flex-col overflow-hidden"
      >
        <div
          data-testid="send-dialog-scroll-region"
          className="min-h-0 flex-1 space-y-5 overflow-y-auto px-1"
        >
          <div className="flex items-start gap-3 rounded-lg border border-border bg-muted/50 p-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-accent text-accent-foreground">
              <FileCode2 className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-foreground">
                {selectedFileDetails?.original_filename ?? "Select G-code revision"}
              </p>
              <p className="mt-0.5 font-mono text-2xs uppercase tracking-wider text-muted-foreground">
                {selectedPrinters.length} printer{selectedPrinters.length === 1 ? "" : "s"} selected
              </p>
            </div>
          </div>

          <fieldset className="space-y-2">
            <legend className="mb-2 text-sm font-medium text-foreground">Action</legend>
            <div className="grid grid-cols-2 gap-2">
              <Button type="button" variant={deliveryMode === "send" ? "secondary" : "outline"} onClick={() => setDeliveryMode("send")}>Send now</Button>
              <Button type="button" variant={deliveryMode === "queue" ? "secondary" : "outline"} onClick={() => { setDeliveryMode("queue"); setStartPrint(false); if (!user?.is_superuser) setRoutingStrategy("manual"); }}>Add to queue</Button>
            </div>
          </fieldset>

          {deliveryMode === "queue" && (
            <label className="block space-y-1.5 text-sm font-medium text-foreground">
              Routing
              <select value={routingStrategy} onChange={(event) => setRoutingStrategy(event.target.value as RoutingStrategy)} className={selectClassName}>
                {user?.is_superuser && <option value="least_busy">Least busy eligible printer</option>}
                {user?.is_superuser && <option value="default">Default printer</option>}
                <option value="manual">Choose printer</option>
              </select>
            </label>
          )}

          {(deliveryMode === "send" || routingStrategy === "manual") && <fieldset className="space-y-2">
            <legend className="mb-2 text-sm font-medium text-foreground">Printers</legend>
            <div className="grid gap-2 sm:grid-cols-2">
              {printers.map((printer) => {
                const disabled = !printer.access.can_print || !printer.capabilities.can_upload;
                const selected = selectedPrinterIds.includes(printer.id);
                const offline = printer.status === "offline" || printer.status === "unknown";
                return (
                  <div
                    key={printer.id}
                    onClick={() => { if (!disabled && !sending) togglePrinter(printer.id); }}
                    className={`flex min-w-0 items-center gap-3 rounded-lg border p-3 transition-[background-color,border-color] duration-press ${
                      selected
                        ? "border-primary bg-primary-soft"
                        : "border-border bg-background hover:bg-popover-hover"
                    } ${disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer"}`}
                  >
                    <Checkbox
                      checked={selected}
                      onChange={() => togglePrinter(printer.id)}
                      disabled={disabled || sending}
                      ariaLabel={`Select ${printer.name}`}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium text-foreground">{printer.name}</span>
                      <span className="mt-1 flex flex-wrap gap-1">
                        <Badge variant={offline ? "warning" : "success"} className="font-mono text-3xs uppercase tracking-wider">
                          {printer.status}
                        </Badge>
                        <Badge variant="outline" className="font-mono text-3xs uppercase tracking-wider">
                          {disabled ? "Upload unsupported" : printer.capabilities.can_start ? "Upload + start" : "Upload only"}
                        </Badge>
                      </span>
                    </span>
                  </div>
                );
              })}
            </div>
          </fieldset>}

          {availablePrinters.length === 0 && (
            <div className="rounded-md border border-warning/30 bg-warning/10 p-3 text-sm text-warning">
              No configured printer supports Vault upload/send.
            </div>
          )}

          <div className={`grid gap-4 ${spoolmanEnabled && spools.length > 0 ? "sm:grid-cols-2" : ""}`}>
            <label className="space-y-1.5 text-sm font-medium text-foreground">
              G-code revision
              <select value={selectedFile} onChange={(e) => setSelectedFile(Number(e.target.value))} className={selectClassName}>
                {gcodeFiles.map((file) => (
                  <option key={file.id} value={file.id}>
                    Rev {file.gcode_revision_number ?? file.version}
                    {file.revision_label ? ` · ${file.revision_label}` : ""}
                    {file.id === defaultFile?.id ? " · Recommended" : ""}
                  </option>
                ))}
              </select>
            </label>
            {spoolmanEnabled && spools.length > 0 && (
              <label className="space-y-1.5 text-sm font-medium text-foreground">
                Spool
                <select
                  value={selectedSpoolId}
                  onChange={(e) => setSelectedSpoolId(e.target.value ? Number(e.target.value) : "")}
                  className={selectClassName}
                >
                  <option value="">No spool</option>
                  {spools.map((spool) => (
                    <option key={spool.id} value={spool.id}>
                      {(spool.filament_name || spool.name || `Spool ${spool.id}`) +
                        (spool.vendor_name ? ` · ${spool.vendor_name}` : "") +
                        (spool.location ? ` · ${spool.location}` : "") +
                        (spool.remaining_weight != null ? ` (${formatGrams(spool.remaining_weight)} left)` : "")}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>

          {spoolCoverageWarning && (
            <div className="rounded-md border border-warning/30 bg-warning/10 p-3 text-sm text-warning">
              {spoolCoverageWarning}
            </div>
          )}

          {deliveryMode === "send" && <label className={`flex items-start gap-3 rounded-lg border p-3 ${startPrint ? "border-warning/50 bg-warning/10" : "border-border bg-background"}`}>
            <Checkbox
              checked={startPrint}
              onChange={setStartPrint}
              disabled={!selectedPrintersCanStart || sending}
              ariaLabel="Start print immediately"
              className="mt-0.5"
            />
            <span>
              <span className="block text-sm font-medium text-foreground">Start print immediately</span>
              <span className="mt-0.5 block text-xs leading-relaxed text-muted-foreground">
                Off by default. When enabled, selected printers begin printing after upload.
              </span>
              {!selectedPrintersCanStart && selectedPrinters.length > 0 && (
                <span className="mt-1 block text-xs text-warning">Remove upload-only printers to enable this option.</span>
              )}
            </span>
          </label>}

          {selectedUploads.length > 0 && (
            <div className="rounded-md border border-success/30 bg-success/10 p-3 text-xs text-success">
              Already uploaded to {selectedUploads.map((upload) => `${upload.printer_name} as ${upload.remote_filename}`).join(", ")}.
            </div>
          )}
          {displayError && (
            <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
              {displayError}
            </div>
          )}
        </div>

        <div className="-mx-6 -mb-6 mt-5 flex shrink-0 justify-end gap-2 border-t border-border bg-muted/30 px-6 py-4">
          <Button variant="outline" onClick={() => setShowSend(false)} disabled={sending}>Cancel</Button>
          <Button
            onClick={send}
            loading={sending}
            disabled={!selectedFile || (deliveryMode === "send" ? selectedPrinters.length === 0 || (startPrint && !selectedPrintersCanStart) : routingStrategy === "manual" && selectedPrinters.length === 0)}
          >
            {!sending && <Send className="h-4 w-4" />}
            {sending ? (deliveryMode === "queue" ? "Queuing…" : "Sending…") : deliveryMode === "queue" ? "Add to queue" : startPrint ? "Send & start print" : "Send to printer"}
          </Button>
        </div>
      </Modal>
    </></Localized>
  );
}
