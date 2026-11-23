import { MarkdownView } from "@/components/markdown-view";
import { ProjectProfile } from "./project-profile";
import { RelatedProfiles } from "./related-profiles";
import { GeometryReport } from "./geometry-report";
import { ModelSourceSummary } from "./source-summary";
import { Fragment, useId, useLayoutEffect, useRef, useState } from "react";
import { ExternalLink } from "lucide-react";
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
  onFiles,
  onRevisions,
}: {
  model: ModelRead;
  sourceFile: FileRead | null;
  canEdit: boolean;
  onEdit: () => void;
  onTags: () => void;
  onFiles: () => void;
  onRevisions: () => void;
}) {
  useUiLocale();
  const sourceUrl = model.source_url ? safeHttpUrl(model.source_url) : null;
  const gcodeCount = model.files.filter((file) => file.file_type === "gcode").length;
  return (
    <section aria-label={uiText("About this model")}>
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-base font-semibold">{uiText("About this model")}</h2>
        {canEdit && (
          <Button size="sm" variant="ghost" onClick={onEdit}>
            {uiText("Edit details")}
          </Button>
        )}
      </div>
      {model.description && (
        <ModelDescription key={model.description} description={model.description} />
      )}
      <dl className="model-information-facts">
        {sourceUrl && (
          <>
            <dt>{uiText("Source")}</dt>
            <dd>
              <a
                href={sourceUrl}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-foreground underline-offset-4 hover:underline"
              >
                <ExternalLink className="h-3 w-3" />
                {uiText("Source model")}
              </a>
            </dd>
          </>
        )}
        {model.collection_label && (
          <>
            <dt>{uiText("Collection")}</dt>
            <dd className="wrap-anywhere">{model.collection_label}</dd>
          </>
        )}
        <dt>{uiText("Tags")}</dt>
        <dd className="flex flex-wrap items-center gap-1">
          {!canEdit && model.tags.length === 0 && uiText("None")}
          {model.tags.map((tag) => (
            <span className="bg-muted px-1.5 py-0.5 text-xs" key={tag}>
              {tag}
            </span>
          ))}
          {canEdit && (
            <Button
              size="xs"
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
        <dt>{uiText("Files")}</dt>
        <dd className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <button type="button" className="detail-file-link" onClick={onFiles}>
            {uiText("Source files")} ({model.files.length - gcodeCount})
          </button>
          <button type="button" className="detail-file-link" onClick={onRevisions}>
            {uiText("G-code revisions")} ({gcodeCount})
          </button>
        </dd>
        <dt>{uiText("Created")}</dt>
        <dd>{new Date(model.created_at).toLocaleDateString(currentLocale())}</dd>
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
    <div className="mt-2">
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
