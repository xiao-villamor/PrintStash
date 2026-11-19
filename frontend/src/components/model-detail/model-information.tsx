import { Fragment, useId, useLayoutEffect, useRef, useState } from "react";
import { ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { meshGeometryFacts, type useMeshGeometryMeasurement } from "./use-mesh-geometry-facts";
import { currentLocale, uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import type { FileRead, ModelRead } from "@/types";
import { safeHttpUrl } from "./source-url";

/** Model facts stay beside its source preview, independently of the inspected G-code. */
export function ModelInformation({
  model,
  sourceFile,
  measurement = "unavailable",
  canEdit,
  onEdit,
  onTags,
  onFiles,
  onRevisions,
}: {
  model: ModelRead;
  sourceFile: FileRead | null;
  measurement?: ReturnType<typeof useMeshGeometryMeasurement>;
  canEdit: boolean;
  onEdit: () => void;
  onTags: () => void;
  onFiles: () => void;
  onRevisions: () => void;
}) {
  useUiLocale();
  const sourceUrl = model.source_url ? safeHttpUrl(model.source_url) : null;
  const geometry = meshGeometryFacts(sourceFile, measurement);
  const gcodeCount = model.files.filter((file) => file.file_type === "gcode").length;
  return (
    <section className="model-information" aria-label={uiText("About this model")}>
      <div className="model-information-about">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">{uiText("About this model")}</h2>
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
      </div>
      {sourceFile && (
        <div className="model-information-geometry">
          <h2 className="text-sm font-semibold">{uiText("Whole source geometry")}</h2>
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
        </div>
      )}
    </section>
  );
}

function ModelDescription({ description }: { description: string }) {
  const id = useId();
  const content = useRef<HTMLParagraphElement>(null);
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
      <p
        id={id}
        ref={content}
        className={`whitespace-pre-wrap wrap-anywhere text-sm leading-5 text-muted-foreground ${expanded ? "" : "line-clamp-4"}`}
      >
        {description}
      </p>
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
