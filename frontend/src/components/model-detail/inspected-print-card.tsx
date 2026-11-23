import { RevisionDeliveryActions } from "./revision-delivery-actions";
import { Check, ChevronRight, Star, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { downloadAuthenticatedFile } from "@/lib/api";
import {
  formatBytes,
  formatCost,
  formatDuration,
  formatGrams,
  formatMillimeters,
} from "@/lib/format";
import { currentLocale, uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { toast } from "@/lib/toast";
import type { FileRead, ModelPrintJobRead } from "@/types";
import { HashValue } from "./file-hashes";
import type { GcodePlates } from "@/lib/api/plates";
import { OverviewPlateEstimates } from "./overview-plate-estimates";
import { PRINT_JOB_PRESENTATION, revisionStatusLabel } from "./presentation";

type RecordedPrint = Pick<ModelPrintJobRead, "file_id" | "printer_name" | "state">;

/** The inspected artifact's setup, estimates and recorded evidence stay separate. */
export function InspectedPrintCard({
  file,
  canEdit,
  printerless = false,
  canSend,
  revisionCount,
  recommended,
  jobs,
  onRevisions,
  onSettings,
  onHistory,
  onLogPrint,
  onSend,
  onInspect,
  onAddRevision,
  onCompare,
  plateEstimates,
  selectedSlicedPlate = null,
}: {
  file: FileRead | null;
  canEdit: boolean;
  printerless?: boolean;
  canSend: boolean;
  revisionCount: number;
  recommended: FileRead | null;
  jobs: readonly RecordedPrint[] | null;
  onRevisions: () => void;
  onSettings: () => void;
  onHistory: () => void;
  onLogPrint: (state: "completed" | "failed") => void;
  onSend: (mode: "send" | "queue") => void;
  onInspect: (id: number) => void;
  onAddRevision: () => void;
  onCompare: () => void;
  plateEstimates?: GcodePlates | null;
  selectedSlicedPlate?: number | null;
}) {
  useUiLocale();
  if (!file)
    return (
      <section className="detail-overview flex flex-wrap items-center justify-between gap-2 border-t border-border pt-4">
        <h2>{uiText("No G-code revision yet")}</h2>
        {canEdit && (
          <Button size="sm" onClick={onAddRevision}>
            {uiText("Add revision")}
          </Button>
        )}
      </section>
    );
  const meta = file.metadata;
  const groups = new Map<string, RecordedPrint & { count: number }>();
  for (const job of jobs ?? []) {
    if (job.file_id !== file.id) continue;
    const key = JSON.stringify([job.printer_name, job.state]);
    const group = groups.get(key);
    if (group) group.count += 1;
    else groups.set(key, { ...job, count: 1 });
  }
  const fact = (label: string, value: React.ReactNode) => (
    <div key={label}>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
  return (
    <section className="detail-overview">
      <header className="detail-revision-identity">
        <div className="detail-section-heading flex-wrap">
          <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
            <h2>
              {uiText("G-code revision {number}", {
                number: String(file.gcode_revision_number ?? file.version),
              })}
            </h2>
            {file.revision_status && (
              <span className="rounded-sm bg-accent px-2 py-0.5 text-xs font-medium text-accent-foreground">
                <span className="sr-only">{uiText("Revision test status")} · </span>
                {revisionStatusLabel(file.revision_status)}
              </span>
            )}
            {file.is_recommended ? (
              <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                <Star className="size-3" aria-hidden="true" />
                {uiText("Recommended")}
              </span>
            ) : (
              recommended && (
                <Button size="sm" variant="ghost" onClick={() => onInspect(recommended.id)}>
                  {uiText("Return to recommended revision")}
                </Button>
              )
            )}
          </div>
          <Button size="sm" variant="ghost" onClick={onRevisions}>
            {uiText("All revisions ({count})", { count: String(revisionCount) })}
          </Button>
        </div>
        {file.revision_label && (
          <p className="break-words text-sm font-medium">{file.revision_label}</p>
        )}
        <p className="detail-artifact-filename">{file.original_filename}</p>
      </header>
      {file.revision_notes && <p className="detail-revision-note">{file.revision_notes}</p>}
      <section className="detail-overview-block">
        <div className="detail-section-heading">
          <h3>{uiText("Print setup")}</h3>
          <Button size="sm" variant="ghost" onClick={onSettings}>
            {uiText("Full print settings")}
          </Button>
        </div>
        <dl className="detail-overview-facts">
          {fact(uiText("Printer profile"), meta?.printer_model ?? uiText("Not recorded"))}
          {fact(uiText("Material"), meta?.material_type ?? uiText("Not recorded"))}
          {fact(uiText("Layer height"), formatMillimeters(meta?.layer_height_mm))}
          {fact(uiText("Nozzle"), formatMillimeters(meta?.nozzle_diameter_mm))}
        </dl>
      </section>
      <section className="detail-overview-block">
        {plateEstimates && plateEstimates.plates.length > 1 ? (
          <OverviewPlateEstimates data={plateEstimates} selected={selectedSlicedPlate} />
        ) : (
          <>
            <h3>{uiText("Print estimate")}</h3>
            <dl className="detail-overview-facts detail-estimates">
              {fact(uiText("Time"), formatDuration(meta?.estimated_time_s ?? null))}
              {fact(uiText("Filament per copy"), formatGrams(meta?.filament_weight_g))}
              {fact(uiText("Cost"), formatCost(meta?.filament_cost))}
            </dl>
          </>
        )}
      </section>
      <div className="detail-revision-actions">
        <div className="flex flex-wrap gap-2">
          {canSend && (
            <RevisionDeliveryActions printerless={printerless} onSend={onSend} size="sm" />
          )}
          <Button
            size="sm"
            variant="ghost"
            onClick={() =>
              void downloadAuthenticatedFile(
                `/api/v1/files/${file.id}/download`,
                file.original_filename,
              ).catch((error) => toast.error(error))
            }
          >
            {uiText("Download")}
          </Button>
        </div>
        <Button size="sm" variant="ghost" data-compare-origin onClick={onCompare}>
          {uiText("Compare revisions")}
        </Button>
      </div>
      <section className="detail-overview-block" aria-label={uiText("Recorded prints")}>
        <div className="detail-section-heading">
          <h3>{uiText("Recorded prints")}</h3>
          {canSend && (
            <Button size="sm" variant="ghost" onClick={onHistory}>
              {uiText("View history")}
            </Button>
          )}
        </div>
        {jobs === null ? (
          <p className="detail-empty-evidence">{uiText("Not recorded")}</p>
        ) : groups.size === 0 ? (
          <p className="detail-empty-evidence">{uiText("No prints recorded for this revision.")}</p>
        ) : (
          <table className="detail-print-results">
            <thead>
              <tr>
                <th>{uiText("Printer")}</th>
                <th>{uiText("Result")}</th>
                <th>{uiText("Prints")}</th>
              </tr>
            </thead>
            <tbody>
              {[...groups.entries()].map(([key, group]) => (
                <tr key={key}>
                  <td>{group.printer_name}</td>
                  <td className={group.state === "failed" ? "text-destructive" : undefined}>
                    {group.state === "completed"
                      ? uiText("Successful")
                      : PRINT_JOB_PRESENTATION[group.state].label}
                  </td>
                  <td>{group.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {canSend && (
          <div className="detail-log-print">
            <span>{uiText("Log a print")}</span>
            <Button size="sm" variant="outline" onClick={() => onLogPrint("completed")}>
              <Check className="h-3 w-3" />
              {uiText("Successful")}
            </Button>
            <Button size="sm" variant="outline" onClick={() => onLogPrint("failed")}>
              <X className="h-3 w-3" />
              {uiText("Failed")}
            </Button>
          </div>
        )}
      </section>
      <details className="detail-overview-block group">
        <summary className="flex w-fit items-center gap-2 text-sm font-medium focus-visible:outline-ring">
          <ChevronRight className="size-4 group-open:rotate-90" aria-hidden="true" />
          {uiText("G-code file")}
        </summary>
        <dl className="detail-overview-facts">
          {fact(
            uiText("Added"),
            new Date(file.uploaded_at).toLocaleDateString(currentLocale(), {
              month: "short",
              day: "numeric",
            }),
          )}
          {fact(uiText("File size"), formatBytes(file.size_bytes))}
          {fact(
            uiText("File SHA-256"),
            <HashValue
              sha={file.sha256}
              label={uiText("File SHA-256")}
              filename={file.original_filename}
            />,
          )}
        </dl>
      </details>
    </section>
  );
}
