"use client";

import { knownUiText } from "@/lib/locale";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { useId, useState } from "react";
import { CheckCircle2, XCircle } from "lucide-react";

import { createManualPrintJob, getModelPrintJobs, importPrintJobsFromPrinter } from "@/lib/api";
import { usePrinters, useSpoolmanStatus, useSpools } from "@/lib/queries";
import { formatCost, formatDuration, formatGrams, timeAgo } from "@/lib/format";
import { toast } from "@/lib/toast";
import { FileRead, ModelPrintJobRead } from "@/types";
import { PrintJobReproducibility } from "@/components/print-job-reproducibility";
import { printJobArtifactLabel } from "@/lib/print-job-reproducibility";

import { PRINT_JOB_PRESENTATION } from "./presentation";
import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import { ChoiceField } from "./choice-field";
import { Localized } from "@/components/ui/localized";

type PrintHistoryMode = "manual" | "auto";

export function PrintHistorySection({
  jobs,
  modelName,
  modelId,
  gcodeFiles,
  onJobCreated,
  initialEntry,
}: {
  jobs: ModelPrintJobRead[];
  modelName?: string;
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
    if (submitting || !manualPrinterReady || !selectedFileId) return;
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
        <div className="detail-history-heading">
          <h2>{uiText("Print history")}</h2>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" onClick={openAdd}>
              {uiText("Add record")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                openAdd();
                setMode("auto");
              }}
            >
              {uiText("Import from printer")}
            </Button>
          </div>
        </div>
        <p className="detail-history-intro">
          {uiText("Recorded results for each printer and revision.")}
        </p>

        {/* Add record panel */}
        <Modal
          open={showAdd}
          onClose={() => {
            if (!submitting && !importing) setShowAdd(false);
          }}
          title={mode === "manual" ? uiText("Add print record") : uiText("Import from printer")}
          description={
            mode === "manual"
              ? uiText(
                  "Record an observed result for one Revision on one printer. This does not change the recommended Revision.",
                )
              : undefined
          }
          className={
            mode === "manual"
              ? "absolute inset-y-0 right-0 h-dvh max-h-none max-w-[680px] rounded-none"
              : "max-w-160"
          }
          bodyClassName={mode === "manual" ? "flex-1" : undefined}
          footer={
            <>
              <Button
                variant="outline"
                disabled={submitting || importing}
                onClick={() => setShowAdd(false)}
              >
                {mode === "manual" ? uiText("Cancel") : uiText("Close")}
              </Button>
              {mode === "manual" ? (
                <Button
                  onClick={submitManual}
                  loading={submitting}
                  disabled={!manualPrinterReady || !selectedFileId}
                >
                  {uiText("Save record")}
                </Button>
              ) : (
                <Button onClick={runAutoImport} loading={importing} disabled={!selectedPrinterId}>
                  {uiText("Fetch & Import")}
                </Button>
              )}
            </>
          }
        >
          <div
            className="space-y-4"
            onKeyDown={(event) => {
              if (
                event.defaultPrevented ||
                event.nativeEvent.isComposing ||
                event.repeat ||
                event.altKey ||
                event.shiftKey ||
                !(event.ctrlKey || event.metaKey) ||
                event.key !== "Enter" ||
                mode !== "manual"
              )
                return;
              event.preventDefault();
              void submitManual();
            }}
          >
            {mode === "manual" ? (
              <div className="space-y-4">
                {modelName && (
                  <dl className="grid grid-cols-[1fr_2fr] gap-x-4 gap-y-2 bg-plate p-4 text-[13px]">
                    <dt className="text-muted-foreground">{uiText("Model")}</dt>
                    <dd>{modelName}</dd>
                    <dt className="text-muted-foreground">{uiText("Revision")}</dt>
                    <dd>
                      {selectedFileId != null
                        ? `${uiText("Rev ")}${gcodeFiles.find((file) => file.id === selectedFileId)?.gcode_revision_number ?? gcodeFiles.find((file) => file.id === selectedFileId)?.version ?? "—"}`
                        : "—"}
                    </dd>
                  </dl>
                )}
                <h3 className="text-sm font-semibold">{uiText("Print context")}</h3>
                <div className="space-y-4">
                  <ChoiceField<number | "other">
                    label={uiText("Printer")}
                    inline
                    value={adhocPrinter ? "other" : selectedPrinterId}
                    options={[
                      ...printers.map((printer) => ({ value: printer.id, label: printer.name })),
                      { value: "other", label: uiText("Other (not listed)…") },
                    ]}
                    onChange={(value) => {
                      setAdhocPrinter(value === "other");
                      setSelectedPrinterId(value === "other" ? null : value);
                    }}
                  />
                  <ChoiceField
                    label={uiText("G-code Revision")}
                    inline
                    value={selectedFileId}
                    options={gcodeFiles.map((file) => ({
                      value: file.id,
                      label: `${uiText("Rev ")}${file.gcode_revision_number ?? file.version}`,
                      description: file.original_filename,
                    }))}
                    onChange={setSelectedFileId}
                  />
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
                      className="min-w-0 w-full h-10 bg-card text-foreground text-sm border border-input rounded px-2 focus:outline-none focus:ring-1 focus:ring-primary"
                    />
                  </div>
                )}
                {spoolmanEnabled && spools.length > 0 && (
                  <ChoiceField<number | "none">
                    label={uiText("Spool (opt.)")}
                    inline
                    value={selectedSpoolId ?? "none"}
                    onChange={(value) => setSelectedSpoolId(value === "none" ? null : value)}
                    options={[
                      { value: "none", label: uiText("No spool") },
                      ...spools.map((spool) => ({
                        value: spool.id,
                        label:
                          spool.filament_name ||
                          spool.name ||
                          uiText("Spool {value1}", { value1: String(spool.id) }),
                        description: [
                          spool.vendor_name,
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
                <h3 className="border-t border-border pt-5 text-sm font-semibold">
                  {uiText("Observed outcome")}
                </h3>
                <ChoiceField
                  label={uiText("Result")}
                  value={jobState}
                  options={[
                    { value: "completed", label: uiText("Completed") },
                    { value: "failed", label: uiText("Failed") },
                    { value: "cancelled", label: uiText("Cancelled") },
                  ]}
                  onChange={setJobState}
                  inline
                />
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
                      className="min-w-0 w-full h-10 bg-card text-foreground text-sm border border-input rounded px-2 focus:outline-none focus:ring-1 focus:ring-primary"
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
                      className="min-w-0 w-full h-10 bg-card text-foreground text-sm border border-input rounded px-2 focus:outline-none focus:ring-1 focus:ring-primary"
                    />
                  </div>
                </div>
                {(jobState === "failed" || jobState === "cancelled") && (
                  <div>
                    <label
                      className="block text-xs font-medium text-on-surface-variant mb-1"
                      htmlFor={`${formId}-8`}
                    >
                      {uiText("Error (opt.)")}
                    </label>
                    <input
                      id={`${formId}-8`}
                      ref={(node) => {
                        if (initialEntry?.state === "failed") node?.setAttribute("autofocus", "");
                      }}
                      value={jobError}
                      onChange={(e) => setJobError(e.target.value)}
                      placeholder={uiText("Describe what went wrong…")}
                      className="min-w-0 w-full h-10 bg-card text-foreground text-sm border border-input rounded px-2 focus:outline-none focus:ring-1 focus:ring-primary"
                    />
                  </div>
                )}
              </div>
            ) : (
              <div className="space-y-2">
                <p className="text-xs text-on-surface-variant">
                  {uiText(
                    "Fetch recent print history from a Moonraker printer and import jobs matching this model's G-code files.",
                  )}
                </p>
                <ChoiceField
                  label={uiText("Printer")}
                  value={selectedPrinterId}
                  options={printers.map((printer) => ({ value: printer.id, label: printer.name }))}
                  onChange={(value) => {
                    setSelectedPrinterId(value);
                    setImportResults([]);
                    setImportDone(false);
                  }}
                />
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
              </div>
            )}
          </div>
        </Modal>

        {jobs.length === 0 ? (
          <p className="text-sm text-on-surface-variant">
            {uiText("No print history yet. Add a record manually or import from a printer.")}
          </p>
        ) : (
          <div>
            {jobs.map((job) => {
              const present = PRINT_JOB_PRESENTATION[job.state];
              return (
                <article key={job.id} className="detail-history-record">
                  <div className="detail-history-identity">
                    <span className="break-words">
                      {job.source === "external"
                        ? printJobArtifactLabel(job)
                        : uiText("Rev {value1}", {
                            value1: String(job.gcode_revision_number ?? "—"),
                          })}
                      {" · "}
                      {job.printer_name}
                      {" · "}
                      <span
                        className={
                          present.tone === "error" ? "text-destructive" : "text-foreground"
                        }
                      >
                        {present.label}
                      </span>
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
                  <PrintJobReproducibility
                    job={job}
                    previewHref={`/models/${modelId}`}
                    presentation="history"
                  />
                  {(job.actual_duration_s != null || job.filament_used_g != null) && (
                    <p className="text-xs text-on-surface-variant">
                      <span>{uiText("measured")}</span>
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
                </article>
              );
            })}
          </div>
        )}
      </section>
    </Localized>
  );
}
