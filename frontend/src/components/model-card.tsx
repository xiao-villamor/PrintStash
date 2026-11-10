"use client";

import type { LibraryEntry } from "@/features/library/navigation-state";
import { formatNumber } from "@/lib/format";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { LibraryItemLink } from "@/features/library/navigation";
import { memo, useEffect, useState } from "react";
import { ModelListItem, FileRevisionStatus } from "@/types";
import { Box, Star, Tags, ScanSearch } from "lucide-react";
import { LibraryCardSelection } from "@/components/library-card-selection";
import { Button } from "@/components/ui/button";
import { useLibraryStar } from "@/features/library/mutations";
import { toast } from "@/lib/toast";
import { timeAgoShort } from "@/lib/format";
import { ProtectedThumbnail } from "@/components/protected-thumbnail";
import { Localized } from "@/components/ui/localized";
import "./library-items.css";
import { MODEL_DND_MIME, captureModelDrag } from "@/lib/model-dnd";

import {
  CARD_METRIC_STORAGE_KEY,
  CardMetricId,
  CardMetrics,
  readCardMetrics,
} from "@/lib/card-metrics";

function formatTime(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours > 0)
    return `${formatNumber(hours, { style: "unit", unit: "hour", unitDisplay: "narrow" })} ${formatNumber(minutes, { style: "unit", unit: "minute", unitDisplay: "narrow" })}`;
  return formatNumber(minutes, { style: "unit", unit: "minute", unitDisplay: "narrow" });
}

const REVISION_CONFIG = {
  known_good: {
    get label() {
      return uiText("Known Good");
    },
    classes: "text-success",
  },
  needs_test: {
    get label() {
      return uiText("Needs Test");
    },
    classes: "text-warning",
  },
  failed: {
    get label() {
      return uiText("Failed");
    },
    classes: "text-destructive",
  },
  archived: {
    get label() {
      return uiText("Archived");
    },
    classes: "text-muted-foreground",
  },
} satisfies Record<FileRevisionStatus, { label: string; classes: string }>;

const METRIC_CONFIG = {
  layer_height: {
    get abbr() {
      return uiText("LYR");
    },
    getValue: (m) =>
      m.print_summary?.layer_height_mm != null
        ? `${formatNumber(m.print_summary.layer_height_mm, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} mm`
        : "—",
  },
  print_time: {
    get abbr() {
      return uiText("TIME");
    },
    getValue: (m) =>
      m.print_summary?.estimated_time_s != null
        ? formatTime(m.print_summary.estimated_time_s)
        : "—",
  },
  filament_weight: {
    get abbr() {
      return uiText("WGT");
    },
    getValue: (m) =>
      m.print_summary?.filament_weight_g != null
        ? `${formatNumber(Math.round(m.print_summary.filament_weight_g))} g`
        : "—",
  },
  material: {
    get abbr() {
      return uiText("MAT");
    },
    getValue: (m) => m.print_summary?.material_type ?? "—",
  },
  slicer: {
    get abbr() {
      return uiText("SLR");
    },
    getValue: (m) => m.print_summary?.slicer_name ?? "—",
  },
  file_count: {
    get abbr() {
      return uiText("FILES");
    },
    getValue: (m) => `${m.file_count}`,
  },
} satisfies Record<CardMetricId, { abbr: string; getValue: (model: ModelListItem) => string }>;

function RevisionBadge({
  status,
  label,
}: {
  status: FileRevisionStatus | null | undefined;
  label?: string | null;
}) {
  useUiLocale();
  if (!status)
    return (
      <span className="bg-card/90 px-1.5 py-0.5 text-2xs text-muted-foreground">
        {uiText("library.noRevisionStatus")}
      </span>
    );
  const cfg = REVISION_CONFIG[status];
  const accessibleLabel = label
    ? uiText("Revision status: {status}; label: {label}", { status: cfg.label, label })
    : uiText("Revision status: {status}", { status: cfg.label });
  return (
    <span
      aria-label={accessibleLabel}
      title={accessibleLabel}
      className={`inline-flex max-w-full flex-wrap items-center gap-1 text-2xs font-medium px-1.5 py-0.5 bg-card/90 shrink-0 ${cfg.classes}`}
    >
      <span>{cfg.label}</span>
      {label && (
        <>
          <span aria-hidden="true">·</span>
          <span className="max-w-20 truncate">{label}</span>
        </>
      )}
    </span>
  );
}

function MetricCell({
  id,
  model,
  isLast,
}: {
  id: CardMetricId;
  model: ModelListItem;
  isLast: boolean;
}) {
  useUiLocale();
  const cfg = METRIC_CONFIG[id];
  return (
    <div className={`min-w-0 ${isLast ? "" : "pr-2"}`}>
      <p className="text-3xs font-normal text-muted-foreground uppercase tracking-wider mb-0.5">
        {cfg.abbr}
      </p>
      <p className="text-2xs font-normal text-foreground font-mono truncate">
        {cfg.getValue(model)}
      </p>
    </div>
  );
}

