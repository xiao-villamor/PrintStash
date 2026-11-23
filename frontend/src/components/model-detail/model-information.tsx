import { RelatedProfiles } from "./related-profiles";
import { GeometryReport } from "./geometry-report";
import { ModelSourceSummary } from "./source-summary";
import { Fragment } from "react";
import { ChevronRight, ExternalLink, Star } from "lucide-react";
import { Button } from "@/components/ui/button";
import { meshGeometryFacts, type useMeshGeometryMeasurement } from "./use-mesh-geometry-facts";
import { currentLocale, uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import type { FileRead, ModelRead } from "@/types";
import { safeHttpUrl } from "./source-url";
import { ModelDescription } from "./model-description";

/** Model facts in Overview describe the Model independently of the inspected Revision. */
export function ModelInformation({
  model,
  canEdit,
  recommendedRevision,
  onEdit,
  onTags,
}: {
  model: ModelRead;
  canEdit: boolean;
  recommendedRevision?: { file: FileRead; open: () => void } | null;
  onEdit: () => void;
  onTags: () => void;
}) {
  useUiLocale();
  const sourceUrl = model.source_url ? safeHttpUrl(model.source_url) : null;
  const collectionNames = model.collection_label ? model.collection_label.split("/") : null;
  const revision = recommendedRevision?.file;
  return (
    <section aria-label={uiText("About this model")} className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">{uiText("About this model")}</h2>
        {canEdit && (
          <Button size="sm" variant="outline" onClick={onEdit}>
            {uiText("Edit details")}
          </Button>
        )}
      </div>
      {recommendedRevision && revision && (
        <Button
          type="button"
          variant="ghost"
          onClick={recommendedRevision.open}
          className="h-auto min-h-10 w-full justify-start gap-2.5 whitespace-normal border border-border px-3 py-2 text-start"
        >
          <Star className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          <span className="flex min-w-0 grow flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <span className="text-muted-foreground">{uiText("Recommended revision")}</span>
            <span className="min-w-0 font-semibold wrap-anywhere">
              {uiText("Rev {value1}", {
                value1: String(revision.gcode_revision_number ?? revision.version),
              })}
              {revision.revision_label && ` · ${revision.revision_label}`}
            </span>
          </span>
          <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        </Button>
      )}
      {model.description?.trim() && <ModelDescription description={model.description} />}
      <dl className="flex flex-wrap gap-x-10 gap-y-4 text-base [&_dt]:text-sm [&_dt]:font-medium [&_dt]:text-muted-foreground">
        {collectionNames && (
          <div className="min-w-0 basis-full space-y-1">
            <dt>{uiText("Collection")}</dt>
            <dd>
              <ol
                role="list"
                aria-label={uiText("Collection")}
                className="flex flex-wrap gap-x-1.5"
              >
                {collectionNames.map((name, index) => (
                  <li
                    key={`${index}:${name}`}
                    className="flex min-w-0 max-w-full items-baseline gap-1.5 text-muted-foreground last:font-semibold last:text-foreground"
                  >
                    {index > 0 && (
                      <ChevronRight
                        aria-hidden="true"
                        className="size-3 shrink-0 self-start mt-1.5"
                      />
                    )}
                    <span className="min-w-0 wrap-anywhere">{name}</span>
                  </li>
                ))}
              </ol>
            </dd>
          </div>
        )}
        <div className="min-w-0 space-y-1">
          <dt>{uiText("Tags")}</dt>
          <dd className="flex flex-wrap items-center gap-1.5">
            {!canEdit && model.tags.length === 0 && uiText("None")}
            {model.tags.map((tag) => (
              <span
                className="min-w-0 wrap-anywhere bg-muted px-2 py-1 text-sm text-muted-foreground"
                key={tag}
              >
                {tag}
              </span>
            ))}
            {canEdit && (
              <Button
                size="sm"
                variant="outline"
                aria-label={
                  model.tags.length
                    ? uiText("Edit tags for {value1}", { value1: model.name })
                    : uiText("Add tags to {value1}", { value1: model.name })
                }
                onClick={onTags}
              >
                {model.tags.length ? uiText("Edit tags") : uiText("Add tags")}
              </Button>
            )}
          </dd>
        </div>
        <div className="space-y-1">
          <dt>{uiText("Created")}</dt>
          <dd>{new Date(model.created_at).toLocaleDateString(currentLocale())}</dd>
        </div>
        {sourceUrl && (
          <div className="space-y-1">
            <dt>{uiText("Source")}</dt>
            <dd>
              <a
                href={sourceUrl}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1.5 text-foreground underline-offset-4 hover:underline"
              >
                <ExternalLink className="size-3.5" aria-hidden="true" />
                {uiText("Source model")}
              </a>
            </dd>
          </div>
        )}
      </dl>
      <ModelSourceSummary modelId={model.id} />
      <RelatedProfiles modelId={model.id} />
    </section>
  );
}

export function ModelGeometry({
  modelId,
  sourceFile,
  measurement = "unavailable",
}: {
  modelId: number;
  sourceFile: FileRead | null;
  measurement?: ReturnType<typeof useMeshGeometryMeasurement>;
}) {
  useUiLocale();
  if (!sourceFile) return null;
  const geometry = meshGeometryFacts(sourceFile, measurement);
  return (
    <section
      className="model-information-geometry border-b border-border pb-4"
      aria-label={uiText("Whole source geometry")}
    >
      <h2 className="text-base font-semibold">{uiText("Whole source geometry")}</h2>
      {geometry.emptyMessage ? (
        <p className="mt-2 text-sm text-muted-foreground">{geometry.emptyMessage}</p>
      ) : (
        <dl className="model-information-facts">
          {geometry.facts.map(([label, value]) => (
            <Fragment key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </Fragment>
          ))}
        </dl>
      )}
      <GeometryReport
        modelId={modelId}
        fileId={sourceFile.file_type === "gcode" ? null : sourceFile.id}
        volume={sourceFile.metadata?.volume_measurement ?? null}
      />
    </section>
  );
}
