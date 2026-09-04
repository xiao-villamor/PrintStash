"use client";

import { useEffect, useMemo, useState } from "react";
import { Boxes, Folder, FolderOpen, Search, X } from "lucide-react";

import { Checkbox } from "@/components/ui/checkbox";
import { Localized } from "@/components/ui/localized";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import type { CollectionRead } from "@/types";
import type { MultipartStructureFilter } from "@/components/multipart-model-browser";

interface MultipartFilterSidebarProps {
  collections: CollectionRead[];
  selectedCollection: string | null;
  structures: MultipartStructureFilter[];
  guidesOnly: boolean;
  onCollectionChange: (path: string | null) => void;
  onStructuresChange: (value: MultipartStructureFilter[]) => void;
  onGuidesOnlyChange: (value: boolean) => void;
}

const STRUCTURE_FILTERS: MultipartStructureFilter[] = ["variants", "fixed", "empty"];

function readSidebarWidth(): number {
  try {
    return Number.parseInt(localStorage.getItem("ps-sidebar-width") ?? "220", 10);
  } catch {
    return 220;
  }
}

function collectionDepth(path: string): number {
  return Math.max(0, path.split("/").length - 1);
}

export function MultipartFilterSidebar({
  collections,
  selectedCollection,
  structures,
  guidesOnly,
  onCollectionChange,
  onStructuresChange,
  onGuidesOnlyChange,
}: MultipartFilterSidebarProps) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const [sidebarWidth, setSidebarWidth] = useState(readSidebarWidth);
  const visibleCollections = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return [...collections]
      .filter((collection) => !needle || collection.path.toLocaleLowerCase().includes(needle))
      .sort((a, b) => a.path.localeCompare(b.path));
  }, [collections, query]);

  useEffect(() => {
    try {
      localStorage.setItem("ps-sidebar-width", String(sidebarWidth));
    } catch {}
  }, [sidebarWidth]);

  function handleResizeStart(event: React.MouseEvent) {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = sidebarWidth;
    const handleMove = (moveEvent: MouseEvent) => {
      setSidebarWidth(Math.min(520, Math.max(180, startWidth + moveEvent.clientX - startX)));
    };
    const handleUp = () => {
      document.removeEventListener("mousemove", handleMove);
      document.removeEventListener("mouseup", handleUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", handleMove);
    document.addEventListener("mouseup", handleUp);
  }

  function toggleStructure(value: MultipartStructureFilter) {
    onStructuresChange(
      structures.includes(value)
        ? structures.filter((current) => current !== value)
        : [...structures, value],
    );
  }

  return (
    <Localized>
      <aside
        style={{ width: sidebarWidth }}
        className="relative hidden shrink-0 flex-col border-r border-border bg-sidebar md:flex"
        aria-label={t("multipart.filters")}
      >
        <div className="border-b border-border bg-sidebar p-2">
          <div className="relative">
            <Search
              className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
            <input
              type="text"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t("multipart.filterCollections")}
              className="block w-full rounded border border-border bg-muted py-1.5 pl-7 pr-7 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
            />
            {query && (
              <button
                type="button"
                onClick={() => setQuery("")}
                aria-label={t("multipart.clearCollectionFilter")}
                className="absolute inset-y-0 right-2 flex items-center text-muted-foreground hover:text-foreground"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        </div>

        <div className="min-h-0 flex-1 space-y-6 overflow-y-auto p-3">
          <section>
            <h2 className="mb-2 px-2 text-xs font-bold uppercase tracking-wider text-muted-foreground">
              {t("multipart.collections")}
            </h2>
            <button
              type="button"
              onClick={() => onCollectionChange(null)}
              className={cn(
                "flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm font-medium transition-colors duration-press",
                selectedCollection === null
                  ? "bg-accent text-accent-foreground"
                  : "text-foreground hover:bg-muted",
              )}
            >
              <Boxes className="h-4 w-4 shrink-0 text-primary" aria-hidden />
              <span className="truncate">{t("multipart.allSets")}</span>
            </button>
            <div className="mt-1 space-y-0.5">
              {visibleCollections.map((collection) => {
                const selected = selectedCollection === collection.path;
                return (
                  <button
                    key={collection.id}
                    type="button"
                    onClick={() => onCollectionChange(selected ? null : collection.path)}
                    className={cn(
                      "flex w-full items-center gap-2 rounded py-1.5 pr-2 text-left text-sm transition-colors duration-press",
                      selected
                        ? "bg-accent text-accent-foreground"
                        : "text-foreground hover:bg-muted",
                    )}
                    style={{ paddingLeft: 8 + collectionDepth(collection.path) * 16 }}
                    title={collection.path}
                  >
                    {selected ? (
                      <FolderOpen className="h-4 w-4 shrink-0 text-primary" aria-hidden />
                    ) : (
                      <Folder className="h-4 w-4 shrink-0" aria-hidden />
                    )}
                    <span className="truncate">{collection.name}</span>
                  </button>
                );
              })}
            </div>
          </section>

          <section>
            <h2 className="mb-2 px-2 text-xs font-bold uppercase tracking-wider text-muted-foreground">
              {t("multipart.structure")}
            </h2>
            <div className="space-y-0.5">
              {STRUCTURE_FILTERS.map((value) => (
                <label
                  key={value}
                  className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm text-foreground transition-colors duration-press hover:bg-muted"
                >
                  <Checkbox
                    className="h-4 w-4"
                    checked={structures.includes(value)}
                    onChange={() => toggleStructure(value)}
                  />
                  <span>{t(`multipart.structure.${value}`)}</span>
                </label>
              ))}
            </div>
          </section>

          <section>
            <h2 className="mb-2 px-2 text-xs font-bold uppercase tracking-wider text-muted-foreground">
              {t("multipart.guidesHeading")}
            </h2>
            <label className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm text-foreground transition-colors duration-press hover:bg-muted">
              <Checkbox
                className="h-4 w-4"
                checked={guidesOnly}
                onChange={() => onGuidesOnlyChange(!guidesOnly)}
              />
              <span>{t("multipart.withGuides")}</span>
            </label>
          </section>
        </div>

        <div
          onMouseDown={handleResizeStart}
          className="absolute inset-y-0 right-0 z-50 w-1.5 cursor-col-resize transition-colors duration-press hover:bg-primary/50"
        />
      </aside>
    </Localized>
  );
}
