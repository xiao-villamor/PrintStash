"use client";

import { knownUiText } from "@/lib/locale";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { useId, useState } from "react";
import { Check, CheckCircle2, Clock, Loader2, Plus, RefreshCw, XCircle } from "lucide-react";

import { createManualPrintJob, getModelPrintJobs, importPrintJobsFromPrinter } from "@/lib/api";
import { usePrinters, useSpoolmanStatus, useSpools } from "@/lib/queries";
import { formatCost, formatDuration, formatGrams, timeAgo } from "@/lib/format";
import { toast } from "@/lib/toast";
import { FileRead, ModelPrintJobRead } from "@/types";
import { PrintJobReproducibility } from "@/components/print-job-reproducibility";
import { printJobArtifactLabel } from "@/lib/print-job-reproducibility";

import { PRINT_JOB_PRESENTATION, printJobToneClass } from "./presentation";
import { Modal } from "@/components/ui/modal";
import { Localized } from "@/components/ui/localized";

const PRINT_HISTORY_MODES = ["manual", "auto"] as const;
type PrintHistoryMode = (typeof PRINT_HISTORY_MODES)[number];

export function PrintHistorySection({
  jobs,
  modelId,
  gcodeFiles,
  onJobCreated,
  initialEntry,
}: {
  jobs: ModelPrintJobRead[];
  modelId: number;
  gcodeFiles: FileRead[];
  onJobCreated: (job: ModelPrintJobRead) => void;
  initialEntry?: { fileId: number; state: "completed" | "failed" } | null;
}) {
  useUiLocale();
  const formId = useId();
  const [showAdd, setShowAdd] = useState(!!initialEntry);
  const [mode, setMode] = useState<PrintHistoryMode>("manual");

  // Manual form state
  const printers = usePrinters().data ?? [];
  // `null` is "nothing selected"; the empty-string the <select> needs for that is
  // produced at the JSX boundary, so the ids stay numbers everywhere else.
  const [selectedPrinterId, setSelectedPrinterId] = useState<number | null>(null);
  // When the printer isn't a registered one, log against this free-text name.
  const [adhocPrinter, setAdhocPrinter] = useState(false);
  const [adhocPrinterName, setAdhocPrinterName] = useState("");
  const [selectedFileId, setSelectedFileId] = useState<number | null>(
    initialEntry?.fileId ?? gcodeFiles[0]?.id ?? null,
  );
  const [selectedSpoolId, setSelectedSpoolId] = useState<number | null>(null);
  const [jobState, setJobState] = useState<string>(initialEntry?.state ?? "completed");
  const [startedAt, setStartedAt] = useState("");
  const [finishedAt, setFinishedAt] = useState("");
  const [jobError, setJobError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // Spoolman inventory — only surfaced when the integration is enabled.
  const spoolmanEnabled = useSpoolmanStatus().data?.enabled ?? false;
  const spools = useSpools({ enabled: spoolmanEnabled }).data ?? [];

  // Auto mode state
  const [importing, setImporting] = useState(false);
  const [importResults, setImportResults] = useState<{ filename: string; imported: boolean }[]>([]);
  const [importDone, setImportDone] = useState(false);

  function openAdd() {
    setShowAdd(true);
    setMode("manual");
    setSelectedPrinterId(null);
    setAdhocPrinter(false);
    setAdhocPrinterName("");
    setSelectedFileId(gcodeFiles[0]?.id ?? null);
    setSelectedSpoolId(null);
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
      const spool =
        selectedSpoolId !== null ? spools.find((s) => s.id === selectedSpoolId) : undefined;
      const job = await createManualPrintJob(modelId, {
        printer_id: adhocPrinter ? null : selectedPrinterId,
        printer_name: adhocPrinter ? adhocPrinterName.trim() : null,
        file_id: selectedFileId,
        state: jobState,
        spool_id: selectedSpoolId,
        spool_name: spool
          ? spool.filament_name ||
            spool.name ||
            uiText("Spool {value1}", { value1: String(spool.id) })
          : null,
        spool_filament_id: spool ? spool.filament_id : null,
        started_at: startedAt || null,
        finished_at: finishedAt || null,
        error: jobError || null,
      });
      onJobCreated(job);
      setShowAdd(false);
      toast.success(uiText("Print record added"));
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
      const results = await importPrintJobsFromPrinter(modelId, selectedPrinterId);
      setImportResults(results.map((r) => ({ filename: r.filename, imported: r.imported })));
      setImportDone(true);
      const imported = results.filter((r) => r.imported).length;
      if (imported > 0) {
        const refreshed = await getModelPrintJobs(modelId);
        refreshed.forEach((j) => onJobCreated(j));
        toast.success(
          uiText("jobs.imported", { value1: String(imported), count: Number(imported) }),
        );
      } else {
        toast.success(uiText("No new jobs to import"));
      }
    } catch (e) {
      toast.error(e);
    } finally {
      setImporting(false);
    }
  }

  return (
    <Localized>
      <section>
        <div className="flex items-center justify-between mb-4 pb-1 border-b border-outline-variant">
          <h2 className="text-lg font-semibold text-on-surface flex items-center gap-2">
            <Clock className="h-4 w-4" />
            {uiText(" Print History")}
          </h2>
          <button
            onClick={openAdd}
            className="inline-flex items-center gap-1.5 rounded border border-outline-variant px-2 py-1 text-xs font-medium text-on-surface-variant transition-colors hover:bg-surface-container-low"
          >
            <Plus className="h-3.5 w-3.5" />
            {uiText(" Add Record")}
          </button>
        </div>

        {/* Add record panel */}
        <Modal
          open={showAdd}
          onClose={() => {
            if (!submitting && !importing) setShowAdd(false);
          }}
          title={uiText("Log a print")}
          className="max-h-[calc(100dvh-2rem)] max-w-xl overflow-y-auto"
        >
          <div className="space-y-4">
            {/* Mode toggle */}
            <div className="flex gap-1">
              {PRINT_HISTORY_MODES.map((m) => (
                <button
                  key={m}
                  onClick={() => {
                    setMode(m);
                    setImportResults([]);
                    setImportDone(false);
                  }}
                  className={`px-3 py-1 text-xs font-medium rounded transition-colors ${
                    mode === m
                      ? "bg-accent text-accent-foreground"
                      : "text-on-surface-variant hover:bg-surface-container-high"
                  }`}
                >
                  {m === "manual" ? uiText("Manual Entry") : uiText("Auto from Printer")}
                </button>
              ))}
            </div>

            {mode === "manual" ? (
              <div className="space-y-2">
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  <div>
                    <label
                      className="block text-xs font-medium text-on-surface-variant mb-1"
                      htmlFor={`${formId}-1`}
                    >
                      {uiText("Printer")}
                    </label>
                    <select
                      id={`${formId}-1`}
                      value={adhocPrinter ? "__adhoc__" : (selectedPrinterId ?? "")}
                      onChange={(e) => {
                        const v = e.target.value;
                        if (v === "__adhoc__") {
                          setAdhocPrinter(true);
                          setSelectedPrinterId(null);
                        } else {
                          setAdhocPrinter(false);
                          setSelectedPrinterId(v ? Number(v) : null);
                        }
                      }}
                      className="min-w-0 w-full h-8 bg-surface text-on-surface text-sm border border-outline-variant rounded px-2 focus:outline-none focus:ring-1 focus:ring-primary"
                    >
                      <option value="">{uiText("Select printer…")}</option>
                      {printers.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}
                        </option>
                      ))}
                      <option value="__adhoc__">{uiText("Other (not listed)…")}</option>
                    </select>
                  </div>
                  <div>
                    <label
                      className="block text-xs font-medium text-on-surface-variant mb-1"
                      htmlFor={`${formId}-2`}
                    >
                      {uiText("G-code Revision")}
                    </label>
                    <select
                      id={`${formId}-2`}
                      value={selectedFileId ?? ""}
                      onChange={(e) =>
                        setSelectedFileId(e.target.value ? Number(e.target.value) : null)
                      }
                      className="min-w-0 w-full h-8 bg-surface text-on-surface text-sm border border-outline-variant rounded px-2 focus:outline-none focus:ring-1 focus:ring-primary"
                    >
                      <option value="">{uiText("Select revision…")}</option>
                      {gcodeFiles.map((f, i) => (
                        <option key={f.id} value={f.id}>
                          {uiText("Rev ")}
                          {i + 1} — {f.original_filename}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
                {adhocPrinter && (
                  <div>
                    <label
                      className="block text-xs font-medium text-on-surface-variant mb-1"
                      htmlFor={`${formId}-3`}
                    >
                      {uiText("Printer name")}
                    </label>
                    <input
                      id={`${formId}-3`}
                      value={adhocPrinterName}
                      onChange={(e) => setAdhocPrinterName(e.target.value)}
                      maxLength={128}
                      placeholder={uiText("e.g. Garage Prusa MK4")}
                      className="min-w-0 w-full h-8 bg-surface text-on-surface text-sm border border-outline-variant rounded px-2 focus:outline-none focus:ring-1 focus:ring-primary"
                    />
                  </div>
                )}
                <div>
                  <label
                    className="block text-xs font-medium text-on-surface-variant mb-1"
                    htmlFor={`${formId}-4`}
                  >
                    {uiText("Result")}
                  </label>
                  <select
                    id={`${formId}-4`}
                    value={jobState}
                    onChange={(e) => setJobState(e.target.value)}
                    className="min-w-0 w-full h-8 bg-surface text-on-surface text-sm border border-outline-variant rounded px-2 focus:outline-none focus:ring-1 focus:ring-primary"
                  >
                    <option value="completed">{uiText("Completed")}</option>
                    <option value="failed">{uiText("Failed")}</option>
                    <option value="cancelled">{uiText("Cancelled")}</option>
                  </select>
                </div>
                {spoolmanEnabled && spools.length > 0 && (
                  <div>
                    <label
                      className="block text-xs font-medium text-on-surface-variant mb-1"
                      htmlFor={`${formId}-5`}
                    >
                      {uiText("Spool (opt.)")}
                    </label>
                    <select
                      id={`${formId}-5`}
                      value={selectedSpoolId ?? ""}
                      onChange={(e) =>
                        setSelectedSpoolId(e.target.value ? Number(e.target.value) : null)
                      }
                      className="min-w-0 w-full h-8 bg-surface text-on-surface text-sm border border-outline-variant rounded px-2 focus:outline-none focus:ring-1 focus:ring-primary"
                    >
                      <option value="">{uiText("No spool")}</option>
                      {spools.map((s) => (
                        <option key={s.id} value={s.id}>
                          {(s.filament_name ||
                            s.name ||
                            uiText("Spool {value1}", { value1: String(s.id) })) +
                            (s.vendor_name ? ` · ${s.vendor_name}` : "") +
                            (s.remaining_weight != null
                              ? uiText(" ({value1} left)", {
                                  value1: String(formatGrams(s.remaining_weight)),
                                })
                              : "")}
                        </option>
                      ))}
                    </select>
                  </div>
                )}
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  <div>
                    <label
                      className="block text-xs font-medium text-on-surface-variant mb-1"
                      htmlFor={`${formId}-6`}
                    >
                      {uiText("Started (opt.)")}
                    </label>
                    <input
                      id={`${formId}-6`}
                      type="datetime-local"
                      value={startedAt}
                      onChange={(e) => setStartedAt(e.target.value)}
                      className="min-w-0 w-full h-8 bg-surface text-on-surface text-sm border border-outline-variant rounded px-2 focus:outline-none focus:ring-1 focus:ring-primary"
                    />
                  </div>
                  <div>
                    <label
                      className="block text-xs font-medium text-on-surface-variant mb-1"
                      htmlFor={`${formId}-7`}
                    >
                      {uiText("Finished (opt.)")}
                    </label>
                    <input
                      id={`${formId}-7`}
                      type="datetime-local"
                      value={finishedAt}
                      onChange={(e) => setFinishedAt(e.target.value)}
                      className="min-w-0 w-full h-8 bg-surface text-on-surface text-sm border border-outline-variant rounded px-2 focus:outline-none focus:ring-1 focus:ring-primary"
                    />
                  </div>
                </div>
                {jobState === "failed" && (
                  <div>
                    <label
                      className="block text-xs font-medium text-on-surface-variant mb-1"
                      htmlFor={`${formId}-8`}
                    >
                      {uiText("Error (opt.)")}
                    </label>
                    <input
                      id={`${formId}-8`}
                      value={jobError}
                      onChange={(e) => setJobError(e.target.value)}
                      placeholder={uiText("Describe what went wrong…")}
                      className="min-w-0 w-full h-8 bg-surface text-on-surface text-sm border border-outline-variant rounded px-2 focus:outline-none focus:ring-1 focus:ring-primary"
                    />
                  </div>
                )}
                <div className="flex gap-2 pt-1">
                  <button
                    onClick={submitManual}
                    disabled={submitting || !manualPrinterReady || !selectedFileId}
                    className="flex-1 h-8 bg-primary text-primary-foreground text-sm uppercase tracking-wider rounded disabled:opacity-50 hover:opacity-90 transition-opacity flex items-center justify-center gap-1.5"
                  >
                    {submitting ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Check className="h-3.5 w-3.5" />
                    )}
                    {uiText("Save")}
                  </button>
                  <button
                    onClick={() => setShowAdd(false)}
                    className="px-3 h-8 border border-outline-variant rounded text-sm text-on-surface-variant hover:bg-surface-container-high transition-colors"
                  >
                    {uiText("Cancel")}
                  </button>
                </div>
              </div>
            ) : (
              <div className="space-y-2">
                <p className="text-xs text-on-surface-variant">
                  {uiText(
                    "Fetch recent print history from a Moonraker printer and import jobs matching this model's G-code files.",
                  )}
                </p>
                <div>
                  <label
                    className="block text-xs font-medium text-on-surface-variant mb-1"
                    htmlFor={`${formId}-9`}
                  >
                    {uiText("Printer")}
                  </label>
                  <select
                    id={`${formId}-9`}
                    value={selectedPrinterId ?? ""}
                    onChange={(e) => {
                      setSelectedPrinterId(e.target.value ? Number(e.target.value) : null);
                      setImportResults([]);
                      setImportDone(false);
                    }}
                    className="min-w-0 w-full h-8 bg-surface text-on-surface text-sm border border-outline-variant rounded px-2 focus:outline-none focus:ring-1 focus:ring-primary"
                  >
                    <option value="">{uiText("Select printer…")}</option>
                    {printers.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                </div>
                {importDone && importResults.length > 0 && (
                  <div className="space-y-1">
                    {importResults.map((r) => (
                      <div key={r.filename} className="flex items-center gap-2 text-xs">
                        {r.imported ? (
                          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 shrink-0" />
                        ) : (
                          <XCircle className="h-3.5 w-3.5 text-on-surface-variant shrink-0" />
                        )}
                        <span
                          className={r.imported ? "text-on-surface" : "text-on-surface-variant"}
                        >
                          {r.filename}
                        </span>
                        <span className="opacity-50">
                          {r.imported ? uiText("imported") : uiText("already exists")}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
                {importDone && importResults.length === 0 && (
                  <p className="text-xs text-on-surface-variant">
                    {uiText("No matching jobs found on this printer.")}
                  </p>
                )}
                <div className="flex gap-2 pt-1">
                  <button
                    onClick={runAutoImport}
                    disabled={importing || !selectedPrinterId}
                    className="flex-1 h-8 bg-primary text-primary-foreground text-sm uppercase tracking-wider rounded disabled:opacity-50 hover:opacity-90 transition-opacity flex items-center justify-center gap-1.5"
                  >
                    {importing ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <RefreshCw className="h-3.5 w-3.5" />
                    )}
                    {uiText("Fetch & Import")}
                  </button>
                  <button
                    onClick={() => setShowAdd(false)}
                    className="px-3 h-8 border border-outline-variant rounded text-sm text-on-surface-variant hover:bg-surface-container-high transition-colors"
                  >
                    {uiText("Close")}
                  </button>
                </div>
              </div>
            )}
          </div>
        </Modal>

        {jobs.length === 0 ? (
          <p className="text-sm text-on-surface-variant">
            {uiText("No print history yet. Add a record manually or import from a printer.")}
          </p>
        ) : (
          <div className="space-y-2">
            {jobs.map((job) => {
              const present = PRINT_JOB_PRESENTATION[job.state];
              const Icon =
                present.tone === "success"
                  ? CheckCircle2
                  : present.tone === "error"
                    ? XCircle
                    : Clock;
              return (
                <div
                  key={job.id}
                  className="p-3 border border-outline-variant rounded bg-surface space-y-1"
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2 min-w-0">
                      <Icon
                        className={`h-4 w-4 shrink-0 ${
                          present.tone === "success"
                            ? "text-emerald-600"
                            : present.tone === "error"
                              ? "text-error"
                              : "text-amber-600"
                        }`}
                      />
                      <span className="font-mono text-[13px] text-on-surface truncate">
                        {job.source === "external"
                          ? printJobArtifactLabel(job)
                          : uiText("Rev {value1}", {
                              value1: String(job.gcode_revision_number ?? "—"),
                            })}{" "}
                        · {job.printer_name}
                      </span>
                    </div>
                    <span
                      className={`shrink-0 border rounded px-1.5 py-0.5 text-xs font-medium ${printJobToneClass(present.tone)}`}
                    >
                      {present.label}
                    </span>
                  </div>
                  <p className="text-xs text-on-surface-variant">
                    {job.material_type ? `${job.material_type} · ` : ""}
                    {timeAgo(job.created_at)}
                  </p>
                  {job.source === "external" && (
                    <p className="text-xs uppercase tracking-wider text-on-surface-variant">
                      {knownUiText(job.artifact_evidence)}
                    </p>
                  )}
                  <PrintJobReproducibility job={job} previewHref={`/models/${modelId}`} />
                  {(job.actual_duration_s != null || job.filament_used_g != null) && (
                    <p className="text-xs text-on-surface-variant">
                      <span className="text-emerald-600">{uiText("measured")}</span>
                      {job.actual_duration_s != null
                        ? ` · ${formatDuration(job.actual_duration_s)}`
                        : ""}
                      {job.filament_used_g != null ? ` · ${formatGrams(job.filament_used_g)}` : ""}
                      {job.filament_cost != null ? ` · ${formatCost(job.filament_cost)}` : ""}
                    </p>
                  )}
                  {job.spool_name && (
                    <p className="text-xs text-on-surface-variant">
                      {uiText("spool · {value1}", { value1: String(job.spool_name ?? "") })}
                    </p>
                  )}
                  {job.error && <p className="text-xs text-error break-words">{job.error}</p>}
                </div>
              );
            })}
          </div>
        )}
      </section>
    </Localized>
  );
}
