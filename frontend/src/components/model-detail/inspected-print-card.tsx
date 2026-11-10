import { Check, Star, X } from "lucide-react";
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
import { PRINT_JOB_PRESENTATION, revisionStatusLabel } from "./presentation";

type RecordedPrint = Pick<ModelPrintJobRead, "file_id" | "printer_name" | "state">;

/** The inspected artifact's setup, estimates and recorded evidence stay separate. */
export function InspectedPrintCard({
  file,
  canEdit,
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
}: {
  file: FileRead | null;
  canEdit: boolean;
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
}) {
  useUiLocale();
  if (!file)
    return (
      <section className="detail-overview">
        <h2>{uiText("No G-code revision yet")}</h2>
        <p className="my-4 text-sm text-muted-foreground">
          {uiText("No sliced G-code yet. Add a revision to capture the settings that worked.")}
        </p>
        {canEdit && <Button onClick={onAddRevision}>{uiText("Add revision")}</Button>}
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
      <div className="detail-section-heading">
        <h2>{uiText("Revision overview")}</h2>
        <Button size="sm" variant="ghost" onClick={onRevisions}>
          {uiText("All revisions ({count})", { count: String(revisionCount) })}
        </Button>
      </div>
      <div className="detail-revision-identity">
        <h3>
          <strong>
            {uiText("Rev {value1}", { value1: String(file.gcode_revision_number ?? file.version) })}
          </strong>
          {file.revision_label && <span>{file.revision_label}</span>}
          {file.is_recommended ? (
            <span className="detail-recommended">
              <Star className="h-3 w-3" />
              {uiText("Recommended")}
            </span>
          ) : (
            recommended && (
              <Button size="sm" variant="ghost" onClick={() => onInspect(recommended.id)}>
                {uiText("Return to recommended revision")}
              </Button>
            )
          )}
        </h3>
        <p className="detail-artifact-filename">{file.original_filename}</p>
      </div>
      {file.revision_notes && <p className="detail-revision-note">{file.revision_notes}</p>}
      <div className="detail-overview-columns">
        <div>
          <section>
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
          <section className="detail-overview-secondary">
            <h3>{uiText("Print estimate")}</h3>
            <dl className="detail-overview-facts detail-estimates">
              {fact(uiText("Time"), formatDuration(meta?.estimated_time_s ?? null))}
              {fact(uiText("Filament per copy"), formatGrams(meta?.filament_weight_g))}
              {fact(uiText("Cost"), formatCost(meta?.filament_cost))}
            </dl>
          </section>
        </div>
        <div>
          <section aria-label={uiText("Recorded prints")}>
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
              <p className="detail-empty-evidence">
                {uiText("No prints recorded for this revision.")}
              </p>
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
            <p className="detail-manual-label">
              {uiText("Manual label")}
              <span>
                {file.revision_status ? revisionStatusLabel(file.revision_status) : uiText("None")}
              </span>
            </p>
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
          <section className="detail-overview-secondary">
            <h3>{uiText("G-code file")}</h3>
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
          </section>
        </div>
      </div>
      <div className="detail-revision-actions">
        <div className="flex flex-wrap gap-2">
          {canSend && (
            <>
              <Button onClick={() => onSend("send")}>{uiText("Send to printer")}</Button>
              <Button variant="outline" onClick={() => onSend("queue")}>
                {uiText("Add to queue")}
              </Button>
            </>
          )}
          <Button
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
        <Button variant="ghost" onClick={onCompare}>
          {uiText("Compare revisions")}
        </Button>
      </div>
    </section>
  );
}
