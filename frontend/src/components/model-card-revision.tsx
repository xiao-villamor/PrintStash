import { lazy, Suspense, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { Printer3D } from "@/components/ui/printer-3d";
import { modelPrinterFilesOptions, useModelPrintJobs } from "@/features/library/model-detail";
import { useAuth } from "@/lib/auth-context";
import { downloadAuthenticatedFile } from "@/lib/api";
import { uiText } from "@/lib/locale";
import { formatCalendarDate } from "@/lib/format";
import { useUiLocale } from "@/lib/i18n";
import { toast } from "@/lib/toast";
import type {
  FileRead,
  FileRevisionStatus,
  ModelListItem,
  ModelPrintJobRead,
  ModelRead,
} from "@/types";
import { Button } from "@/components/ui/button";
import { RevisionStatusBadge } from "@/components/revision-status-badge";
const SendToButtons = lazy(() =>
  import("@/components/model-detail/send-to-buttons").then((module) => ({
    default: module.SendToButtons,
  })),
);
import { cardRevisions } from "@/features/library/card-detail";
import type { ReactNode } from "react";

function outcomes(jobs: ModelPrintJobRead[], file: FileRead) {
  const groups = new Map<
    string,
    { state: ModelPrintJobRead["state"]; printer: string; count: number }
  >();
  for (const job of jobs) {
    if (job.file_id !== file.id) continue;
    const key = JSON.stringify([job.state, job.printer_name]);
    const group = groups.get(key);
    if (group) group.count += 1;
    else groups.set(key, { state: job.state, printer: job.printer_name, count: 1 });
  }
  return [...groups.values()];
}

export function ModelCardRevision({
  model,
  detail,
  admitted,
  status,
  statusLabel,
  dimensions,
  collectionLabel,
  children,
}: {
  model: ModelListItem;
  detail?: ModelRead;
  admitted: boolean;
  /** The recommended revision's status; absent when no status is known. */
  status: FileRevisionStatus | null | undefined;
  statusLabel: string | null | undefined;
  /** Bounding box, e.g. `166 × 166 × 1 mm`; unknown until the card detail loads. */
  dimensions: string | null;
  /** Shown when the view spans folders; otherwise the card shows its file count. */
  collectionLabel?: string | null;
  children: ReactNode;
}) {
  useUiLocale();
  const { user } = useAuth();
  const revisions = cardRevisions(detail);
  const file = revisions.find((revision) => revision.is_recommended) ?? revisions[0];
  const administrator = !!user?.is_superuser;
  // The list row says whether any G-code exists, so the card takes its final
  // height at once instead of resizing (and moving its row) when the detail
  // arrives; a Model without G-code never reads its detail at all.
  const expectsRevision = model.gcode_file_count > 0;
  const history = useModelPrintJobs(model.id, admitted && !!file && administrator);
  const [sendOpen, setSendOpen] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const canPrint =
    administrator && (detail?.effective_role === "edit" || detail?.effective_role === "admin");
  const printerFiles = useQuery({
    ...modelPrinterFilesOptions(model.id),
    enabled: sendOpen && canPrint,
  });
  const results = file && history.data ? outcomes(history.data, file) : null;
  const outcomeLabels = results?.map(({ state, printer, count }) => ({
    state,
    printer,
    label: uiText("library.cardOutcome", {
      result:
        state === "completed"
          ? uiText("OK")
          : state === "failed"
            ? uiText("Failed")
            : state === "cancelled"
              ? uiText("Cancelled")
              : uiText("In progress"),
      count,
      printer,
    }),
  }));
  const resultText =
    outcomeLabels === undefined
      ? uiText("Print results not available")
      : outcomeLabels.length === 0
        ? uiText("Not printed yet")
        : outcomeLabels.map(({ label }) => label).join(" · ");
  const revision = file
    ? uiText("Rev {value1}", { value1: String(file.gcode_revision_number ?? file.version) })
    : uiText("Revision");

  async function download() {
    if (!file || downloading) return;
    setDownloading(true);
    try {
      await downloadAuthenticatedFile(`/api/v1/files/${file.id}/download`, file.original_filename);
    } catch (error) {
      toast.error(error);
    } finally {
      setDownloading(false);
    }
  }

  // Every card ends on the same quiet line, so footers align across a row.
  const meta = (
    <p className="library-card-meta">
      <span>{dimensions ?? ""}</span>
      <time dateTime={model.updated_at}>{formatCalendarDate(model.updated_at)}</time>
    </p>
  );

  return (
    <div className="library-card-summary-container">
      <div className="library-card-revision-summary">
        {(detail ? !file : !expectsRevision) ? (
          <>
            <span className="sr-only">{uiText("No G-code yet")}</span>
            <p className="library-card-context" title={collectionLabel ?? undefined}>
              {collectionLabel
                ? collectionLabel.slice(collectionLabel.lastIndexOf("/") + 1)
                : uiText("counts.files", { count: model.file_count })}
            </p>
          </>
        ) : (
          <>
            <div className="library-card-revision-line" title={resultText}>
              {status && <RevisionStatusBadge status={status} label={statusLabel} />}
              <strong title={file?.revision_label ?? model.recommended_revision_label ?? undefined}>
                {revision}
              </strong>
              <div className="library-card-revision-actions">
                {file?.is_recommended && (
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    disabled={!canPrint}
                    onClick={() => setSendOpen(true)}
                    aria-label={`${uiText("Print")} ${revision}`}
                    title={canPrint ? uiText("Print") : uiText("Access restricted")}
                    className="library-card-send"
                  >
                    <Printer3D aria-hidden />
                  </Button>
                )}
                {file && (
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    disabled={downloading}
                    onClick={() => void download()}
                    aria-label={`${uiText("Download")} ${revision}`}
                    title={uiText("Download")}
                  >
                    <Download aria-hidden />
                  </Button>
                )}
              </div>
            </div>
            <div className="library-card-data-row">
              {children}
              <time dateTime={model.updated_at}>{formatCalendarDate(model.updated_at)}</time>
            </div>
          </>
        )}
        {(detail ? !file : !expectsRevision) && meta}
      </div>
      {sendOpen && canPrint && file && (
        <div className="hidden">
          <Suspense fallback={null}>
            <SendToButtons
              modelName={model.name}
              gcodeFiles={revisions}
              printerFiles={printerFiles.data ?? []}
              open
              onOpenChange={setSendOpen}
              preselectFileId={file.id}
              compact
            />
          </Suspense>
        </div>
      )}
    </div>
  );
}
