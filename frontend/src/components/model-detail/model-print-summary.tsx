import { Button } from "@/components/ui/button";
import { SlicerOpenButton } from "@/components/slicer-open-button";
import { CalendarDays, ChevronRight, Clock, History } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { RelatedProfile } from "./use-related-profiles";
import { InspectorSection, PropertyList, PropertyRow, SoftCard } from "./inspector-section";
import { FilamentSpool } from "@/components/ui/filament-spool";
import { RevisionDeliveryActions } from "./revision-delivery-actions";
import { summarizePrintEstimate } from "./print-estimate";
import { formatPlateEstimate } from "./format-plate-estimate";
import { uiText } from "@/lib/locale";
import { timeAgo } from "@/lib/format";
import type { GcodePlates } from "@/lib/api/plates";
import type { FileRead, ModelPrintJobRead } from "@/types";
import { PRINT_JOB_PRESENTATION, revisionStatusLabel, sourceProviderName } from "./presentation";

const REVISION_STATUS_DOT = {
  known_good: "bg-success",
  needs_test: "bg-warning",
  failed: "bg-destructive",
  archived: "bg-muted-foreground",
};

function jobInstant(job: ModelPrintJobRead): string {
  return job.finished_at ?? job.started_at ?? job.created_at;
}

/** The most recent recorded result for one revision, or null when none exists. */
function latestRevisionJob(jobs: ModelPrintJobRead[], fileId: number): ModelPrintJobRead | null {
  let latest: ModelPrintJobRead | null = null;
  for (const job of jobs) {
    if (job.file_id !== fileId) continue;
    if (latest === null || Date.parse(jobInstant(job)) > Date.parse(jobInstant(latest)))
      latest = job;
  }
  return latest;
}

export function RevisionStatusBadge({ status }: { status: FileRead["revision_status"] }) {
  return (
    <Badge variant="outline" className="gap-1.5 text-sm">
      <span
        aria-hidden
        className={`size-2 shrink-0 rounded-full ${status ? REVISION_STATUS_DOT[status] : "bg-muted-foreground"}`}
      />
      {revisionStatusLabel(status)}
    </Badge>
  );
}

