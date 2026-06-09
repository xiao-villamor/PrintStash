"use client";

import Link from "next/link";
import { memo } from "react";
import { ModelListItem, FileRevisionStatus } from "@/types";
import { FileText } from "lucide-react";
import { getAssetUrl } from "@/lib/api";

function timeAgoShort(dateStr: string): string {
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const diff = now - then;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return "Today";
  const days = Math.floor(hours / 24);
  if (days === 1) return "Yesterday";
  if (days < 7) return `${days} days ago`;
  return new Date(dateStr).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function formatTime(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

const REVISION_CONFIG: Record<FileRevisionStatus, { label: string; classes: string }> = {
  known_good: { label: "Known Good", classes: "bg-green-50 dark:bg-green-950/50 text-green-700 border-green-200 dark:border-green-800" },
  needs_test: { label: "Needs Test", classes: "bg-amber-50 dark:bg-amber-950/50 text-amber-700 border-amber-200 dark:border-amber-800" },
  failed:     { label: "Failed",     classes: "bg-red-50 dark:bg-red-950/50 text-red-700 border-red-200 dark:border-red-800" },
  archived:   { label: "Archived",   classes: "bg-muted text-muted-foreground border-border" },
};

function RevisionBadge({ status, label }: { status: FileRevisionStatus | null | undefined; label?: string | null }) {
  if (!status) return null;
  const cfg = REVISION_CONFIG[status];
  return (
    <span className={`text-[10px] font-mono font-semibold px-1.5 py-0.5 rounded border uppercase tracking-tight shrink-0 ${cfg.classes}`}>
      {label ?? cfg.label}
    </span>
  );
}

function ModelCardInner({ model }: { model: ModelListItem }) {
  const thumb = model.thumbnail_url ? getAssetUrl(model.thumbnail_url) : null;
  const printerPresence = model.printer_presence ?? [];
  const hasPrinter = printerPresence.length > 0;
  const ps = model.print_summary;

  return (
    <article className="group flex h-full flex-col bg-card border border-border rounded transition-all duration-200 hover:border-blue-500 dark:border-orange-500 overflow-hidden">
      <Link href={`/models/${model.id}`} className="flex flex-col h-full overflow-hidden">

        {/* Thumbnail */}
        <div className="bg-muted relative overflow-hidden h-48 border-b border-border shrink-0">
          {thumb ? (
            <img
              alt={model.name}
              className="w-full h-full object-cover opacity-90 group-hover:opacity-100 transition-opacity"
              src={thumb}
              loading="lazy"
              decoding="async"
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center">
              <FileText className="h-10 w-10 text-muted-foreground/40" />
            </div>
          )}
          {hasPrinter && (
            <div className="absolute bottom-2 right-2">
              <span className="text-[9px] font-bold text-green-700 bg-green-50 dark:bg-green-950/60 px-1.5 py-0.5 border border-green-200 dark:border-green-800 rounded-sm uppercase">
                On printer
              </span>
            </div>
          )}
        </div>

        {/* Title + revision */}
        <div className="px-3 pt-3 pb-1 flex items-start justify-between gap-2">
          <h4 className="text-xs font-bold text-foreground uppercase tracking-tight truncate leading-tight">
            {model.name}
          </h4>
          <RevisionBadge
            status={model.recommended_revision_status}
            label={model.recommended_revision_label}
          />
        </div>

        {/* Subtitle */}
        {(ps?.slicer_name || hasPrinter || ps?.material_type) && (
          <p className="px-3 pb-1 text-[10px] text-muted-foreground truncate">
            {[printerPresence[0]?.printer_name, ps?.material_type, ps?.slicer_name]
              .filter(Boolean)
              .join(" · ")}
          </p>
        )}

        {/* Metrics */}
        <div className="px-3 pb-2">
          <div className="grid grid-cols-3 border border-border rounded-sm overflow-hidden">
            <div className="py-2 px-1 text-center border-r border-border bg-muted/50">
              <p className="text-[7px] font-bold text-muted-foreground uppercase tracking-wider mb-0.5">LYR</p>
              <p className="text-[10px] font-bold text-foreground font-mono">
                {ps?.layer_height_mm != null ? `${ps.layer_height_mm.toFixed(2)} mm` : "—"}
              </p>
            </div>
            <div className="py-2 px-1 text-center border-r border-border bg-muted/50">
              <p className="text-[7px] font-bold text-muted-foreground uppercase tracking-wider mb-0.5">TIME</p>
              <p className="text-[10px] font-bold text-foreground font-mono">
                {ps?.estimated_time_s != null ? formatTime(ps.estimated_time_s) : "—"}
              </p>
            </div>
            <div className="py-2 px-1 text-center bg-muted/50">
              <p className="text-[7px] font-bold text-muted-foreground uppercase tracking-wider mb-0.5">WGT</p>
              <p className="text-[10px] font-bold text-foreground font-mono">
                {ps?.filament_weight_g != null ? `${Math.round(ps.filament_weight_g)} g` : "—"}
              </p>
            </div>
          </div>
        </div>

        {/* Footer chips */}
        <div className="px-3 pb-3 mt-auto flex items-end justify-between gap-2 border-t border-border pt-2">
          <div className="flex flex-wrap gap-1.5 min-w-0">
            {model.collection && (
              <span className="px-2 py-0.5 bg-muted border border-border rounded text-[11px] font-mono font-semibold text-muted-foreground uppercase tracking-tight">
                {model.collection}
              </span>
            )}
            {ps?.slicer_name && (
              <span className="px-2 py-0.5 bg-muted border border-border rounded text-[11px] font-mono font-semibold text-muted-foreground uppercase tracking-tight">
                GCODE
              </span>
            )}
            <span className="px-2 py-0.5 bg-muted border border-border rounded text-[11px] font-mono font-semibold text-muted-foreground uppercase tracking-tight">
              {model.file_count} {model.file_count === 1 ? "File" : "Files"}
            </span>
            {ps?.material_type && (
              <span className="px-2 py-0.5 bg-muted border border-border rounded text-[11px] font-mono font-semibold text-muted-foreground uppercase tracking-tight">
                {ps.material_type}
              </span>
            )}
            {model.tags.slice(0, 2).map((tag) => (
              <span key={tag} className="px-2 py-0.5 bg-blue-50 dark:bg-orange-950/40 border border-blue-200 dark:border-orange-800 dark:border-orange-800 rounded text-[11px] font-mono font-semibold text-blue-700 dark:text-orange-400 uppercase tracking-tight">
                {tag}
              </span>
            ))}
            {model.tags.length > 2 && (
              <span className="px-2 py-0.5 bg-muted border border-border rounded text-[11px] font-mono font-semibold text-muted-foreground">
                +{model.tags.length - 2}
              </span>
            )}
          </div>
          <p className="text-[10px] text-muted-foreground font-mono uppercase shrink-0">
            {timeAgoShort(model.updated_at)}
          </p>
        </div>

      </Link>
    </article>
  );
}

export const ModelCard = memo(ModelCardInner);
