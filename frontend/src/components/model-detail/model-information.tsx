import { GeometryReport } from "./geometry-report";
import { Fragment } from "react";
import { ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { meshGeometryFacts, type useMeshGeometryMeasurement } from "./use-mesh-geometry-facts";
import { currentLocale, uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import type { FileRead, ModelRead } from "@/types";
import { ModelDescription } from "./model-description";

/** Model facts in Overview describe the Model independently of the inspected Revision. */
export function ModelInformation({
  model,
  canEdit,
  onTags,
}: {
  model: ModelRead;
  canEdit: boolean;
  onTags: () => void;
}) {
  useUiLocale();
  const collectionNames = model.collection_label ? model.collection_label.split("/") : null;
  return (
    <section aria-label={uiText("Model details")} className="min-w-0 space-y-3">
      <h2 className="text-base font-semibold">{uiText("Model details")}</h2>
      {model.description?.trim() && <ModelDescription description={model.description} />}
      <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-3 text-sm [&_dt]:text-xs [&_dt]:font-medium [&_dt]:text-muted-foreground">
        {collectionNames && (
          <div className="min-w-0 space-y-1">
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
        <div className="col-start-2 space-y-1">
          <dt>{uiText("Created")}</dt>
          <dd>
            {new Date(model.created_at).toLocaleDateString(currentLocale(), {
              dateStyle: "medium",
            })}
          </dd>
        </div>
        <div className="col-span-full min-w-0 space-y-1">
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
      </dl>
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
