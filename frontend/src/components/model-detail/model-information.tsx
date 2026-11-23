import { MarkdownView } from "@/components/markdown-view";
import { ProjectProfile } from "./project-profile";
import { RelatedProfiles } from "./related-profiles";
import { GeometryReport } from "./geometry-report";
import { ModelSourceSummary } from "./source-summary";
import { Fragment, useId, useLayoutEffect, useRef, useState } from "react";
import { ChevronRight, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { meshGeometryFacts, type useMeshGeometryMeasurement } from "./use-mesh-geometry-facts";
import { currentLocale, uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import type { FileRead, ModelRead } from "@/types";
import { safeHttpUrl } from "./source-url";

/** Model facts in Overview describe the Model independently of the inspected Revision. */
export function ModelInformation({
  model,
  sourceFile,
  canEdit,
  onEdit,
  onTags,
}: {
  model: ModelRead;
  sourceFile: FileRead | null;
  canEdit: boolean;
  onEdit: () => void;
  onTags: () => void;
}) {
  useUiLocale();
  const sourceUrl = model.source_url ? safeHttpUrl(model.source_url) : null;
  const collectionNames = model.collection_label ? model.collection_label.split("/") : null;
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
      {model.description && (
        <ModelDescription key={model.description} description={model.description} />
      )}
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
      {sourceFile?.file_type === "3mf" && (
        <ProjectProfile modelId={model.id} fileId={sourceFile.id} />
      )}
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

function ModelDescription({ description }: { description: string }) {
  const id = useId();
  const content = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [overflows, setOverflows] = useState(false);
  useLayoutEffect(() => {
    const element = content.current;
    if (!element) return;
    const measure = () => {
      const lineHeight = Number.parseFloat(getComputedStyle(element).lineHeight);
      setOverflows(element.scrollHeight > lineHeight * 4 + 1);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [description]);
  return (
    <div>
      <div
        id={id}
        ref={content}
        className={`wrap-anywhere text-sm leading-5 text-muted-foreground ${expanded ? "" : "max-h-20 overflow-hidden"}`}
      >
        <MarkdownView source={description} />
      </div>
      {overflows && (
        <Button
          size="sm"
          variant="ghost"
          className="mt-1"
          aria-controls={id}
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          {uiText(expanded ? "Show less" : "Show more")}
        </Button>
      )}
    </div>
  );
}