function ModelCardInner({
  model,
  origin,
  arriving = false,
  collectionLabel,
  metrics,
  selectable = false,
  selected = false,
  onToggleSelect,
  draggable = false,
  onEditTags,
  onIntent,
}: {
  model: ModelListItem;
  origin?: LibraryEntry;
  /** Turned up in the open view since it was last read: ease in once. */
  arriving?: boolean;
  collectionLabel?: string | null;
  metrics: CardMetrics;
  selectable?: boolean;
  selected?: boolean;
  onToggleSelect?: (id: number, range?: boolean) => void;
  draggable?: boolean;
  onEditTags?: (model: ModelListItem) => void;
  onIntent?: (id: number | null) => void;
}) {
  useUiLocale();
  const [dragging, setDragging] = useState(false);
  const starMutation = useLibraryStar();
  const starred =
    starMutation.isPending && starMutation.variables
      ? starMutation.variables.starred
      : model.starred;
  const starBusy = starMutation.isPending;
  const printerPresence = model.printer_presence ?? [];
  const hasPrinter = printerPresence.length > 0;
  const ps = model.print_summary;
  const canEditTags = model.effective_role === "edit" || model.effective_role === "admin";

  async function toggleStar() {
    if (starBusy) return;
    try {
      await starMutation.mutateAsync({ kind: "model", id: model.id, starred: !starred });
    } catch (error) {
      toast.error(error);
    }
  }

  return (
    <Localized>
      <article
        draggable={draggable}
        onDragStart={
          draggable
            ? (e) => {
                e.dataTransfer.setData(MODEL_DND_MIME, JSON.stringify(captureModelDrag(model)));
                e.dataTransfer.effectAllowed = "move";
                setDragging(true);
              }
            : undefined
        }
        onDragEnd={() => setDragging(false)}
        className={`${origin ? "" : "animate-card-in"} ${arriving ? "animate-card-arrive" : ""} group group/selection-card relative flex h-full flex-col min-w-0 bg-card border rounded transition-[color,background-color,border-color,opacity,transform] duration-fast active:scale-[0.99] overflow-hidden ${
          draggable ? "cursor-grab active:cursor-grabbing" : ""
        } ${dragging ? "opacity-40" : ""} ${
          selected
            ? "border-primary ring-2 ring-primary-soft"
            : "border-border hover:border-primary"
        }`}
      >
        {onToggleSelect && (
          <LibraryCardSelection
            selecting={selectable}
            checked={selected}
            onChange={() => onToggleSelect(model.id)}
            label={uiText("Select {value1}", { value1: String(model.name) })}
          />
        )}
        <button
          type="button"
          onClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
            void toggleStar();
          }}
          disabled={starBusy}
          aria-label={
            starred
              ? uiText("Remove {value1} from favorites", { value1: String(model.name) })
              : uiText("Add {value1} to favorites", { value1: String(model.name) })
          }
          className="absolute right-2 top-28 z-10 rounded bg-card/90 p-1.5 text-muted-foreground transition-[color,background-color,transform] duration-press ease-out hover:bg-card hover:text-primary active:scale-[0.98] disabled:opacity-50"
        >
          <Star className={`h-4 w-4 ${starred ? "fill-current text-primary" : ""}`} />
        </button>
        {!selectable && canEditTags && onEditTags && (
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              onEditTags(model);
            }}
            aria-label={
              model.tags.length > 0
                ? uiText("Edit tags for {value1}", { value1: String(model.name) })
                : uiText("Add tags to {value1}", { value1: String(model.name) })
            }
            title={model.tags.length > 0 ? uiText("Edit tags") : uiText("Add tags")}
            className="absolute right-10 top-28 z-10 bg-card/90 text-muted-foreground hover:bg-card hover:text-primary"
          >
            <Tags className="h-4 w-4" />
          </Button>
        )}
        <LibraryItemLink
          origin={origin}
          href={`/models/${model.id}`}
          aria-label={model.name}
          draggable={false}
          className="flex min-w-0 flex-col h-full overflow-hidden focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
          onPointerEnter={() => !selectable && onIntent?.(model.id)}
          onFocus={() => !selectable && onIntent?.(model.id)}
          onPointerLeave={() => onIntent?.(null)}
          onBlur={() => onIntent?.(null)}
          onClick={(e) => {
            if (selectable) {
              e.preventDefault();
              onToggleSelect?.(model.id, e.shiftKey);
            }
          }}
        >
          {/* Thumbnail */}
          <ProtectedThumbnail
            path={model.thumbnail_url}
            alt={model.name}
            fade
            className="library-preview relative overflow-hidden h-36 border-b border-border shrink-0"
            imageClassName="w-full h-full object-contain"
            placeholder={
              <div className="flex h-full w-full flex-col items-center justify-center gap-2 px-3 text-center">
                <Box className="h-6 w-6 text-muted-foreground" />
                <span className="text-2xs text-muted-foreground">
                  {uiText("library.previewMissing")}
                </span>
              </div>
            }
          >
            <div className="absolute right-2 top-2 max-w-[calc(100%-3rem)]">
              <RevisionBadge
                status={model.recommended_revision_status}
                label={model.recommended_revision_label}
              />
            </div>
            {hasPrinter && (
              <div className="absolute bottom-2 left-2">
                <span className="text-3xs font-medium text-success bg-card/90 px-1.5 py-0.5">
                  {uiText("On printer")}
                </span>
              </div>
            )}
          </ProtectedThumbnail>

          {/* Title + revision */}
          <div className="px-3 pt-4 pb-2 flex min-w-0 items-start justify-between gap-2">
            <h2
              title={model.name}
              className="min-w-0 break-words text-sm font-semibold text-foreground tracking-tight line-clamp-2 leading-snug"
            >
              {model.name}
            </h2>
          </div>

          {Boolean(model.similarity?.open_candidates) && (
            <p className="flex items-center gap-1 px-3 pb-2 text-xs text-primary">
              <ScanSearch className="h-3.5 w-3.5" aria-hidden="true" />
              {uiText("similarity.openCount", { count: model.similarity?.open_candidates ?? 0 })}
            </p>
          )}
          {/* Subtitle */}
          {(ps?.slicer_name || hasPrinter || ps?.material_type) && (
            <p className="px-3 pb-1 text-xs text-muted-foreground truncate">
              {[printerPresence[0]?.printer_name, ps?.material_type, ps?.slicer_name]
                .filter(Boolean)
                .join(" · ")}
            </p>
          )}

          {/* Configurable metrics grid */}
          <div className="px-3 pb-2">
            <div className="grid grid-cols-3 gap-2 border-t border-border pt-2">
              {metrics.map((id, i) => (
                <MetricCell key={id} id={id} model={model} isLast={i === 2} />
              ))}
            </div>
          </div>

          {/* Footer chips */}
          <div className="px-3 pb-3 mt-auto flex min-w-0 items-end justify-between gap-2 pt-1">
            <div className="flex flex-wrap gap-1 min-w-0">
              {collectionLabel && (
                <span
                  title={collectionLabel}
                  className="max-w-full truncate text-2xs text-muted-foreground"
                >
                  {collectionLabel.split("/").at(-1)}
                </span>
              )}
              {ps?.slicer_name && (
                <span className="text-3xs font-mono text-muted-foreground">GCODE</span>
              )}
              <span className="text-3xs font-mono text-muted-foreground">
                {uiText("counts.cardFiles", { count: model.file_count })}
              </span>
              {ps?.material_type && (
                <span className="text-3xs font-mono text-muted-foreground">{ps.material_type}</span>
              )}
              {model.tags.slice(0, 2).map((tag) => (
                <span
                  key={tag}
                  className="max-w-full truncate border border-border px-1 py-0.5 text-3xs text-muted-foreground"
                >
                  {tag}
                </span>
              ))}
              {model.tags.length > 2 && (
                <span className="text-3xs text-muted-foreground">+{model.tags.length - 2}</span>
              )}
            </div>
            <p className="text-2xs text-muted-foreground font-mono uppercase shrink-0">
              {timeAgoShort(model.updated_at)}
            </p>
          </div>
        </LibraryItemLink>
      </article>
    </Localized>
  );
}

