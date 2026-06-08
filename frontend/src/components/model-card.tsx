"use client";

import Link from "next/link";
import { memo, useEffect, useRef, useState } from "react";
import { ModelListItem } from "@/types";
import { ArrowRight, FileText, MoreVertical, Printer } from "lucide-react";
import { getAssetUrl } from "@/lib/api";

const MAX_VISIBLE_TAGS = 3;

function timeAgo(dateStr: string): string {
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const diff = now - then;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(dateStr).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });
}

function ModelCardInner({ model }: { model: ModelListItem }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const thumb = model.thumbnail_url
    ? getAssetUrl(model.thumbnail_url)
    : null;

  const visibleTags = model.tags.slice(0, MAX_VISIBLE_TAGS);
  const hiddenCount = model.tags.length - MAX_VISIBLE_TAGS;
  const printerPresence = model.printer_presence ?? [];

  useEffect(() => {
    if (!menuOpen) return;
    function onPointerDown(event: MouseEvent) {
      if (!menuRef.current?.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setMenuOpen(false);
    }
    window.addEventListener("mousedown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("mousedown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [menuOpen]);

  return (
    <article
      className="relative group active:scale-[0.98] transition-transform duration-150 h-full bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded hover:shadow-[0_4px_12px_rgba(0,0,0,0.05)] hover:border-[var(--primary)]"
      style={{ contentVisibility: "auto", containIntrinsicSize: "320px" } as React.CSSProperties}
    >
      <Link
        href={`/models/${model.id}`}
        className="flex flex-col overflow-hidden h-full rounded"
      >
        {/* 1. Thumbnail — fixed aspect ratio, no padding */}
        <div className="aspect-[16/9] bg-[var(--surface-container-low)] relative overflow-hidden flex-shrink-0">
          {model.file_count > 0 && (
            <div className="absolute right-2 top-2 z-10 inline-flex h-6 items-center gap-1.5 rounded border border-[var(--primary-fixed-dim)] bg-[var(--primary-fixed)] px-2 shadow-sm">
              <span className="h-2 w-2 rounded-full bg-emerald-500" />
              <span className="font-mono text-[10px] uppercase leading-none tracking-wider text-[#00174b]">
                {model.file_count} file{model.file_count !== 1 ? "s" : ""}
              </span>
            </div>
          )}

          {thumb ? (
            <img
              src={thumb}
              alt={model.name}
              className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500"
              loading="lazy"
              decoding="async"
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center text-[var(--on-surface-variant)]">
              <FileText className="h-12 w-12 opacity-30" />
            </div>
          )}
        </div>

        {/* 2. Body — flex-grows to consume empty space, pushes footer down */}
        <div className="p-3 sm:p-4 flex flex-col gap-1 flex-1 min-h-0">
          <div className="flex items-start justify-between gap-2">
            {/* 4. Strict single-line ellipsis */}
            <h3
              className="text-[15px] font-semibold text-[var(--on-surface)] truncate leading-tight pr-8"
              title={model.name}
            >
              {model.name}
            </h3>
          </div>

          {printerPresence.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {printerPresence.slice(0, 2).map((presence) => (
                <span
                  key={presence.printer_id}
                  className="inline-flex items-center gap-1 rounded border border-emerald-500/30 bg-emerald-500/10 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider text-emerald-600"
                  title={`${presence.file_count} file${presence.file_count === 1 ? "" : "s"} on ${presence.printer_name}`}
                >
                  <Printer className="h-3 w-3" />
                  {presence.printer_name}
                </span>
              ))}
              {printerPresence.length > 2 && (
                <span className="rounded border border-[var(--outline-variant)] px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider text-[var(--on-surface-variant)]">
                  +{printerPresence.length - 2}
                </span>
              )}
            </div>
          )}

          {/* 3. Footer — margin-top: auto anchors to card bottom */}
          <div className="mt-auto pt-2 flex items-center justify-between gap-2 border-t border-[var(--surface-variant)] min-w-0">
            {/* Scrollable tags + always-visible +N badge */}
            <div className="flex items-center gap-1.5 min-w-0">
              <div
                className="flex items-center gap-1.5 flex-1 min-w-0 overflow-x-auto"
                style={{
                  scrollbarWidth: "none",
                  msOverflowStyle: "none",
                  WebkitMaskImage: "linear-gradient(to right, black 85%, transparent 100%)",
                  maskImage: "linear-gradient(to right, black 85%, transparent 100%)",
                }}
              >
                {model.collection && (
                  <span className="bg-[var(--surface-container)] text-[var(--on-surface)] px-1.5 py-0.5 rounded font-mono text-[10px] uppercase tracking-wider whitespace-nowrap flex-shrink-0">
                    {model.collection}
                  </span>
                )}
                {visibleTags.map((tag) => (
                  <span
                    key={tag}
                    className="bg-[var(--surface-container)] text-[var(--on-surface)] px-1.5 py-0.5 rounded font-mono text-[10px] uppercase tracking-wider whitespace-nowrap flex-shrink-0"
                  >
                    {tag}
                  </span>
                ))}
              </div>
              {hiddenCount > 0 && (
                <span className="bg-[var(--secondary-container)] text-[var(--on-secondary-container)] px-1.5 py-0.5 rounded font-mono text-[10px] whitespace-nowrap flex-shrink-0">
                  +{hiddenCount}
                </span>
              )}
            </div>
            <span className="font-mono text-[11px] text-[var(--on-surface-variant)] whitespace-nowrap flex-shrink-0">
              {timeAgo(model.updated_at)}
            </span>
          </div>
        </div>
      </Link>

      <div ref={menuRef} className="absolute left-2 top-2 z-20">
        <button
          type="button"
          onClick={() => setMenuOpen((value) => !value)}
          className="flex h-7 w-7 items-center justify-center rounded border border-transparent bg-[var(--surface-container-lowest)]/90 text-[var(--on-surface-variant)] opacity-100 shadow-sm transition-colors hover:border-[var(--outline-variant)] hover:text-[var(--on-surface)] sm:opacity-0 sm:group-hover:opacity-100"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          aria-label={`Open actions for ${model.name}`}
          title="Model actions"
        >
          <MoreVertical className="h-4 w-4" />
        </button>
        {menuOpen && (
          <div
            role="menu"
            className="absolute right-0 top-full mt-1 w-40 rounded border border-[var(--outline-variant)] bg-[var(--surface-container-lowest)] py-1 shadow-lg"
          >
            <Link
              href={`/models/${model.id}`}
              role="menuitem"
              className="flex items-center justify-between px-3 py-2 text-xs font-mono text-[var(--on-surface)] hover:bg-[var(--surface-container-low)]"
            >
              Open model
              <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </div>
        )}
      </div>
    </article>
  );
}

export const ModelCard = memo(ModelCardInner);
