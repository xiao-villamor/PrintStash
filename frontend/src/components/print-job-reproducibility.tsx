"use client";

import { lazy, Suspense, useState, type ComponentType } from "react";
import { Download, Eye, ExternalLink, FileWarning, Loader2 } from "lucide-react";

import { downloadAuthenticatedFile } from "@/lib/api/request";
import { useOptionalI18n } from "@/lib/i18n";
import { Link } from "@/lib/link";
import { toast } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { Button, buttonVariants } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import {
  isArchivedPrintArtifact,
  printJobArtifactLabel,
  resolvePrintJobReproducibility,
  type PrintJobReproducibilityInput,
} from "@/lib/print-job-reproducibility";
import type { ReproducibilityLevel } from "@/types";

const LazyGcodeViewer = lazy(() =>
  import("@/components/gcode-viewer").then(({ GcodeViewer }) => ({ default: GcodeViewer })),
);

interface LevelCopy {
  label: string;
  description: string;
  className: string;
}

type DownloadFile = (path: string) => Promise<void>;
type ToolpathViewer = ComponentType<{ url: string }>;

function levelCopy(level: ReproducibilityLevel): LevelCopy {
  switch (level) {
    case "exact":
      return {
        label: "Exactly reproducible",
        description: "The archived artifact is available for this print.",
        className: "border-success/30 bg-success/10 text-success",
      };
    case "metadata":
      return {
        label: "Partially reproducible",
        description:
          "Printer-reported identity and metadata are available; the original artifact is not archived.",
        className: "border-warning/30 bg-warning/10 text-warning",
      };
    default:
      return {
        label: "External/basic evidence",
        description:
          "Only external print evidence is available; the original file is not archived.",
        className: "border-border bg-muted text-muted-foreground",
      };
  }
}

function Detail({ label, value }: { label: string; value: string | number | null | undefined }) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <div className="min-w-0">
      <dt className="font-mono text-3xs uppercase tracking-wider text-muted-foreground">{label}</dt>
      <dd className="truncate font-mono text-2xs text-foreground" title={String(value)}>
        {value}
      </dd>
    </div>
  );
}

