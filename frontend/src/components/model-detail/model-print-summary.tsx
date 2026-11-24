import { Button } from "@/components/ui/button";
import { SlicerOpenButton } from "@/components/slicer-open-button";
import { formatDuration, formatGrams } from "@/lib/format";
import { uiText } from "@/lib/locale";
import type { GcodePlates } from "@/lib/api/plates";
import type { FileRead } from "@/types";
import { PlateEstimates } from "./plate-estimates";
import { revisionStatusLabel } from "./presentation";

export function ModelPrintSummary({
  revision,
  source,
  estimates,
  selectedPlate,
  results,
  onReview,
  onPrepare,
  onAddRevision,
  onFiles,
}: {
  revision: FileRead | null;
  source: FileRead | undefined;
  estimates: GcodePlates | null;
  selectedPlate: number | null;
  results: number | null;
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
    <section
      aria-label={title}
      className="space-y-3 border border-border bg-surface-container-low p-4"
    >
      <h2 className="text-base font-semibold">{title}</h2>
      {revision ? (
        <>
          <div className="space-y-1">
            <p className="wrap-anywhere text-lg font-semibold">
              {uiText("Rev {value1}", {
                value1: String(revision.gcode_revision_number ?? revision.version),
              })}
              {revision.revision_label && ` · ${revision.revision_label}`}
            </p>
            <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
              <span className="border border-border bg-surface-container-lowest px-2 py-0.5 font-medium">
                {revisionStatusLabel(revision.revision_status)}
              </span>
              {revision.is_recommended && (
                <span className="text-muted-foreground">{uiText("Recommended revision")}</span>
              )}
              {results === 0 && (
                <span className="text-muted-foreground">· {uiText("No recorded prints")}</span>
              )}
            </p>
          </div>
          {multiPlate ? (
            <>
              {material && (
                <p className="text-sm text-muted-foreground">
                  {uiText("Material")} · {material}
                </p>
              )}
              {estimates.plates.length <= 2 ? (
                <PlateEstimates data={estimates} selected={selectedPlate} showCost={false} />
              ) : (
                <p className="text-sm text-muted-foreground">
                  {uiText("modelDetail.platesSummary", { value1: String(estimates.plates.length) })}
                </p>
              )}
            </>
          ) : showEstimate && (duration != null || weight != null) ? (
            <p className="text-sm text-muted-foreground">
              {uiText("modelDetail.gcodeEstimate")} ·{" "}
              {[
                material,
                duration != null ? formatDuration(duration) : null,
                weight != null ? formatGrams(weight) : null,
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
          ) : null}
          <div className="flex flex-wrap items-center gap-2">
            {onPrepare && <Button onClick={onPrepare}>{uiText("modelDetail.preparePrint")}</Button>}
            <Button variant={onPrepare ? "outline" : "default"} onClick={onReview}>
              {uiText("modelDetail.viewRevision")}
            </Button>
          </div>
        </>
      ) : (
        <>
          <p className="text-lg font-semibold">{uiText("No G-code revision")}</p>
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
