"use client";

import { formatNumber } from "@/lib/format";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { Link } from "@/lib/link";
import { useRouter } from "@/lib/navigation";
import { memo, useEffect, useState } from "react";
import { ModelListItem, FileRevisionStatus } from "@/types";
import { FileText, Star, Tags, ScanSearch } from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { Button } from "@/components/ui/button";
import { getAssetUrl, starModel, unstarModel } from "@/lib/api";
import { toast } from "@/lib/toast";
import { timeAgoShort } from "@/lib/format";
import { useAuthenticatedAssetUrl } from "@/lib/use-authenticated-asset-url";
import { Localized } from "@/components/ui/localized";
import { MODEL_DND_MIME } from "@/lib/model-dnd";

// STL blobs already warmed this session (the /stl endpoint serves
// Cache-Control'd responses, so a hover fetch lands in the HTTP cache and the
// viewer's loader reads from disk instead of the network).
const warmedMeshFiles = new Set<number>();

function warmStl(meshFileId: number | null) {
  if (meshFileId == null || warmedMeshFiles.has(meshFileId)) return;
  warmedMeshFiles.add(meshFileId);
  fetch(getAssetUrl(`/api/v1/files/${meshFileId}/stl`)).catch(() => {
    warmedMeshFiles.delete(meshFileId);
  });
}
import {
  CARD_METRIC_STORAGE_KEY,
  CardMetricId,
  CardMetrics,
  readCardMetrics,
} from "@/lib/card-metrics";

/** An optimistic star toggle, remembered against the server value it overrode. */
interface StarOverride {
  /** The `model.starred` this override was made against. */
  base: boolean;
  /** What the card shows for as long as the override stands. */
  value: boolean;
}

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
    classes:
      "bg-green-50 dark:bg-green-950/50 text-green-700 border-green-200 dark:border-green-800",
  },
  needs_test: {
    get label() {
      return uiText("Needs Test");
    },
    classes:
      "bg-amber-50 dark:bg-amber-950/50 text-amber-700 border-amber-200 dark:border-amber-800",
  },
  failed: {
    get label() {
      return uiText("Failed");
    },
    classes: "bg-red-50 dark:bg-red-950/50 text-red-700 border-red-200 dark:border-red-800",
  },
  archived: {
    get label() {
      return uiText("Archived");
    },
    classes: "bg-muted text-muted-foreground border-border",
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
  if (!status) return null;
  const cfg = REVISION_CONFIG[status];
  const accessibleLabel = label
    ? uiText("Revision status: {status}; label: {label}", { status: cfg.label, label })
    : uiText("Revision status: {status}", { status: cfg.label });
  return (
    <span
      aria-label={accessibleLabel}
      title={accessibleLabel}
      className={`inline-flex items-center gap-1 text-3xs font-mono font-semibold px-1.5 py-0.5 rounded border uppercase tracking-tight shrink-0 ${cfg.classes}`}
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
    <div className={`py-2 px-1 text-center bg-muted/50 ${isLast ? "" : "border-r border-border"}`}>
      <p className="text-3xs font-bold text-muted-foreground uppercase tracking-wider mb-0.5">
        {cfg.abbr}
      </p>
      <p className="text-2xs font-bold text-foreground font-mono truncate">{cfg.getValue(model)}</p>
    </div>
  );
}

