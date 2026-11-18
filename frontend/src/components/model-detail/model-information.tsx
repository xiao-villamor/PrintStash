import { Fragment, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { formatCalendarDate, formatNumber } from "@/lib/format";
import { currentLocale, uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import type { FileRead, ModelRead } from "@/types";
import { safeHttpUrl } from "./source-url";

/** Model facts stay beside its source preview, independently of the inspected G-code. */
export function ModelInformation({
  model,
  sourceFile,
  canEdit,
  onEdit,
  onTags,
  layout = "below-preview",
}: {
  model: ModelRead;
  sourceFile: FileRead | null;
  canEdit: boolean;
  onEdit: () => void;
  onTags: () => void;
  layout?: ModelInformationLayout;
}) {
  useUiLocale();
  const sourceUrl = model.source_url ? safeHttpUrl(model.source_url) : null;
  const geometry = sourceFile?.metadata;
  const gcodeCount = model.files.filter((file) => file.file_type === "gcode").length;
  const dimensions =
    geometry &&
    geometry.bbox_x_mm !== null &&
    geometry.bbox_y_mm !== null &&
    geometry.bbox_z_mm !== null
      ? `${formatNumber(geometry.bbox_x_mm)}mm × ${formatNumber(geometry.bbox_y_mm)}mm × ${formatNumber(geometry.bbox_z_mm)}mm`
      : uiText("Not recorded");
  const inPanel = layout === "panel";
  const description = model.description && (
    <ModelDescription key={model.description} description={model.description} />
  );
  const collection = model.collection_label;
  const details: Fact[] = [];
  if (sourceUrl) {
    details.push({
      label: uiText("Source"),
      value: (
        <a
          href={sourceUrl}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-foreground underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <ExternalLink className="h-3 w-3" />
          {uiText("Source model")}
        </a>
      ),
    });
  }
  if (collection || inPanel) {
    details.push({
      label: uiText("Collection"),
      valueClassName: inPanel ? undefined : "wrap-anywhere",
      value:
        collection && inPanel ? (
          <span className="block truncate" title={collection}>
            {collection.slice(collection.lastIndexOf("/") + 1).trim()}
          </span>
        ) : (
          (collection ?? uiText("None"))
        ),
    });
  }
  details.push(
    {
      label: uiText("Tags"),
      value: (
        <>
          {model.tags.map((tag) => (
            <span className="bg-muted px-1.5 py-0.5 text-xs" key={tag}>
              {tag}
            </span>
          ))}
          {canEdit && (
            <Button
              size="sm"
              variant="ghost"
              className={inPanel ? "-mr-3" : undefined}
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
          {inPanel && !canEdit && model.tags.length === 0 && uiText("None")}
        </>
      ),
      valueClassName: `flex flex-wrap items-center gap-1 ${inPanel ? "justify-end" : ""}`,
    },
    {
      label: uiText("Files"),
      value: inPanel
        ? formatNumber(model.files.length)
        : uiText("{total} total · {sources} source · {revisions} G-code", {
            total: String(model.files.length),
            sources: String(model.files.length - gcodeCount),
            revisions: String(gcodeCount),
          }),
    },
    {
      label: uiText("Created"),
      value: inPanel
        ? formatCalendarDate(model.created_at)
        : new Date(model.created_at).toLocaleDateString(currentLocale()),
    },
  );
  if (inPanel && !canEdit) {
    details.push({ label: uiText("G-code"), value: uiText("None yet") });
  }

  const geometryFacts: Fact[] = [
    { label: uiText("Dimensions"), value: dimensions },
    {
      label: uiText("Volume"),
      value:
        geometry?.volume_mm3 != null
          ? uiText("{value} cm³", {
              value: formatNumber(geometry.volume_mm3 / 1000, { maximumFractionDigits: 2 }),
            })
          : uiText("Not recorded"),
    },
    {
      label: uiText("Triangles"),
      value:
        geometry?.triangle_count != null
          ? formatNumber(geometry.triangle_count)
          : uiText("Not recorded"),
    },
  ];
  return (
    <section
      className={inPanel ? "space-y-5" : "model-information"}
      aria-label={uiText("About this model")}
    >
      {inPanel && description}
      <div className="model-information-about">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">
            {inPanel ? uiText("Details") : uiText("About this model")}
          </h2>
          {canEdit && (
            <Button
              size="sm"
              variant="ghost"
              className={inPanel ? "-mr-3" : undefined}
              onClick={onEdit}
              data-edit-model-details
            >
              {uiText("Edit details")}
            </Button>
          )}
        </div>
        {!inPanel && description}
        <ModelFacts facts={details} layout={layout} />
      </div>
      <div className={inPanel ? "" : "model-information-geometry"}>
        <h2 className="text-sm font-semibold">{uiText("Geometry")}</h2>
        <ModelFacts facts={geometryFacts} layout={layout} mono />
      </div>
    </section>
  );
}

type ModelInformationLayout = "below-preview" | "panel";

type Fact = { label: string; value: ReactNode; valueClassName?: string };

function ModelFacts({
  facts,
  layout,
  mono = false,
}: {
  facts: Fact[];
  layout: ModelInformationLayout;
  mono?: boolean;
}) {
  const inPanel = layout === "panel";
  return (
    <dl className={inPanel ? "mt-1 divide-y divide-border text-[13px]" : "model-information-facts"}>
      {facts.map(({ label, value, valueClassName }) =>
        inPanel ? (
          <div
            key={label}
            className="grid grid-cols-[auto_minmax(0,1fr)] items-baseline gap-x-4 py-2"
          >
            <dt className="text-muted-foreground">{label}</dt>
            <dd
              className={`min-w-0 wrap-anywhere text-right ${mono ? "font-mono" : ""} ${valueClassName ?? ""}`}
            >
              {value}
            </dd>
          </div>
        ) : (
          <Fragment key={label}>
            <dt>{label}</dt>
            <dd className={valueClassName}>{value}</dd>
          </Fragment>
        ),
      )}
    </dl>
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
