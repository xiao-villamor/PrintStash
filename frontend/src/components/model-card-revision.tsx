import { lazy, Suspense, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, Printer, Star } from "lucide-react";
import { LibraryItemLink } from "@/features/library/navigation";
import type { LibraryEntry } from "@/features/library/navigation-state";
import { modelPrinterFilesOptions, useModelPrintJobs } from "@/features/library/model-detail";
import { useAuth } from "@/lib/auth-context";
import { downloadAuthenticatedFile } from "@/lib/api";
import { uiText, currentLocale } from "@/lib/locale";
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
  children,
}: {
  model: ModelListItem;
  detail?: ModelRead;
  admitted: boolean;
  origin?: LibraryEntry;
  children: ReactNode;
}) {
  useUiLocale();
  const { user } = useAuth();
  const revisions = cardRevisions(detail);
  const file = revisions.find((revision) => revision.is_recommended) ?? revisions[0];
  const administrator = !!user?.is_superuser;
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

  return (
    <div className="library-card-summary-container">
      <div className="library-card-revision-summary">
        {detail && !file ? (
          <div className="library-card-no-gcode">
            <span>{uiText("No G-code yet")}</span>
            <time dateTime={model.updated_at}>
              {new Intl.DateTimeFormat(currentLocale(), { month: "short", day: "numeric" }).format(
                new Date(model.updated_at),
              )}
            </time>
          </div>
        ) : (
          <>
            <div className="library-card-revision-line">
              <strong title={file?.revision_label ?? model.recommended_revision_label ?? undefined}>
                {revision}
              </strong>
              {file?.is_recommended && (
                <span className="library-card-recommended">
                  <Star className="h-3 w-3 fill-current shrink-0" aria-hidden />
                  <span>{uiText("Recommended")}</span>
                </span>
              )}
              <div className="library-card-revision-actions">
                {file?.is_recommended && (
                  <Button
                    variant="outline"
                    size="icon-sm"
                    disabled={!canPrint}
                    onClick={() => setSendOpen(true)}
                    aria-label={`${uiText("Print")} ${revision}`}
                    title={canPrint ? uiText("Print") : uiText("Access restricted")}
                  >
                    <Printer aria-hidden />
                  </Button>
                )}
                {file && (
                  <Button
                    variant="outline"
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
            {administrator ? (
              <LibraryItemLink
                origin={origin}
                href={`/models/${model.id}?tab=history`}
                className="library-card-result"
                aria-label={uiText("Print history")}
              >
                {results === null
                  ? uiText("Print results not available")
                  : results.length === 0
                    ? uiText("Not printed yet")
                    : results.map((result) => (
                        <span
                          key={`${result.state}:${result.printer}`}
                          className={
                            result.state === "completed"
                              ? "library-card-outcome"
                              : result.state === "failed"
                                ? "library-card-outcome library-card-outcome-failed"
                                : undefined
                          }
                        >
                          {uiText("library.cardOutcome", {
                            result:
                              result.state === "completed"
                                ? uiText("OK")
                                : result.state === "failed"
                                  ? uiText("Failed")
                                  : result.state === "cancelled"
                                    ? uiText("Cancelled")
                                    : uiText("In progress"),
                            count: result.count,
                            printer: result.printer,
                          })}
                        </span>
                      ))}
              </LibraryItemLink>
            ) : (
              <p className="library-card-result">{uiText("Print results not available")}</p>
            )}
            {children}
          </>
        )}
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