function ModelCardInner({
  model,
  metrics,
  selectable = false,
  selected = false,
  onToggleSelect,
  draggable = false,
  onEditTags,
}: {
  model: ModelListItem;
  metrics: CardMetrics;
  selectable?: boolean;
  selected?: boolean;
  onToggleSelect?: (id: number, range?: boolean) => void;
  draggable?: boolean;
  onEditTags?: (model: ModelListItem) => void;
}) {
  useUiLocale();
  const router = useRouter();
  const [dragging, setDragging] = useState(false);
  // The card owns an optimistic star only until the server says otherwise: a
  // fresh `model.starred` (list refetch, or the star toggled on the detail
  // page) no longer matches `base` and supersedes the override, so nothing has
  // to copy the prop into state.
  const [starOverride, setStarOverride] = useState<StarOverride | null>(null);
  const starred =
    starOverride !== null && starOverride.base === model.starred
      ? starOverride.value
      : model.starred;
  const [starBusy, setStarBusy] = useState(false);
  const thumb = useAuthenticatedAssetUrl(model.thumbnail_url);
  // Lazy thumbnails used to snap in at full opacity the instant their bytes
  // arrived. Fade each one in on load so scrolling/searching settles smoothly
  // instead of popping card by card.
  const [thumbLoaded, setThumbLoaded] = useState(false);
  const printerPresence = model.printer_presence ?? [];
  const hasPrinter = printerPresence.length > 0;
  const ps = model.print_summary;
  const canEditTags = model.effective_role === "edit" || model.effective_role === "admin";

  async function toggleStar() {
    if (starBusy) return;
    const next = !starred;
    setStarOverride({ base: model.starred, value: next });
    setStarBusy(true);
    try {
      await (next ? starModel(model.id) : unstarModel(model.id));
    } catch (error) {
      // `!next` is exactly what the card showed before this toggle.
      setStarOverride({ base: model.starred, value: !next });
      toast.error(error);
    } finally {
      setStarBusy(false);
    }
  }

  // Hover intent: prefetch the detail route (server-rendered payload) and warm
  // the STL into the browser cache so the 3D viewer opens without a spinner.
  function handleHover() {
    router.prefetch(`/models/${model.id}`);
    warmStl(model.mesh_file_id ?? null);
  }

  return (
    <Localized>
      <article
        draggable={draggable}
        onDragStart={
          draggable
            ? (e) => {
                e.dataTransfer.setData(MODEL_DND_MIME, String(model.id));
                e.dataTransfer.effectAllowed = "move";
                setDragging(true);
              }
            : undefined
        }
        onDragEnd={() => setDragging(false)}
        className={`animate-card-in group relative flex h-full flex-col bg-card border rounded transition-[color,background-color,border-color,box-shadow,opacity,transform] duration-fast active:scale-[0.99] overflow-hidden ${
          draggable ? "cursor-grab active:cursor-grabbing" : ""
        } ${dragging ? "opacity-40" : ""} ${
          selected
            ? "border-primary ring-2 ring-primary-soft"
            : "border-border hover:border-primary"
        }`}
        onMouseEnter={handleHover}
        onTouchStart={handleHover}
      >
        {selectable && (
          <div className="absolute left-2 top-2 z-10">
            <Checkbox
              checked={selected}
              onChange={() => onToggleSelect?.(model.id)}
              ariaLabel={uiText("Select {value1}", { value1: String(model.name) })}
            />
          </div>
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
          className="absolute right-2 top-2 z-10 rounded bg-card/90 p-2 text-muted-foreground shadow-sm transition-[color,background-color,transform] duration-press ease-out hover:bg-card hover:text-primary active:scale-[0.98] disabled:opacity-50"
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
            className="absolute right-2 top-12 z-10 bg-card/90 text-muted-foreground shadow-sm hover:bg-card hover:text-primary"
          >
            <Tags className="h-4 w-4" />
          </Button>
        )}
        <Link
          href={`/models/${model.id}`}
          draggable={false}
          className="flex flex-col h-full overflow-hidden"
          onClick={(e) => {
            if (selectable) {
              e.preventDefault();
              onToggleSelect?.(model.id, e.shiftKey);
            }
          }}
        >
          {/* Thumbnail */}
          <div className="bg-muted relative overflow-hidden h-48 border-b border-border shrink-0">
            {thumb ? (
              <img
                alt={model.name}
                draggable={false}
                className={`w-full h-full object-cover transition-opacity duration-slow ease-out ${
                  thumbLoaded ? "opacity-90 group-hover:opacity-100" : "opacity-0"
                }`}
                src={thumb}
                loading="lazy"
                decoding="async"
                onLoad={() => setThumbLoaded(true)}
                // Cached images can finish before React attaches onLoad; catch that
                // case so they don't stay stuck at opacity-0.
                ref={(node) => {
                  if (node?.complete && node.naturalWidth > 0) setThumbLoaded(true);
                }}
              />
            ) : (
              <div className="flex h-full w-full items-center justify-center">
                <FileText className="h-10 w-10 text-muted-foreground/40" />
              </div>
            )}
            {hasPrinter && (
              <div className="absolute bottom-2 right-2">
                <span className="text-3xs font-bold text-green-700 bg-green-50 dark:bg-green-950/60 px-1.5 py-0.5 border border-green-200 dark:border-green-800 rounded-sm uppercase">
                  {uiText("On printer")}
                </span>
              </div>
            )}
          </div>

          {/* Title + revision */}
          <div className="px-3 pt-3 pb-1 flex items-start justify-between gap-2">
            <h2
              title={model.name}
              className="text-sm font-bold text-foreground uppercase tracking-tight line-clamp-2 leading-tight"
            >
              {model.name}
            </h2>
            <RevisionBadge
              status={model.recommended_revision_status}
              label={model.recommended_revision_label}
            />
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
            <div className="grid grid-cols-3 border border-border rounded-sm overflow-hidden">
              {metrics.map((id, i) => (
                <MetricCell key={id} id={id} model={model} isLast={i === 2} />
              ))}
            </div>
          </div>

          {/* Footer chips */}
          <div className="px-3 pb-3 mt-auto flex items-end justify-between gap-2 border-t border-border pt-2">
            <div className="flex flex-wrap gap-1.5 min-w-0">
              {model.collection && (
                <span className="px-2 py-0.5 bg-muted border border-border rounded text-xs font-mono font-semibold text-muted-foreground uppercase tracking-tight">
                  {model.collection}
                </span>
              )}
              {ps?.slicer_name && (
                <span className="px-2 py-0.5 bg-muted border border-border rounded text-xs font-mono font-semibold text-muted-foreground uppercase tracking-tight">
                  GCODE
                </span>
              )}
              <span className="px-2 py-0.5 bg-muted border border-border rounded text-xs font-mono font-semibold text-muted-foreground uppercase tracking-tight">
                {uiText("counts.cardFiles", { count: model.file_count })}
              </span>
              {ps?.material_type && (
                <span className="px-2 py-0.5 bg-muted border border-border rounded text-xs font-mono font-semibold text-muted-foreground uppercase tracking-tight">
                  {ps.material_type}
                </span>
              )}
              {model.tags.slice(0, 2).map((tag) => (
                <span
                  key={tag}
                  className="px-2 py-0.5 bg-accent border border-primary-soft rounded text-xs font-mono font-semibold text-accent-foreground uppercase tracking-tight"
                >
                  {tag}
                </span>
              ))}
              {model.tags.length > 2 && (
                <span className="px-2 py-0.5 bg-muted border border-border rounded text-xs font-mono font-semibold text-muted-foreground">
                  +{model.tags.length - 2}
                </span>
              )}
            </div>
            <p className="text-2xs text-muted-foreground font-mono uppercase shrink-0">
              {timeAgoShort(model.updated_at)}
            </p>
          </div>
        </Link>
      </article>
    </Localized>
  );
}

const ModelCardMemo = memo(ModelCardInner);

export function ModelCard({
  model,
  selectable,
  selected,
  onToggleSelect,
  draggable,
  onEditTags,
}: {
  model: ModelListItem;
  selectable?: boolean;
  selected?: boolean;
  onToggleSelect?: (id: number, range?: boolean) => void;
  draggable?: boolean;
  onEditTags?: (model: ModelListItem) => void;
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
        model={model}
        metrics={metrics}
        selectable={selectable}
        selected={selected}
        onToggleSelect={onToggleSelect}
        draggable={draggable}
        onEditTags={onEditTags}
      />
    </Localized>
  );
}