const ModelCardMemo = memo(ModelCardInner);

export function ModelCard({
  model,
  origin,
  arriving,
  collectionLabel,
  selectable,
  selected,
  onToggleSelect,
  draggable,
  onEditTags,
  onIntent,
}: {
  model: ModelListItem;
  origin?: LibraryEntry;
  arriving?: boolean;
  collectionLabel?: string | null;
  selectable?: boolean;
  selected?: boolean;
  onToggleSelect?: (id: number, range?: boolean) => void;
  draggable?: boolean;
  onEditTags?: (model: ModelListItem) => void;
  /** A sustained pointer or focus on the card, or `null` once it leaves. */
  onIntent?: (id: number | null) => void;
}) {
  useUiLocale();
  // `readCardMetrics` already falls back to the defaults when there is no stored
  // preference, so it is the correct initial value rather than something to
  // re-read after mount.
  const [metrics, setMetrics] = useState(readCardMetrics);

  useEffect(() => {
    function onStorage(e: StorageEvent) {
      if (e.key === CARD_METRIC_STORAGE_KEY) setMetrics(readCardMetrics());
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  return (
    <Localized>
      <ModelCardMemo
        origin={origin}
        arriving={arriving}
        model={model}
        collectionLabel={collectionLabel}
        metrics={metrics}
        selectable={selectable}
        selected={selected}
        onToggleSelect={onToggleSelect}
        draggable={draggable}
        onEditTags={onEditTags}
        onIntent={onIntent}
      />
    </Localized>
  );
}