export function ModelPrintSummary({
  revision,
  source,
  estimates,
  jobs = null,
  onReview,
  onPrepare,
  onRecordResult,
  onPlateEstimates,
  onMarkKnownGood,
  sent = false,
  profiles = [],
  onAddRevision,
  onFiles,
}: {
  revision: FileRead | null;
  source: FileRead | undefined;
  estimates: GcodePlates | null;
  /** Recorded results visible to this viewer; null when they cannot be read. */
  jobs?: ModelPrintJobRead[] | null;
  onReview: () => void;
  onPrepare?: () => void;
  onRecordResult?: () => void;
  onPlateEstimates?: () => void;
  onMarkKnownGood?: () => void;
  sent?: boolean;
  profiles?: RelatedProfile[];
  onAddRevision?: () => void;
  onFiles: () => void;
}) {
  const latest = revision && jobs ? latestRevisionJob(jobs, revision.id) : null;
  const untested =
    revision !== null &&
    (revision.revision_status === "needs_test" || revision.revision_status === null);

  if (!revision)
    return (
      <InspectorSection title={uiText("modelDetail.currentRevision")}>
        <SoftCard>
          <div className="space-y-1 px-4 py-3">
            <p className="text-base font-semibold">{uiText("No G-code revision")}</p>
            <p className="wrap-anywhere text-sm text-muted-foreground">
              {source
                ? `${source.original_filename} · ${source.file_type.toUpperCase()}`
                : uiText("modelDetail.noSliceableFile")}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2 px-4 py-3">
            {source ? (
              <SlicerOpenButton
                fileId={source.id}
                fileType={source.file_type}
                primary
                showLabel
                size="sm"
              />
            ) : (
              <Button size="sm" variant={onAddRevision ? "outline" : "default"} onClick={onFiles}>
                {uiText("View source files")}
              </Button>
            )}
            {onAddRevision && (
              <Button size="sm" variant={source ? "outline" : "default"} onClick={onAddRevision}>
                {uiText("Add revision")}
              </Button>
            )}
          </div>
        </SoftCard>
      </InspectorSection>
    );

  const summary = summarizePrintEstimate(revision, estimates);
  const time = summary.kind === "single" || summary.kind === "plates" ? summary.time : null;
  const weight = summary.kind === "single" || summary.kind === "plates" ? summary.weight : null;
  const estimateText = [
    time != null ? formatPlateEstimate("estimated_time_s", time) : null,
    weight != null ? formatPlateEstimate("filament_weight_g", weight) : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const estimateSource =
    summary.kind === "single"
      ? uiText("G-code estimate")
      : summary.kind === "plates"
        ? uiText("G-code estimate · sum of {count} plates", { count: summary.count })
        : summary.kind === "partial-plates"
          ? uiText("modelDetail.plateEstimatesIncomplete")
          : null;

  const profile = profiles.find(
    (item) =>
      item.profile.origin === "provider" &&
      (item.profile.estimated_seconds != null || item.profile.filament_grams != null) &&
      ((item.profile.estimated_seconds != null && item.profile.estimated_seconds !== time) ||
        (item.profile.filament_grams != null && item.profile.filament_grams !== weight)),
  );
  const profileEstimate = profile
    ? [
        profile.profile.estimated_seconds != null
          ? formatPlateEstimate("estimated_time_s", profile.profile.estimated_seconds)
          : null,
        profile.profile.filament_grams != null
          ? formatPlateEstimate("filament_weight_g", profile.profile.filament_grams)
          : null,
      ]
        .filter(Boolean)
        .join(" · ")
    : null;

  return (
    <InspectorSection title={uiText("modelDetail.currentRevision")}>
      <SoftCard>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3">
          <button
            type="button"
            onClick={onReview}
            title={uiText("modelDetail.viewRevision")}
            className="inline-flex min-w-0 items-center gap-1 rounded-sm text-start text-base font-semibold underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          >
            <span className="min-w-0 wrap-anywhere">
              {uiText("Rev {value1}", {
                value1: String(revision.gcode_revision_number ?? revision.version),
              })}
              {revision.revision_label && ` · ${revision.revision_label}`}
            </span>
            <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          </button>
          <RevisionStatusBadge status={revision.revision_status ?? "needs_test"} />
          {revision.is_recommended && (
            <span className="text-sm text-muted-foreground">{uiText("Recommended")}</span>
          )}
        </div>
        <PropertyList>
          {jobs !== null && (
            <PropertyRow label={uiText("modelDetail.lastPrintLabel")} icon={<History />}>
              <span data-testid="latest-print">
                {latest ? (
                  <span className={latest.state === "failed" ? "text-destructive" : undefined}>
                    {uiText("modelDetail.lastPrintValue", {
                      state:
                        latest.state === "completed"
                          ? uiText("Completed")
                          : PRINT_JOB_PRESENTATION[latest.state].label,
                      printer: latest.printer_name,
                      when: timeAgo(jobInstant(latest)),
                    })}
                  </span>
                ) : (
                  <>
                    {uiText("modelDetail.noPrintsYetShort")}
                    {untested && (
                      <span className="block text-muted-foreground">
                        {uiText("modelDetail.testNextStep")}
                      </span>
                    )}
                  </>
                )}
              </span>
            </PropertyRow>
          )}
          {revision.metadata?.material_type && (
            <PropertyRow label={uiText("Material")} icon={<FilamentSpool />}>
              {revision.metadata.material_type}
            </PropertyRow>
          )}
          {(estimateText || estimateSource || profileEstimate) && (
            <PropertyRow label={uiText("modelDetail.estimate")} icon={<Clock />}>
              <span className="tabular-nums">{estimateText}</span>
              {estimateSource && (
                <span className="block text-sm text-muted-foreground">{estimateSource}</span>
              )}
              {profile && profileEstimate && (
                <span className="block text-sm text-muted-foreground">
                  {uiText("{site} profile says {estimate}", {
                    site: sourceProviderName(profile.provider),
                    estimate: profileEstimate,
                  })}
                </span>
              )}
            </PropertyRow>
          )}
          {(summary.kind === "plates" || summary.kind === "partial-plates") && (
            <PropertyRow label={uiText("Plates")}>
              <Button variant="ghost" size="sm" onClick={onPlateEstimates}>
                {uiText("modelDetail.plateCount", { count: summary.count })}
                <ChevronRight className="size-4" aria-hidden />
              </Button>
            </PropertyRow>
          )}
          <PropertyRow label={uiText("Added")} icon={<CalendarDays />}>
            {timeAgo(revision.uploaded_at)}
          </PropertyRow>
        </PropertyList>
        {(onPrepare || onRecordResult) && (
          <div className="grid grid-cols-2 gap-2 bg-muted/30 px-4 py-3">
            {onRecordResult ? (
              <Button size="sm" variant={sent ? "default" : "outline"} onClick={onRecordResult}>
                {uiText("modelDetail.recordResult")}
              </Button>
            ) : (
              <span />
            )}
            {onPrepare && (
              <RevisionDeliveryActions secondary={sent && !!onRecordResult} onSend={onPrepare} />
            )}
          </div>
        )}
        {untested &&
          onMarkKnownGood &&
          jobs?.some((job) => job.file_id === revision.id && job.state === "completed") && (
            <div className="border-t border-border px-4 py-3">
              <Button size="sm" variant="outline" onClick={onMarkKnownGood}>
                {uiText("Mark known good")}
              </Button>
            </div>
          )}
      </SoftCard>
    </InspectorSection>
  );
}
