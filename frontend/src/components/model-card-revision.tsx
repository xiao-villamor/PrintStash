import { lazy, Suspense, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, Star } from "lucide-react";
import { Printer3D } from "@/components/ui/printer-3d";
import { LibraryItemLink } from "@/features/library/navigation";
import type { LibraryEntry } from "@/features/library/navigation-state";
import { modelPrinterFilesOptions, useModelPrintJobs } from "@/features/library/model-detail";
import { useAuth } from "@/lib/auth-context";
import { downloadAuthenticatedFile } from "@/lib/api";
import { uiText } from "@/lib/locale";
import { formatCalendarDate } from "@/lib/format";
import { useUiLocale } from "@/lib/i18n";
import { toast } from "@/lib/toast";
import type { FileRead, ModelListItem, ModelPrintJobRead, ModelRead } from "@/types";
import { Button } from "@/components/ui/button";
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
  origin,
  dimensions,
  collectionLabel,
  children,
}: {
  model: ModelListItem;
  detail?: ModelRead;
  admitted: boolean;
  origin?: LibraryEntry;
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
            <div className="library-card-revision-line">
              <strong title={file?.revision_label ?? model.recommended_revision_label ?? undefined}>
                {revision}
              </strong>
              {file?.is_recommended && (
                <span className="library-card-recommended">
                  <Star className="h-3 w-3 fill-current shrink-0" aria-hidden />
                  <span className="sr-only">{uiText("Recommended")}</span>
                </span>
              )}
              <span className="library-card-revision-separator" aria-hidden="true" />
              {administrator ? (
                <LibraryItemLink
                  origin={origin}
                  href={`/models/${model.id}?tab=history`}
                  trackPosition={false}
                  className="library-card-result focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                  aria-label={uiText("Print history")}
                  title={resultText}
                >
                  {outcomeLabels?.length
                    ? outcomeLabels.map(({ state, printer, label }) => (
                        <span
                          key={JSON.stringify([state, printer])}
                          className={
                            state === "completed"
                              ? "library-card-outcome"
                              : state === "failed"
                                ? "library-card-outcome library-card-outcome-failed"
                                : undefined
                          }
                        >
                          {label}
                        </span>
                      ))
                    : resultText}
                </LibraryItemLink>
              ) : (
                <p className="library-card-result" title={resultText}>
                  {resultText}
                </p>
              )}
              <div className="library-card-revision-actions">
                {file?.is_recommended && (
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    disabled={!canPrint}
                    onClick={() => setSendOpen(true)}
                    aria-label={`${uiText("Print")} ${revision}`}
                    title={canPrint ? uiText("Print") : uiText("Access restricted")}
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
            {children}
          </>
        )}
        {meta}
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
