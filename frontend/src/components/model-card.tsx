"use client";

import type { LibraryEntry } from "@/features/library/navigation-state";
import { formatDuration, formatNumber } from "@/lib/format";
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
        ? formatDuration(m.print_summary.estimated_time_s)
        : "—",
  },
  filament_weight: {
    get abbr() {
      return uiText("WGT");
    },
    getValue: (m) =>
      m.print_summary?.filament_weight_g != null
        ? `${formatNumber(m.print_summary.filament_weight_g, { maximumFractionDigits: 1 })} g`
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
      <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
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
      className="inline-flex max-w-full items-center gap-1 text-xs text-foreground"
    >
      <span
        className={`h-1.5 w-1.5 shrink-0 rounded-full ${status === "needs_test" ? "bg-warning" : status === "failed" ? "bg-destructive" : "bg-muted-foreground/60"}`}
        aria-hidden
      />
      <span>{status === "known_good" ? uiText("Ready") : cfg.label}</span>
    </span>
  );
}

function MetricCell({ id, model }: { id: CardMetricId; model: ModelListItem }) {
  useUiLocale();
  const cfg = METRIC_CONFIG[id];
  const value = cfg.getValue(model);
  if (value === "—") return null;
  return (
    <span title={`${cfg.abbr}: ${value}`} aria-label={`${cfg.abbr}: ${value}`}>
      {value}
    </span>
  );
}

function ModelCardInner({
  model,
  origin,
  arriving = false,
  metrics,
  selectable = false,
  selected = false,
  onToggleSelect,
  draggable = false,
  onEditTags,
  onIntent,
  favorites = false,
  onInspect,
  inspected = false,
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
  favorites?: boolean;
  onInspect?: (id: number, opener: HTMLElement) => void;
  inspected?: boolean;
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
  const factMetrics: readonly CardMetricId[] =
    metrics.join(",") === "layer_height,print_time,filament_weight"
      ? ["print_time", "filament_weight", "layer_height"]
      : metrics;
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
        data-favorites={favorites || undefined}
        data-inspected={inspected || undefined}
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
        className={`${origin ? "" : "animate-card-in"} ${arriving ? "animate-card-arrive" : ""} library-model-card group group/selection-card relative flex h-full flex-col min-w-0 bg-card border rounded transition-[color,background-color,border-color,opacity,transform] duration-fast active:scale-[0.99] overflow-hidden ${
          draggable ? "cursor-grab active:cursor-grabbing" : ""
        } ${dragging ? "opacity-40" : ""} ${
          selected
            ? "border-border bg-accent text-accent-foreground"
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
        {!favorites && (
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
        )}
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
            data-library-card-tags
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
            } else if (
              onInspect &&
              e.button === 0 &&
              !e.metaKey &&
              !e.ctrlKey &&
              !e.shiftKey &&
              !e.altKey
            ) {
              e.preventDefault();
              onInspect(model.id, e.currentTarget);
            }
          }}
        >
          {/* Thumbnail */}
          <ProtectedThumbnail
            path={model.thumbnail_url}
            alt={model.name}
            fade
            className="library-preview relative overflow-hidden border-b border-border shrink-0 h-36"
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
            {!favorites && (
              <div className="absolute right-2 top-2 max-w-[calc(100%-3rem)]">
                <RevisionBadge
                  status={model.recommended_revision_status}
                  label={model.recommended_revision_label}
                />
              </div>
            )}
            {hasPrinter && (
              <div className="absolute bottom-2 left-2">
                <span
                  title={printerPresence.map((presence) => presence.printer_name).join(", ")}
                  className="text-3xs font-medium text-success bg-card/90 px-1.5 py-0.5"
                >
                  {uiText("On printer")}
                </span>
              </div>
            )}
          </ProtectedThumbnail>

          {favorites ? (
            <div className="library-card-content flex flex-1 flex-col">
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
                  {uiText("similarity.openCount", {
                    count: model.similarity?.open_candidates ?? 0,
                  })}
                </p>
              )}
              <div className="mx-3 mt-1 flex min-h-8 flex-wrap items-center gap-1.5 border-t border-border pt-2 text-xs">
                {model.recommended_revision_label ? (
                  <>
                    <span className="font-mono font-semibold">
                      {model.recommended_revision_label}
                    </span>
                    <span className="text-muted-foreground">★ {uiText("Recommended")}</span>
                  </>
                ) : (
                  <span className="text-muted-foreground">
                    {model.recommended_revision_status
                      ? REVISION_CONFIG[model.recommended_revision_status].label
                      : uiText("library.noRevisionStatus")}
                  </span>
                )}
              </div>
              <div className="library-card-facts flex flex-wrap gap-x-1.5 gap-y-1 px-3 pt-2 font-mono text-2xs text-foreground">
                {metrics.map((id) => (
                  <MetricCell key={id} id={id} model={model} />
                ))}
              </div>
            </div>
          ) : (
            <div className="library-card-content library-card-standard">
              <h2 title={model.name}>
                <span>{model.name}</span>
              </h2>
              {Boolean(model.similarity?.open_candidates) && (
                <p className="flex items-center gap-1 text-xs text-primary">
                  <ScanSearch className="h-3.5 w-3.5" aria-hidden="true" />
                  {uiText("similarity.openCount", {
                    count: model.similarity?.open_candidates ?? 0,
                  })}
                </p>
              )}
              <div className="library-card-revision-summary">
                <div className="library-card-revision-line">
                  <strong>
                    {!model.recommended_revision_label && model.recommended_revision_status && (
                      <Star className="mr-1 inline-block h-3 w-3 fill-current text-muted-foreground" />
                    )}
                    {model.recommended_revision_label ??
                      (model.recommended_revision_status
                        ? uiText("Recommended revision")
                        : uiText("Revision"))}
                  </strong>
                  {model.recommended_revision_label && (
                    <span>
                      <Star className="h-3 w-3 fill-current" />
                      {uiText("Recommended")}
                    </span>
                  )}
                </div>
                <p className="library-card-result">{uiText("Print results not available")}</p>
                <div className="library-card-facts">
                  {factMetrics.map((id) => (
                    <MetricCell key={id} id={id} model={model} />
                  ))}
                  {ps?.material_type && !metrics.includes("material") && (
                    <span>{ps.material_type}</span>
                  )}
                  {!ps && <span className="text-muted-foreground">{uiText("Not recorded")}</span>}
                </div>
              </div>
            </div>
          )}
        </LibraryItemLink>
        {favorites && (
          <div className="flex items-center justify-between gap-2 px-3 pb-3 pt-1">
            <Button variant="outline" size="xs" asChild>
              <LibraryItemLink origin={origin} href={`/models/${model.id}`}>
                {uiText("Open")}
              </LibraryItemLink>
            </Button>
            <Button
              variant="ghost"
              size="xs"
              disabled={starBusy}
              onClick={() => void toggleStar()}
              aria-label={uiText("Remove {value1} from favorites", { value1: model.name })}
            >
              {uiText("Remove favorite")}
            </Button>
          </div>
        )}
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
  favorites = false,
  onInspect,
  inspected = false,
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
  favorites?: boolean;
  onInspect?: (id: number, opener: HTMLElement) => void;
  inspected?: boolean;
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
        favorites={favorites}
        onInspect={onInspect}
        inspected={inspected}
      />
    </Localized>
  );
}
