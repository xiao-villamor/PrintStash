import { ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { formatNumber } from "@/lib/format";
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
}: {
  model: ModelRead;
  sourceFile: FileRead | null;
  canEdit: boolean;
  onEdit: () => void;
  onTags: () => void;
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
          <p className="mt-2 whitespace-pre-wrap wrap-anywhere text-sm text-muted-foreground">
            {model.description}
          </p>
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
            {model.tags.map((tag) => (
              <span className="bg-muted px-1.5 py-0.5 text-xs" key={tag}>
                {tag}
              </span>
            ))}
            {canEdit && (
              <Button
                size="sm"
                variant="ghost"
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
          <dd>
            {uiText("{total} total · {sources} source · {revisions} G-code", {
              total: String(model.files.length),
              sources: String(model.files.length - gcodeCount),
              revisions: String(gcodeCount),
            })}
          </dd>
          <dt>{uiText("Created")}</dt>
          <dd>{new Date(model.created_at).toLocaleDateString(currentLocale())}</dd>
        </dl>
      </div>
      <div className="model-information-geometry">
        <h2 className="text-sm font-semibold">{uiText("Geometry")}</h2>
        <dl className="model-information-facts">
          <dt>{uiText("Dimensions")}</dt>
          <dd>{dimensions}</dd>
          <dt>{uiText("Volume")}</dt>
          <dd>
            {geometry?.volume_mm3 != null
              ? uiText("{value} cm³", {
                  value: formatNumber(geometry.volume_mm3 / 1000, { maximumFractionDigits: 2 }),
                })
              : uiText("Not recorded")}
          </dd>
          <dt>{uiText("Triangles")}</dt>
          <dd>
            {geometry?.triangle_count != null
              ? formatNumber(geometry.triangle_count)
              : uiText("Not recorded")}
          </dd>
        </dl>
      </div>
    </section>
  );
}