export function PrintJobReproducibility({
  job,
  previewHref,
  previewLabel = "Open model detail",
  downloadFile = downloadAuthenticatedFile,
  toolpathViewer = LazyGcodeViewer,
}: {
  job: PrintJobReproducibilityInput;
  previewHref?: string | null;
  previewLabel?: string;
  downloadFile?: DownloadFile;
  toolpathViewer?: ToolpathViewer;
}) {
  const i18n = useOptionalI18n();
  const [downloading, setDownloading] = useState(false);
  const [toolpathPreviewOpen, setToolpathPreviewOpen] = useState(false);
  const resolved = resolvePrintJobReproducibility(job);
  const copy = levelCopy(resolved.level);
  const ToolpathViewer = toolpathViewer;
  const previewToolpathLabel = i18n?.t("repro.previewToolpath") ?? "Preview toolpath";
  const toolpathPreviewTitle = i18n?.t("repro.toolpathPreview") ?? "Toolpath preview";
  const loadingToolpathLabel = i18n?.t("viewer.loadingToolpath") ?? "Loading toolpath…";
  const artifactName = printJobArtifactLabel(job);
  const canDownload = Boolean(
    resolved.level === "exact" &&
    resolved.downloadUrl &&
    isArchivedPrintArtifact(job.artifact_evidence),
  );
  const canPreview = Boolean(previewHref && isArchivedPrintArtifact(job.artifact_evidence));
  const hasReportedIdentity = Object.values(resolved.identity).some(
    (value) => value !== null && value !== "",
  );
  const hasReportedMetadata = Object.values(resolved.metadata).some((value) => value !== null);

  async function downloadArtifact() {
    if (!canDownload || !resolved.downloadUrl || downloading) return;
    setDownloading(true);
    try {
      await downloadFile(resolved.downloadUrl);
    } catch (error) {
      toast.error(error);
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div
      className="mt-2 space-y-2 rounded border border-border bg-muted/30 p-2.5"
      data-testid="print-job-reproducibility"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 space-y-1">
          <p className="font-mono text-2xs font-semibold text-foreground">{artifactName}</p>
          <p className="font-mono text-3xs text-muted-foreground">{copy.description}</p>
        </div>
        <span
          className={cn(
            "shrink-0 rounded border px-1.5 py-0.5 font-mono text-3xs uppercase tracking-wider",
            copy.className,
          )}
          data-testid="reproducibility-level"
        >
          {copy.label}
        </span>
      </div>

      {(hasReportedIdentity || hasReportedMetadata) && (
        <dl className="grid grid-cols-2 gap-x-3 gap-y-2 border-t border-border pt-2 sm:grid-cols-3">
          {hasReportedIdentity && (
            <>
              <Detail label="Reported name" value={resolved.identity.display_name} />
              <Detail label="Task ID" value={resolved.identity.task_id} />
              <Detail label="Subtask ID" value={resolved.identity.subtask_id} />
              <Detail label="Project ID" value={resolved.identity.project_id} />
              <Detail label="Profile ID" value={resolved.identity.profile_id} />
              <Detail label="Printer file" value={resolved.identity.gcode_file} />
              <Detail label="Plate" value={resolved.identity.plate_index} />
            </>
          )}
          {hasReportedMetadata && (
            <>
              <Detail
                label="Layers"
                value={
                  resolved.metadata.current_layer !== null ||
                  resolved.metadata.total_layers !== null
                    ? `${resolved.metadata.current_layer ?? "—"} / ${resolved.metadata.total_layers ?? "—"}`
                    : null
                }
              />
              <Detail
                label="Nozzle"
                value={
                  resolved.metadata.nozzle_diameter !== null
                    ? `${resolved.metadata.nozzle_diameter} mm`
                    : null
                }
              />
            </>
          )}
        </dl>
      )}

      {resolved.error && (
        <div
          className="flex gap-2 border-t border-border pt-2 text-destructive"
          data-testid="reproducibility-error"
        >
          <FileWarning className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          <div className="min-w-0 font-mono text-2xs">
            <p>
              <span className="font-semibold">Error code:</span> {resolved.error.code}
            </p>
            <p className="break-words">{resolved.error.message}</p>
          </div>
        </div>
      )}

      {(canDownload || canPreview || resolved.toolpathPreviewUrl) && (
        <div className="flex flex-wrap gap-2 border-t border-border pt-2">
          {canDownload && (
            <Button
              type="button"
              variant="outline"
              size="xs"
              loading={downloading}
              onClick={() => void downloadArtifact()}
              aria-label="Download archived artifact"
            >
              <Download className="h-3.5 w-3.5" aria-hidden />
              Download archived artifact
            </Button>
          )}
          {canPreview && (
            <Link
              href={previewHref!}
              className={cn(buttonVariants({ variant: "outline", size: "xs" }))}
              aria-label={previewLabel}
            >
              <ExternalLink className="h-3.5 w-3.5" aria-hidden />
              {previewLabel}
            </Link>
          )}
          {resolved.toolpathPreviewUrl && (
            <Button
              type="button"
              variant="outline"
              size="xs"
              onClick={() => setToolpathPreviewOpen(true)}
              aria-label={previewToolpathLabel}
            >
              <Eye className="h-3.5 w-3.5" aria-hidden />
              {previewToolpathLabel}
            </Button>
          )}
        </div>
      )}

      {resolved.toolpathPreviewUrl && (
        <Modal
          open={toolpathPreviewOpen}
          onClose={() => setToolpathPreviewOpen(false)}
          title={toolpathPreviewTitle}
          className="flex max-h-[min(48rem,calc(100vh-2rem))] max-w-5xl flex-col overflow-hidden"
        >
          <div className="relative h-[min(70vh,42rem)] min-h-[20rem] w-full overflow-hidden rounded border border-border bg-surface-container-lowest">
            <Suspense
              fallback={
                <div className="absolute inset-0 flex items-center justify-center gap-2 text-muted-foreground">
                  <Loader2 className="h-5 w-5 animate-spin" aria-hidden />
                  <span className="font-mono text-2xs">{loadingToolpathLabel}</span>
                </div>
              }
            >
              <ToolpathViewer url={resolved.toolpathPreviewUrl} />
            </Suspense>
          </div>
        </Modal>
      )}
    </div>
  );
}
