import { Button } from "@/components/ui/button";
import { SlicerOpenButton } from "@/components/slicer-open-button";
import { ChevronRight, Star } from "lucide-react";
import { formatPlateEstimate } from "./format-plate-estimate";
import { uiText } from "@/lib/locale";
import type { GcodePlates } from "@/lib/api/plates";
import type { FileRead } from "@/types";
import { PlateEstimates } from "./plate-estimates";
import { revisionStatusLabel } from "./presentation";

const REVISION_STATUS_DOT = {
  known_good: "bg-success",
  needs_test: "bg-warning",
  failed: "bg-destructive",
  archived: "bg-muted-foreground",
};

export function ModelPrintSummary({
  revision,
  source,
  estimates,
  selectedPlate,
  onReview,
  onPrepare,
  onAddRevision,
  onFiles,
}: {
  revision: FileRead | null;
  source: FileRead | undefined;
  estimates: GcodePlates | null;
  selectedPlate: number | null;
  onReview: () => void;
  onPrepare?: () => void;
  onAddRevision?: () => void;
  onFiles: () => void;
}) {
  const title = uiText("modelDetail.printStatus");
  const duration = revision?.metadata?.estimated_time_s;
  const weight = revision?.metadata?.filament_weight_g;
  const material = revision?.metadata?.material_type;
  const multiPlate = estimates !== null && estimates.plates.length > 1;
  const showEstimate =
    !revision?.original_filename.toLowerCase().endsWith(".3mf") || estimates !== null;

  return (
    <section aria-label={title} className="space-y-3">
      <h2 className="sr-only">{title}</h2>
      {revision ? (
        <>
          <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-2">
            <div className="min-w-0 space-y-1">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  onClick={onReview}
                  title={uiText("modelDetail.viewRevision")}
                  className="-ms-1.5 h-auto min-h-8 max-w-full justify-start gap-1 whitespace-normal px-1.5 py-1 text-start text-base font-semibold underline-offset-4 hover:underline"
                >
                  <span className="min-w-0 wrap-anywhere">
                    {uiText("Rev {value1}", {
                      value1: String(revision.gcode_revision_number ?? revision.version),
                    })}
                    {revision.revision_label && ` · ${revision.revision_label}`}
                  </span>
                  <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                </Button>
                {revision.is_recommended && (
                  <span
                    role="img"
                    aria-label={uiText("Recommended revision")}
                    title={uiText("Recommended revision")}
                    className="inline-flex items-center text-muted-foreground"
                  >
                    <Star className="size-3.5" aria-hidden />
                  </span>
                )}
                <span className="inline-flex items-center gap-1.5 text-xs font-medium">
                  <span
                    aria-hidden
                    className={`size-1.5 shrink-0 rounded-full ${revision.revision_status ? REVISION_STATUS_DOT[revision.revision_status] : "bg-muted-foreground"}`}
                  />
                  {revisionStatusLabel(revision.revision_status)}
                </span>
              </div>
              {multiPlate ? (
                material && (
                  <p className="text-sm text-muted-foreground">
                    {uiText("Material")} · {material}
                  </p>
                )
              ) : showEstimate && (duration != null || weight != null) ? (
                <p className="text-sm tabular-nums text-muted-foreground">
                  <span className="sr-only">{uiText("modelDetail.gcodeEstimate")} </span>
                  {[
                    material,
                    duration != null ? formatPlateEstimate("estimated_time_s", duration) : null,
                    weight != null ? formatPlateEstimate("filament_weight_g", weight) : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
              ) : null}
            </div>
            {onPrepare && (
              <Button size="sm" onClick={onPrepare}>
                {uiText("modelDetail.preparePrint")}
              </Button>
            )}
          </div>
          {multiPlate &&
            (estimates.plates.length <= 2 ? (
              <PlateEstimates data={estimates} selected={selectedPlate} showCost={false} />
            ) : (
              <p className="text-sm text-muted-foreground">
                {uiText("modelDetail.platesSummary", { value1: String(estimates.plates.length) })}
              </p>
            ))}
        </>
      ) : (
        <>
          <p className="text-base font-semibold">{uiText("No G-code revision")}</p>
          {source ? (
            <p className="wrap-anywhere text-sm text-muted-foreground">
              {source.original_filename} · {source.file_type.toUpperCase()}
            </p>
          ) : (
            <p className="text-sm text-muted-foreground">{uiText("modelDetail.noSliceableFile")}</p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            {source ? (
              <SlicerOpenButton fileId={source.id} fileType={source.file_type} primary showLabel />
            ) : (
              <Button variant={onAddRevision ? "outline" : "default"} onClick={onFiles}>
                {uiText("View source files")}
              </Button>
            )}
            {onAddRevision && (
              <Button variant={source ? "outline" : "default"} onClick={onAddRevision}>
                {uiText("Add revision")}
              </Button>
            )}
          </div>
        </>
      )}
    </section>
  );
}
