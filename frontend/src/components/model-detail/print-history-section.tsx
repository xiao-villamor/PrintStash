"use client";

import { useState } from "react";
import {
  Check,
  CheckCircle2,
  Clock,
  Loader2,
  Plus,
  RefreshCw,
  XCircle,
} from "lucide-react";

import {
  createManualPrintJob,
  getModelPrintJobs,
  importPrintJobsFromPrinter,
} from "@/lib/api";
import { usePrinters } from "@/lib/queries";
import { formatCost, formatDuration, formatGrams, timeAgo } from "@/lib/format";
import { toast } from "@/lib/toast";
import { FileRead, ModelPrintJobRead } from "@/types";

import { PRINT_JOB_PRESENTATION, printJobToneClass } from "./presentation";

type PrintHistoryMode = "manual" | "auto";

export function PrintHistorySection({
  jobs,
  modelId,
  gcodeFiles,
  onJobCreated,
}: {
  jobs: ModelPrintJobRead[];
  modelId: number;
  gcodeFiles: FileRead[];
  onJobCreated: (job: ModelPrintJobRead) => void;
}) {
  const [showAdd, setShowAdd] = useState(false);
  const [mode, setMode] = useState<PrintHistoryMode>("manual");

  // Manual form state
  const printers = usePrinters().data ?? [];
  const [selectedPrinterId, setSelectedPrinterId] = useState<number | "">("");
  // When the printer isn't a registered one, log against this free-text name.
  const [adhocPrinter, setAdhocPrinter] = useState(false);
  const [adhocPrinterName, setAdhocPrinterName] = useState("");
  const [selectedFileId, setSelectedFileId] = useState<number | "">(gcodeFiles[0]?.id ?? "");
  const [jobState, setJobState] = useState("completed");
  const [startedAt, setStartedAt] = useState("");
  const [finishedAt, setFinishedAt] = useState("");
  const [jobError, setJobError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // Auto mode state
  const [importing, setImporting] = useState(false);
  const [importResults, setImportResults] = useState<{ filename: string; imported: boolean }[]>([]);
  const [importDone, setImportDone] = useState(false);

  function openAdd() {
    setShowAdd(true);
    setMode("manual");
    setSelectedPrinterId("");
    setAdhocPrinter(false);
    setAdhocPrinterName("");
    setSelectedFileId(gcodeFiles[0]?.id ?? "");
    setJobState("completed");
    setStartedAt("");
    setFinishedAt("");
    setJobError("");
    setImportResults([]);
    setImportDone(false);
    // Printers come from the shared usePrinters() cache — no fetch needed here.
  }

  const manualPrinterReady = adhocPrinter
    ? adhocPrinterName.trim().length > 0
    : !!selectedPrinterId;

  async function submitManual() {
    if (!manualPrinterReady || !selectedFileId) return;
    setSubmitting(true);
    try {
      const job = await createManualPrintJob(modelId, {
        printer_id: adhocPrinter ? null : (selectedPrinterId as number),
        printer_name: adhocPrinter ? adhocPrinterName.trim() : null,
        file_id: selectedFileId as number,
        state: jobState,
        started_at: startedAt || null,
        finished_at: finishedAt || null,
        error: jobError || null,
      });
      onJobCreated(job);
      setShowAdd(false);
      toast.success("Print record added");
    } catch (e) {
      toast.error(e);
    } finally {
      setSubmitting(false);
    }
  }

  async function runAutoImport() {
    if (!selectedPrinterId) return;
    setImporting(true);
    setImportResults([]);
    setImportDone(false);
    try {
      const results = await importPrintJobsFromPrinter(modelId, selectedPrinterId as number);
      setImportResults(results.map((r) => ({ filename: r.filename, imported: r.imported })));
      setImportDone(true);
      const imported = results.filter((r) => r.imported).length;
      if (imported > 0) {
        const refreshed = await getModelPrintJobs(modelId);
        refreshed.forEach((j) => onJobCreated(j));
        toast.success(`Imported ${imported} job${imported === 1 ? "" : "s"} from printer`);
      } else {
        toast.success("No new jobs to import");
      }
    } catch (e) {
      toast.error(e);
    } finally {
      setImporting(false);
    }
  }

  return (
    <section>
      <div className="flex items-center justify-between mb-4 pb-1 border-b border-[var(--outline-variant)]">
        <h2 className="text-lg font-semibold text-[var(--on-surface)] flex items-center gap-2">
          <Clock className="h-4 w-4" /> Print History
        </h2>
        <button
          onClick={openAdd}
          className="inline-flex items-center gap-1.5 rounded border border-[var(--outline-variant)] px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)] transition-colors hover:bg-[var(--surface-container-low)]"
        >
          <Plus className="h-3.5 w-3.5" /> Add Record
        </button>
      </div>

      {/* Add record panel */}
      {showAdd && (
        <div className="mb-4 border border-[var(--outline-variant)] rounded bg-[var(--surface-container-low)] p-3 space-y-3">
          {/* Mode toggle */}
          <div className="flex gap-1">
            {(["manual", "auto"] as PrintHistoryMode[]).map((m) => (
              <button
                key={m}
                onClick={() => { setMode(m); setImportResults([]); setImportDone(false); }}
                className={`px-3 py-1 font-mono text-[10px] uppercase tracking-wider rounded transition-colors ${
                  mode === m
                    ? "bg-[var(--primary)] text-[var(--primary-foreground)]"
                    : "border border-[var(--outline-variant)] text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-high)]"
                }`}
              >
                {m === "manual" ? "Manual Entry" : "Auto from Printer"}
              </button>
            ))}
          </div>

          {mode === "manual" ? (
            <div className="space-y-2">
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="block font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)] mb-1">Printer</label>
                  <select
                    value={adhocPrinter ? "__adhoc__" : selectedPrinterId}
                    onChange={(e) => {
                      const v = e.target.value;
                      if (v === "__adhoc__") {
                        setAdhocPrinter(true);
                        setSelectedPrinterId("");
                      } else {
                        setAdhocPrinter(false);
                        setSelectedPrinterId(v ? Number(v) : "");
                      }
                    }}
                    className="w-full h-8 bg-[var(--surface)] text-[var(--on-surface)] font-mono text-xs border border-[var(--outline-variant)] rounded px-2 focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
                  >
                    <option value="">Select printer…</option>
                    {printers.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                    <option value="__adhoc__">Other (not listed)…</option>
                  </select>
                </div>
                <div>
                  <label className="block font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)] mb-1">G-code Revision</label>
                  <select
                    value={selectedFileId}
                    onChange={(e) => setSelectedFileId(e.target.value ? Number(e.target.value) : "")}
                    className="w-full h-8 bg-[var(--surface)] text-[var(--on-surface)] font-mono text-xs border border-[var(--outline-variant)] rounded px-2 focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
                  >
                    <option value="">Select revision…</option>
                    {gcodeFiles.map((f, i) => (
                      <option key={f.id} value={f.id}>Rev {i + 1} — {f.original_filename}</option>
                    ))}
                  </select>
                </div>
              </div>
              {adhocPrinter && (
                <div>
                  <label className="block font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)] mb-1">Printer name</label>
                  <input
                    value={adhocPrinterName}
                    onChange={(e) => setAdhocPrinterName(e.target.value)}
                    maxLength={128}
                    placeholder="e.g. Garage Prusa MK4"
                    className="w-full h-8 bg-[var(--surface)] text-[var(--on-surface)] font-mono text-xs border border-[var(--outline-variant)] rounded px-2 focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
                  />
                </div>
              )}
              <div>
                <label className="block font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)] mb-1">Result</label>
                <select
                  value={jobState}
                  onChange={(e) => setJobState(e.target.value)}
                  className="w-full h-8 bg-[var(--surface)] text-[var(--on-surface)] font-mono text-xs border border-[var(--outline-variant)] rounded px-2 focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
                >
                  <option value="completed">Completed</option>
                  <option value="failed">Failed</option>
                  <option value="cancelled">Cancelled</option>
                </select>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="block font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)] mb-1">Started (opt.)</label>
                  <input
                    type="datetime-local"
                    value={startedAt}
                    onChange={(e) => setStartedAt(e.target.value)}
                    className="w-full h-8 bg-[var(--surface)] text-[var(--on-surface)] font-mono text-xs border border-[var(--outline-variant)] rounded px-2 focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
                  />
                </div>
                <div>
                  <label className="block font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)] mb-1">Finished (opt.)</label>
                  <input
                    type="datetime-local"
                    value={finishedAt}
                    onChange={(e) => setFinishedAt(e.target.value)}
                    className="w-full h-8 bg-[var(--surface)] text-[var(--on-surface)] font-mono text-xs border border-[var(--outline-variant)] rounded px-2 focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
                  />
                </div>
              </div>
              {jobState === "failed" && (
                <div>
                  <label className="block font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)] mb-1">Error (opt.)</label>
                  <input
                    value={jobError}
                    onChange={(e) => setJobError(e.target.value)}
                    placeholder="Describe what went wrong…"
                    className="w-full h-8 bg-[var(--surface)] text-[var(--on-surface)] font-mono text-xs border border-[var(--outline-variant)] rounded px-2 focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
                  />
                </div>
              )}
              <div className="flex gap-2 pt-1">
                <button
                  onClick={submitManual}
                  disabled={submitting || !manualPrinterReady || !selectedFileId}
                  className="flex-1 h-8 bg-[var(--primary)] text-[var(--primary-foreground)] font-mono text-xs uppercase tracking-wider rounded disabled:opacity-50 hover:opacity-90 transition-opacity flex items-center justify-center gap-1.5"
                >
                  {submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
                  Save
                </button>
                <button onClick={() => setShowAdd(false)} className="px-3 h-8 border border-[var(--outline-variant)] rounded font-mono text-xs text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-high)] transition-colors">
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div className="space-y-2">
              <p className="font-mono text-[11px] text-[var(--on-surface-variant)]">
                Fetch recent print history from a Moonraker printer and import jobs matching this model&apos;s G-code files.
              </p>
              <div>
                <label className="block font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)] mb-1">Printer</label>
                <select
                  value={selectedPrinterId}
                  onChange={(e) => { setSelectedPrinterId(e.target.value ? Number(e.target.value) : ""); setImportResults([]); setImportDone(false); }}
                  className="w-full h-8 bg-[var(--surface)] text-[var(--on-surface)] font-mono text-xs border border-[var(--outline-variant)] rounded px-2 focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
                >
                  <option value="">Select printer…</option>
                  {printers.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </div>
              {importDone && importResults.length > 0 && (
                <div className="space-y-1">
                  {importResults.map((r) => (
                    <div key={r.filename} className="flex items-center gap-2 font-mono text-[11px]">
                      {r.imported
                        ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 shrink-0" />
                        : <XCircle className="h-3.5 w-3.5 text-[var(--on-surface-variant)] shrink-0" />
                      }
                      <span className={r.imported ? "text-[var(--on-surface)]" : "text-[var(--on-surface-variant)]"}>{r.filename}</span>
                      <span className="opacity-50">{r.imported ? "imported" : "already exists"}</span>
                    </div>
                  ))}
                </div>
              )}
              {importDone && importResults.length === 0 && (
                <p className="font-mono text-[11px] text-[var(--on-surface-variant)]">No matching jobs found on this printer.</p>
              )}
              <div className="flex gap-2 pt-1">
                <button
                  onClick={runAutoImport}
                  disabled={importing || !selectedPrinterId}
                  className="flex-1 h-8 bg-[var(--primary)] text-[var(--primary-foreground)] font-mono text-xs uppercase tracking-wider rounded disabled:opacity-50 hover:opacity-90 transition-opacity flex items-center justify-center gap-1.5"
                >
                  {importing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                  Fetch &amp; Import
                </button>
                <button onClick={() => setShowAdd(false)} className="px-3 h-8 border border-[var(--outline-variant)] rounded font-mono text-xs text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-high)] transition-colors">
                  Close
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {jobs.length === 0 ? (
        <p className="font-mono text-xs text-[var(--on-surface-variant)]">
          No print history yet. Add a record manually or import from a printer.
        </p>
      ) : (
        <div className="space-y-2">
          {jobs.map((job) => {
            const present = PRINT_JOB_PRESENTATION[job.state];
            const Icon =
              present.tone === "success" ? CheckCircle2 : present.tone === "error" ? XCircle : Clock;
            return (
              <div
                key={job.id}
                className="p-3 border border-[var(--outline-variant)] rounded bg-[var(--surface)] space-y-1"
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <Icon
                      className={`h-4 w-4 shrink-0 ${
                        present.tone === "success"
                          ? "text-emerald-600"
                          : present.tone === "error"
                            ? "text-[var(--error)]"
                            : "text-amber-600"
                      }`}
                    />
                    <span className="font-mono text-[13px] text-[var(--on-surface)] truncate">
                      Rev {job.gcode_revision_number ?? "—"} · {job.printer_name}
                    </span>
                  </div>
                  <span className={`shrink-0 border rounded px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider ${printJobToneClass(present.tone)}`}>
                    {present.label}
                  </span>
                </div>
                <p className="font-mono text-[11px] text-[var(--on-surface-variant)]">
                  {job.material_type ? `${job.material_type} · ` : ""}
                  {timeAgo(job.created_at)}
                </p>
                {(job.actual_duration_s != null ||
                  job.filament_used_g != null) && (
                  <p className="font-mono text-[11px] text-[var(--on-surface-variant)]">
                    <span className="text-emerald-600">measured</span>
                    {job.actual_duration_s != null
                      ? ` · ${formatDuration(job.actual_duration_s)}`
                      : ""}
                    {job.filament_used_g != null
                      ? ` · ${formatGrams(job.filament_used_g)}`
                      : ""}
                    {job.filament_cost != null
                      ? ` · ${formatCost(job.filament_cost)}`
                      : ""}
                  </p>
                )}
                {job.error && (
                  <p className="font-mono text-[11px] text-[var(--error)] break-words">
                    {job.error}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
