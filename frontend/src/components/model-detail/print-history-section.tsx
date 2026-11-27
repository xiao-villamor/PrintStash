"use client";

import { knownUiText } from "@/lib/locale";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { useId, useState } from "react";
import { CheckCircle2, CircleAlert, XCircle } from "lucide-react";

import { createManualPrintJob, getModelPrintJobs, importPrintJobsFromPrinter } from "@/lib/api";
import { usePrinters, useSpoolmanStatus, useSpools } from "@/lib/queries";
import { formatCost, formatGrams, timeAgo } from "@/lib/format";
import { toast } from "@/lib/toast";
import { FileRead, ModelPrintJobRead } from "@/types";
import { PrintJobReproducibility } from "@/components/print-job-reproducibility";
import { PrintJobCost } from "@/components/print-job-cost";
import { printJobArtifactLabel } from "@/lib/print-job-reproducibility";

import { formatPlateEstimate } from "./format-plate-estimate";
import { PRINT_JOB_PRESENTATION } from "./presentation";
import { Modal } from "@/components/ui/modal";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { FormField } from "@/components/ui/form-field";
import { Input, inputClasses } from "@/components/ui/input";
import { OptionPicker } from "@/components/ui/option-picker";
import { Localized } from "@/components/ui/localized";

type PrintHistoryMode = "manual" | "auto";
type RecordOutcome = "completed" | "failed" | "cancelled";

const RECORD_OUTCOMES: readonly { value: RecordOutcome; label: string }[] = [
  {
    value: "completed",
    get label() {
      return uiText("Success");
    },
  },
  {
    value: "failed",
    get label() {
      return uiText("Failed");
    },
  },
  {
    value: "cancelled",
    get label() {
      return uiText("Cancelled");
    },
  },
];

const OUTCOME_DOT = {
  completed: "bg-success",
  failed: "bg-destructive",
  cancelled: "bg-warning",
} satisfies Record<RecordOutcome, string>;

