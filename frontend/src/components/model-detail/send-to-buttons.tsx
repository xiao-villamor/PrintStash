"use client";

import { useEffect, useMemo, useState } from "react";
import { Link } from "@/lib/navigation";
import { Loader2, Printer as PrinterIcon, Send, WifiOff } from "lucide-react";

import { sendToPrinter } from "@/lib/api";
import { usePrinters, useSpoolmanStatus, useSpools } from "@/lib/queries";
import { formatGrams } from "@/lib/format";
import { createTask, updateTask } from "@/lib/task-center";
import { toast } from "@/lib/toast";
import { useRequireAuth } from "@/lib/use-require-auth";
import { FileRead, ModelPrinterFileRead } from "@/types";

export function SendToButtons({
  gcodeFiles,
  printerFiles,
  open,
  onOpenChange,
  preselectFileId,
}: {
  gcodeFiles: Pick<FileRead, "id" | "original_filename" | "version" | "gcode_revision_number" | "revision_label" | "is_recommended">[];
  printerFiles: ModelPrinterFileRead[];
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  preselectFileId?: number;
}) {
  const auth = useRequireAuth();
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
        .filter((printer) => printer.capabilities.can_upload)
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
    () => printers.filter((printer) => printer.capabilities.can_upload),
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
    setSelectedPrinterIds((current) =>
      current.includes(id)
        ? current.filter((currentId) => currentId !== id)
        : [...current, id],
    );
  }

  async function send() {
    if (!selectedFile || selectedPrinters.length === 0) return;
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

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="font-mono text-xs text-on-surface-variant uppercase tracking-wider">Klipper status</span>
        <div className="flex items-center gap-1.5 px-2 py-1 bg-surface-container-lowest border border-outline-variant rounded">
          {printersLoading ? (
            <>
              <Loader2 className="h-3 w-3 animate-spin text-on-surface-variant" />
              <span className="font-mono text-xs text-on-surface-variant">Checking…</span>
            </>
          ) : printers.length === 0 ? (
            <>
              <WifiOff className="h-3 w-3 text-on-surface-variant" />
              <span className="font-mono text-xs text-on-surface-variant">No printers</span>
            </>
          ) : selectedPrinters.length > 0 && onlineCount > 0 ? (
            <>
              <span className="w-2 h-2 rounded-full bg-emerald-500" />
              <span className="font-mono text-xs font-bold text-emerald-500 tracking-wider">
                {onlineCount}/{selectedPrinters.length} online
              </span>
            </>
          ) : (
            <>
              <WifiOff className="h-3 w-3 text-amber-500" />
              <span className="font-mono text-xs text-amber-500 capitalize">
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

      {showSend ? (
        <div className="space-y-3">
          {printers.length > 0 && (
            <div className="space-y-1.5 rounded border border-outline-variant bg-surface-container-lowest p-2">
              {printers.map((printer) => {
                const disabled = !printer.capabilities.can_upload;
                return (
                  <label
                    key={printer.id}
                    className={`flex items-center justify-between gap-3 rounded px-2 py-1.5 font-mono text-xs ${
                      disabled
                        ? "text-on-surface-variant/60"
                        : "text-on-surface hover:bg-surface-container-low"
                    }`}
                  >
                    <span className="flex min-w-0 items-center gap-2">
                      <input
                        type="checkbox"
                        checked={selectedPrinterIds.includes(printer.id)}
                        onChange={() => togglePrinter(printer.id)}
                        disabled={disabled || sending}
                        className="rounded"
                      />
                      <span className="truncate">{printer.name}</span>
                    </span>
                    <span className="shrink-0 text-3xs uppercase tracking-wider text-on-surface-variant">
                      {disabled ? "Unsupported" : printer.status}
                    </span>
                  </label>
                );
              })}
            </div>
          )}
          {availablePrinters.length === 0 && (
            <div className="rounded border border-amber-500/30 bg-amber-500/10 p-2 text-2xs text-amber-600 font-mono">
              No configured printer supports Vault upload/send.
            </div>
          )}
          <select
            value={selectedFile}
            onChange={(e) => setSelectedFile(Number(e.target.value))}
            className="w-full bg-surface-container-lowest border border-outline-variant rounded px-3 py-2 font-mono text-xs text-on-surface focus:outline-none focus:ring-2 focus:ring-primary"
          >
            {gcodeFiles.map((f) => (
              <option key={f.id} value={f.id}>
                Rev {f.gcode_revision_number ?? f.version}
                {f.revision_label ? `, ${f.revision_label}` : ""}
                {f.is_recommended ? ", recommended" : ""}
              </option>
            ))}
          </select>
          <label className="flex items-center gap-2 text-xs font-mono text-on-surface-variant">
            <input
              type="checkbox"
              checked={startPrint}
              onChange={(e) => setStartPrint(e.target.checked)}
              disabled={!selectedPrintersCanStart || sending}
              className="rounded"
            />
            Start print immediately
          </label>
          {!selectedPrintersCanStart && selectedPrinters.length > 0 && (
            <p className="text-[11px] text-amber-600 font-mono">
              Selected printers support upload only. Remove them to enable start.
            </p>
          )}
          {spoolmanEnabled && spools.length > 0 && (
            <select
              value={selectedSpoolId}
              onChange={(e) => setSelectedSpoolId(e.target.value ? Number(e.target.value) : "")}
              className="w-full bg-surface-container-lowest border border-outline-variant rounded px-3 py-2 font-mono text-xs text-on-surface focus:outline-none focus:ring-2 focus:ring-primary"
            >
              <option value="">No spool</option>
              {spools.map((s) => (
                <option key={s.id} value={s.id}>
                  {(s.filament_name || s.name || `Spool ${s.id}`) +
                    (s.vendor_name ? ` · ${s.vendor_name}` : "") +
                    (s.remaining_weight != null
                      ? ` (${formatGrams(s.remaining_weight)} left)`
                      : "")}
                </option>
              ))}
            </select>
          )}
          {selectedUploads.length > 0 && (
            <div className="rounded border border-emerald-500/30 bg-emerald-500/10 p-2 text-2xs text-emerald-600 font-mono break-words">
              Already on{" "}
              {selectedUploads
                .map((upload) => `${upload.printer_name} as ${upload.remote_filename}`)
                .join(", ")}
            </div>
          )}
          {displayError && (
            <div className="rounded border border-error/30 bg-error-container/20 p-2 text-2xs text-error font-mono break-words">
              {displayError}
            </div>
          )}
          <div className="flex gap-2">
            <button onClick={() => setShowSend(false)} disabled={sending} className="flex-1 py-2 rounded border border-outline-variant text-on-surface-variant font-mono text-xs uppercase tracking-wider hover:bg-surface-container-low transition-colors disabled:opacity-50">Cancel</button>
            <button onClick={send} disabled={sending || selectedPrinters.length === 0 || (startPrint && !selectedPrintersCanStart)} className="flex-1 py-2 rounded bg-primary text-primary-foreground font-mono text-xs uppercase tracking-wider hover:opacity-90 transition-opacity disabled:opacity-50 flex items-center justify-center gap-1.5">
              {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
              {sending ? "Sending…" : startPrint ? "Send & Print" : "Send"}
            </button>
          </div>
        </div>
      ) : printers.length === 0 ? (
        <div className="space-y-2 rounded border border-outline-variant bg-surface-container-lowest p-3">
          <div className="flex items-center gap-2">
            <WifiOff className="h-4 w-4 text-on-surface-variant" />
            <span className="font-mono text-xs uppercase tracking-wider text-on-surface">
              No printers configured
            </span>
          </div>
          <p className="font-mono text-2xs text-on-surface-variant leading-relaxed">
            Connect Klipper / Moonraker to send files directly to a printer.
          </p>
          <Link
            href="/printers"
            className="mt-1 w-full py-2 bg-primary text-primary-foreground hover:opacity-90 transition-opacity rounded font-mono text-xs uppercase tracking-wider shadow-sm flex items-center justify-center gap-2"
          >
            <PrinterIcon className="h-4 w-4" /> Configure printer
          </Link>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <button
            onClick={() => {
              if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
              setShowSend(true);
            }}
            disabled={!auth.isAuthenticated}
            className="w-full py-2.5 bg-primary text-primary-foreground hover:opacity-90 transition-opacity rounded font-mono text-xs uppercase tracking-wider shadow-sm flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {!auth.isAuthenticated ? (
              <><Send className="h-4 w-4" /> Sign in to send</>
            ) : (
              <><Send className="h-4 w-4" /> Send to printer</>
            )}
          </button>
          <Link href="/printers" className="w-full py-2 border border-outline-variant text-on-surface-variant hover:bg-surface-container-low transition-colors rounded font-mono text-xs uppercase tracking-wider text-center">
            Manage printers
          </Link>
        </div>
      )}
    </div>
  );
}
