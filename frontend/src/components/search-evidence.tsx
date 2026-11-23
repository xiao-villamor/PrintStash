import { useViewportAssetUrl } from "@/lib/use-viewport-admission";
import { Box, Boxes, FileText, Folder } from "lucide-react";
import "./library-items.css";
import { cn } from "@/lib/utils";
import { cardDimensions } from "@/features/library/card-detail";
import { displayModelName } from "@/lib/display-name";
import { filterValueText } from "@/lib/filter-labels";
import { formatCalendarDate, formatDuration, formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { Link } from "@/lib/link";
import type { SearchResult, SearchSubjectType } from "@/types/search";

export function SearchSubjectPreview({
  path,
  subjectType,
  large = false,
}: {
  path: string | null | undefined;
  subjectType: SearchSubjectType;
  large?: boolean;
}) {
  const { url: source, ref } = useViewportAssetUrl(path);
  const frame = large ? "aspect-[4/3] w-full" : "size-12";
  const Icon =
    subjectType === "collection"
      ? Folder
      : subjectType === "multipart_model"
        ? Boxes
        : subjectType === "document"
          ? FileText
          : Box;
  return (
    <div
      ref={ref}
      className={cn(
        frame,
        "library-preview relative flex shrink-0 items-center justify-center overflow-hidden text-muted-foreground",
      )}
      aria-hidden
    >
      {source ? (
        <img
          src={source}
          alt=""
          loading="lazy"
          className={
            large ? "library-card-thumbnail absolute inset-0" : "size-full object-contain p-1"
          }
        />
      ) : (
        <Icon className={large ? "size-10" : "size-6"} />
      )}
    </div>
  );
}

export function SearchResultItem({ item, view }: { item: SearchResult; view: "grid" | "list" }) {
  const { t } = useI18n();
  const grid = view === "grid";
  const isModel = item.subject_type === "model";
  const model = isModel ? item.model : null;
  const collection = model?.collection_label;
  const dimensions = model ? cardDimensions(model.bounding_box) : null;
  const revision = [
    model?.recommended_revision_label,
    model?.recommended_revision_status &&
      filterValueText("revision_status", model.recommended_revision_status),
  ]
    .filter(Boolean)
    .join(" · ");
  const summary = model?.print_summary;
  const printFacts = summary
    ? [
        summary.estimated_time_s != null ? formatDuration(summary.estimated_time_s) : null,
        summary.filament_weight_g != null ? `${formatNumber(summary.filament_weight_g)} g` : null,
        summary.layer_height_mm != null ? `${formatNumber(summary.layer_height_mm)} mm` : null,
        summary.material_type,
      ]
        .filter(Boolean)
        .join(" · ")
    : null;
  const typeId = `search-result-type-${item.subject_type}-${item.subject_id}`;

  return (
    <li className="min-w-0">
      <Link
        href={item.href}
        aria-label={item.name}
        aria-describedby={typeId}
        className={cn(
          "flex h-full min-w-0 overflow-hidden transition-[border-color,scale] duration-press ease-out active:scale-[0.99] motion-reduce:active:scale-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
          grid
            ? "flex-col border border-border bg-card hover:border-border-strong"
            : "items-start gap-3 px-2 py-2 sm:items-center",
        )}
      >
        <SearchSubjectPreview
          path={item.model?.thumbnail_url}
          subjectType={item.subject_type}
          large={grid}
        />
        <div
          className={cn(
            "flex min-w-0 flex-1 gap-1",
            grid ? "flex-col px-3 pb-2.5 pt-2.5" : "flex-col sm:flex-row sm:items-center sm:gap-4",
          )}
        >
          <div className={cn("min-w-0", !grid && "sm:flex-1")}>
            <h2
              title={item.name}
              className="line-clamp-2 break-words text-sm font-semibold leading-snug tracking-tight text-foreground"
            >
              {isModel ? displayModelName(item.name) : item.name}
            </h2>
            <p id={typeId} className={isModel ? "sr-only" : "library-card-context"}>
              {t(`aiSearch.type.${item.subject_type}`)}
            </p>
            {model && (
              <p className="library-card-context" title={collection ?? undefined}>
                {collection
                  ? collection.slice(collection.lastIndexOf("/") + 1)
                  : t("counts.files", { count: model.file_count })}
              </p>
            )}
          </div>
          {(revision || printFacts) && (
            <div className={cn("min-w-0", !grid && "sm:flex-1")}>
              {revision && (
                <p className="library-card-context" title={revision}>
                  {revision}
                </p>
              )}
              {printFacts && (
                <p className="library-card-context font-mono" title={printFacts}>
                  {printFacts}
                </p>
              )}
            </div>
          )}
          {model && (
            <p className={cn("library-card-meta", grid ? "[&]:mt-auto" : "sm:ml-auto sm:shrink-0")}>
              {dimensions && <span title={dimensions}>{dimensions}</span>}
              <time className="ml-auto" dateTime={model.updated_at}>
                {formatCalendarDate(model.updated_at)}
              </time>
            </p>
          )}
        </div>
      </Link>
    </li>
  );
}