export function PrintHistorySection({
  jobs,
  modelName,
  modelId,
  gcodeFiles,
  onJobCreated,
  initialEntry,
  revisionFilter = null,
  onRevisionFilter,
  onAddRevision,
  onOpenRevision,
  onViewActivity,
  onDraftClosed,
  showHistory = true,
}: {
  jobs: ModelPrintJobRead[];
  modelName?: string;
  modelId: number;
  gcodeFiles: FileRead[];
  onJobCreated: (job: ModelPrintJobRead) => void;
  initialEntry?: { fileId: number; state: "completed" | "failed" } | null;
  revisionFilter?: number | null;
  onRevisionFilter?: (fileId: number | null) => void;
  onAddRevision?: () => void;
  onOpenRevision?: (fileId: number) => void;
  onViewActivity?: () => void;
  onDraftClosed?: () => void;
  showHistory?: boolean;
}) {
  useUiLocale();
  const formId = useId();
  const [showAdd, setShowAdd] = useState(!!initialEntry);
  const [mode, setMode] = useState<PrintHistoryMode>("manual");

  // Manual form state
  const printersQuery = usePrinters();
  const printers = printersQuery.data ?? [];
  const importPrinters = printers.filter(
    (printer) => printer.provider === "moonraker" && printer.moonraker_url,
  );
  // `null` is "nothing selected"; the empty-string the <select> needs for that is
  // produced at the JSX boundary, so the ids stay numbers everywhere else.
  const [selectedPrinterId, setSelectedPrinterId] = useState<number | null>(null);
  // When the printer isn't a registered one, log against this free-text name.
  const [adhocPrinter, setAdhocPrinter] = useState(false);
  const [adhocPrinterName, setAdhocPrinterName] = useState("");
  const [selectedFileId, setSelectedFileId] = useState<number | null>(
    initialEntry?.fileId ?? revisionFilter ?? gcodeFiles[0]?.id ?? null,
  );
  const selectedRevision = gcodeFiles.find((file) => file.id === selectedFileId);
  const estimatedFilament = selectedRevision?.metadata?.filament_weight_g;
  const [selectedSpoolId, setSelectedSpoolId] = useState<number | null>(null);
  const [jobState, setJobState] = useState<RecordOutcome | null>(initialEntry?.state ?? null);
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
    setSelectedFileId(revisionFilter ?? gcodeFiles[0]?.id ?? null);
    setSelectedSpoolId(null);
    setJobState(null);
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

  // The first unmet requirement, in the order the dialog asks for them.
  const blockedReason =
    jobState === null
      ? uiText("Choose the observed outcome.")
      : !selectedFileId
        ? uiText("Choose revision")
        : !manualPrinterReady
          ? uiText("Choose the printer that ran it.")
          : null;

  function closeRecord() {
    setShowAdd(false);
    onDraftClosed?.();
  }

  async function submitManual() {
    if (submitting || blockedReason !== null || jobState === null || !selectedFileId) return;
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
        error: jobState === "completed" ? null : jobError.trim() || null,
      });
      onJobCreated(job);
      closeRecord();
      if (onViewActivity)
        toast.successAction(
          uiText("Print record added"),
          `${uiText("View activity")} ›`,
          onViewActivity,
        );
      else toast.success(uiText("Print record added"));
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

  const visibleJobs =
    revisionFilter === null ? jobs : jobs.filter((job) => job.file_id === revisionFilter);

  return (
    <Localized>
      <section>
        {showHistory && (
          <>
            <div className="flex h-8 items-center justify-between gap-3 px-0.5">
              <h2 className="text-sm font-medium text-muted-foreground">
                {uiText("Print history")} · {visibleJobs.length}
              </h2>
              {gcodeFiles.length > 0 ? (
                <div className="flex gap-1.5">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      openAdd();
                      setMode("auto");
                    }}
                  >
                    {uiText("Import from printer")}
                  </Button>
                  <Button size="sm" variant="outline" onClick={openAdd}>
                    {uiText("modelDetail.recordResult")}
                  </Button>
                </div>
              ) : (
                onAddRevision && (
                  <Button size="sm" onClick={onAddRevision}>
                    {uiText("Add revision")}
                  </Button>
                )
              )}
            </div>

            {onRevisionFilter && (gcodeFiles.length > 1 || revisionFilter !== null) && (
              <label className="flex flex-wrap items-center gap-2 text-sm">
                {uiText("G-code Revision")}
                <select
                  className={inputClasses}
                  value={revisionFilter ?? ""}
                  onChange={(event) => {
                    onRevisionFilter(
                      gcodeFiles.find((file) => file.id === Number(event.target.value))?.id ?? null,
                    );
                  }}
                >
                  <option value="">{uiText("modelDetail.allRevisions")}</option>
                  {gcodeFiles.map((file) => (
                    <option key={file.id} value={file.id}>
                      {uiText("Rev {value1}", {
                        value1: String(file.gcode_revision_number ?? file.version),
                      })}
                      {file.revision_label && ` · ${file.revision_label}`}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </>
        )}
        {/* Add record panel */}
        <Modal
          open={showAdd}
          onClose={() => {
            if (!submitting && !importing) closeRecord();
          }}
          title={
            mode === "manual" ? uiText("modelDetail.recordResult") : uiText("Import from printer")
          }
          description={mode === "manual" ? modelName : undefined}
          dismissible={!submitting && !importing}
          className={mode === "manual" ? "max-w-170" : undefined}
          bodyClassName={mode === "manual" ? "p-0" : undefined}
          footer={
            <>
              {mode === "manual" && blockedReason && (
                <p
                  id={`${formId}-reason`}
                  className="me-auto flex items-center gap-2 text-sm text-muted-foreground"
                >
                  <CircleAlert className="size-4 shrink-0 text-warning" aria-hidden />
                  {blockedReason}
                </p>
              )}
              <Button
                variant="outline"
                size="sm"
                disabled={submitting || importing}
                onClick={closeRecord}
              >
                {uiText("Cancel")}
              </Button>
              {mode === "manual" ? (
                <Button
                  size="sm"
                  onClick={submitManual}
                  loading={submitting}
                  disabled={blockedReason !== null}
                  aria-describedby={blockedReason ? `${formId}-reason` : undefined}
                >
                  {uiText("Save record")}
                </Button>
              ) : (
                <Button
                  size="sm"
                  onClick={runAutoImport}
                  loading={importing}
                  disabled={!selectedPrinterId}
                >
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
              <div className="space-y-5 px-6 py-5">
                <div className="flex justify-end">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setMode("auto");
                      setSelectedPrinterId(null);
                    }}
                  >
                    {uiText("Import from printer")}
                  </Button>
                </div>
                <fieldset className="min-w-0">
                  <legend className="mb-1.5 text-sm font-medium">
                    {uiText("Observed outcome")}
                  </legend>
                  <div className="grid grid-cols-3 gap-2">
                    {RECORD_OUTCOMES.map((outcome) => (
                      <label
                        key={outcome.value}
                        className="flex min-h-11 cursor-action items-center gap-3 rounded-lg border border-border bg-card px-3 text-sm hover:bg-muted has-checked:border-muted-foreground has-checked:bg-accent has-focus-visible:outline-2 has-focus-visible:outline-ring"
                      >
                        <input
                          type="radio"
                          name={`${formId}-outcome`}
                          className="size-4 shrink-0 accent-primary"
                          checked={jobState === outcome.value}
                          onChange={() => setJobState(outcome.value)}
                        />
                        {outcome.label}
                      </label>
                    ))}
                  </div>
                </fieldset>
                <div className="grid gap-3 md:grid-cols-2">
                  <OptionPicker
                    label={uiText("G-code Revision")}
                    searchPlaceholder={uiText("Search revisions")}
                    value={selectedFileId}
                    options={gcodeFiles.map((file) => ({
                      value: file.id,
                      label: `${uiText("Rev ")}${file.gcode_revision_number ?? file.version}${file.revision_label ? ` · ${file.revision_label}` : ""}`,
                      description: file.original_filename,
                    }))}
                    onChange={setSelectedFileId}
                  />
                  <OptionPicker
                    label={uiText("Printer")}
                    searchPlaceholder={uiText("Search printers")}
                    value={selectedPrinterId}
                    placeholder={uiText("Choose printer")}
                    options={printers.map((printer) => ({
                      value: printer.id,
                      label: printer.name,
                    }))}
                    onChange={(printerId) => {
                      setAdhocPrinter(false);
                      setSelectedPrinterId(printerId);
                    }}
                    other={{
                      label: uiText("Other (not listed)…"),
                      selected: adhocPrinter,
                      onSelect: () => {
                        setAdhocPrinter(true);
                        setSelectedPrinterId(null);
                      },
                    }}
                  />
                </div>
                {adhocPrinter && (
                  <div>
                    <label className="mb-1.5 block text-sm font-medium" htmlFor={`${formId}-3`}>
                      {uiText("Printer name")}
                    </label>
                    <Input
                      id={`${formId}-3`}
                      value={adhocPrinterName}
                      onChange={(e) => setAdhocPrinterName(e.target.value)}
                      maxLength={128}
                      placeholder={uiText("e.g. Garage Prusa MK4")}
                    />
                  </div>
                )}
                <div className="grid gap-3 md:grid-cols-2">
                  <FormField label={uiText("Started")} htmlFor={`${formId}-6`} optional>
                    <Input
                      id={`${formId}-6`}
                      type="datetime-local"
                      value={startedAt}
                      onChange={(e) => setStartedAt(e.target.value)}
                    />
                  </FormField>
                  <FormField label={uiText("Finished")} htmlFor={`${formId}-7`} optional>
                    <Input
                      id={`${formId}-7`}
                      type="datetime-local"
                      value={finishedAt}
                      onChange={(e) => setFinishedAt(e.target.value)}
                    />
                  </FormField>
                </div>
                {estimatedFilament != null && (
                  <p className="text-sm text-muted-foreground">
                    {uiText("Filament estimate")} ·{" "}
                    {formatPlateEstimate("filament_weight_g", estimatedFilament)} ·{" "}
                    {uiText("G-code estimate")}
                  </p>
                )}
                {spoolmanEnabled && spools.length > 0 && (
                  <OptionPicker<number | "none">
                    label={`${uiText("Spool")} ${uiText("(optional)")}`}
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
                {(jobState === "failed" || jobState === "cancelled") && (
                  <div>
                    <label className="mb-1.5 block text-sm font-medium" htmlFor={`${formId}-8`}>
                      {uiText("What went wrong")}
                    </label>
                    <textarea
                      id={`${formId}-8`}
                      rows={3}
                      maxLength={1024}
                      className={`${inputClasses} h-auto resize-none py-2`}
                      ref={(node) => {
                        if (initialEntry?.state === "failed") node?.setAttribute("autofocus", "");
                      }}
                      value={jobError}
                      onChange={(e) => setJobError(e.target.value)}
                      placeholder={uiText("Describe what went wrong…")}
                    />
                    <p className="mt-1.5 text-sm text-muted-foreground">
                      {uiText("Saved with the record and shown in Activity.")}
                    </p>
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
                {printersQuery.isPending ? (
                  <p role="status">{uiText("Loading printers…")}</p>
                ) : printersQuery.isError ? (
                  <div role="alert" className="space-y-2">
                    <p>{uiText("Couldn’t load printers.")}</p>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => void printersQuery.refetch()}
                    >
                      {uiText("Retry")}
                    </Button>
                  </div>
                ) : importPrinters.length === 0 ? (
                  <div className="space-y-3 border-t border-border pt-3">
                    <p className="text-sm">
                      {uiText("No Moonraker printer is available to import history.")}
                    </p>
                    <Button variant="outline" size="sm" onClick={openAdd}>
                      {uiText("Add record manually")}
                    </Button>
                  </div>
                ) : (
                  <OptionPicker
                    label={uiText("Printer")}
                    searchPlaceholder={uiText("Search printers")}
                    placeholder={uiText("Choose printer")}
                    value={selectedPrinterId}
                    options={importPrinters.map((printer) => ({
                      value: printer.id,
                      label: printer.name,
                    }))}
                    onChange={(value) => {
                      setSelectedPrinterId(value);
                      setImportResults([]);
                      setImportDone(false);
                    }}
                  />
                )}
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

        {showHistory && (
          <div className="mt-2 overflow-hidden rounded-lg border border-border bg-card">
            {visibleJobs.length === 0 ? (
              <div className="space-y-2.5 px-3.5 py-4 text-sm/5">
                <p>
                  {gcodeFiles.length === 0
                    ? uiText("modelDetail.historyNeedsRevision")
                    : revisionFilter === null
                      ? uiText("No print history yet.")
                      : uiText("modelDetail.noRevisionActivity")}
                </p>
                {gcodeFiles.length > 0 && revisionFilter === null && (
                  <p className="text-muted-foreground">
                    {uiText(
                      "Record a result after each print, or import past jobs from a connected printer.",
                    )}
                  </p>
                )}
              </div>
            ) : (
              visibleJobs.map((job) => {
                const revision = gcodeFiles.find((file) => file.id === job.file_id);
                const present = PRINT_JOB_PRESENTATION[job.state];
                const outcomeDot =
                  job.state === "completed" || job.state === "failed" || job.state === "cancelled"
                    ? OUTCOME_DOT[job.state]
                    : "bg-muted-foreground";
                return (
                  <article
                    key={job.id}
                    className="flex items-start gap-3 border-t border-border px-3.5 py-3 text-sm first:border-t-0"
                  >
                    <Badge variant="outline" className="min-w-24 gap-1.5 text-sm">
                      <span aria-hidden className={`size-2 shrink-0 rounded-full ${outcomeDot}`} />
                      {job.state === "completed" ? uiText("Success") : present.label}
                    </Badge>
                    <div className="flex min-w-0 flex-1 flex-col gap-1">
                      <div className="flex flex-wrap items-baseline justify-between gap-2">
                        <span className="break-words">
                          {job.source === "external"
                            ? printJobArtifactLabel(job)
                            : uiText("Rev {value1}", {
                                value1: String(job.gcode_revision_number ?? "—"),
                              })}
                          {" · "}
                          {job.printer_name}
                        </span>
                        {revision && onOpenRevision && (
                          <Button
                            size="xs"
                            variant="ghost"
                            onClick={() => onOpenRevision(revision.id)}
                          >
                            {uiText("modelDetail.viewRevision")}
                          </Button>
                        )}
                      </div>
                      <p className="text-sm text-muted-foreground tabular-nums">
                        {timeAgo(job.finished_at ?? job.started_at ?? job.created_at)}
                        {job.material_type ? ` · ${job.material_type}` : ""}
                        {job.actual_duration_s != null
                          ? ` · ${formatPlateEstimate("estimated_time_s", job.actual_duration_s)}`
                          : ""}
                        {job.filament_used_g != null
                          ? ` · ${formatPlateEstimate("filament_weight_g", job.filament_used_g)}`
                          : ""}
                        {job.filament_cost != null && job.production_cost == null
                          ? ` · ${formatCost(job.filament_cost)}`
                          : ""}
                      </p>
                      {job.source === "external" && (
                        <p className="text-xs uppercase tracking-wider text-muted-foreground">
                          {knownUiText(job.artifact_evidence)}
                        </p>
                      )}
                      <PrintJobReproducibility
                        job={job}
                        previewHref={`/models/${modelId}`}
                        presentation="history"
                      />
                      {job.production_cost && <PrintJobCost cost={job.production_cost} />}
                      {job.spool_name && (
                        <p className="text-sm text-muted-foreground">
                          {uiText("spool · {value1}", { value1: String(job.spool_name ?? "") })}
                        </p>
                      )}
                      {job.error && <p className="break-words text-sm text-error">{job.error}</p>}
                    </div>
                  </article>
                );
              })
            )}
          </div>
        )}
      </section>
    </Localized>
  );
}
