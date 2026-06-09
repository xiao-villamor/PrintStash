"use client";

import { useEffect, useMemo, useState } from "react";
import { CollectionRead, ModelListItem, PrinterRead, TagRead } from "@/types";
import { Skeleton } from "@/components/ui/skeleton";
import { Box, ChevronRight, Folder, FolderOpen, Search, X } from "lucide-react";

interface CollectionNode {
  cat: CollectionRead;
  children: CollectionNode[];
}

function buildTree(cats: CollectionRead[]): CollectionNode[] {
  const byId = new Map<number, CollectionNode>();
  for (const c of cats) byId.set(c.id, { cat: c, children: [] });
  const roots: CollectionNode[] = [];
  for (const node of byId.values()) {
    if (node.cat.parent_id == null) {
      roots.push(node);
    } else {
      const parent = byId.get(node.cat.parent_id);
      if (parent) parent.children.push(node);
      else roots.push(node);
    }
  }
  const sortRec = (nodes: CollectionNode[]) => {
    nodes.sort((a, b) => a.cat.name.localeCompare(b.cat.name));
    nodes.forEach((n) => sortRec(n.children));
  };
  sortRec(roots);
  return roots;
}

function CollectionTreeRow({
  node,
  selected,
  onSelect,
  expanded,
  toggle,
  modelsByCollection,
  visibleIds,
  visibleModelIds,
}: {
  node: CollectionNode;
  selected: string | null;
  onSelect: (path: string | null) => void;
  expanded: Set<string>;
  toggle: (path: string) => void;
  modelsByCollection: Map<string, ModelListItem[]>;
  visibleIds?: Set<number> | null;
  visibleModelIds?: Set<number> | null;
}) {
  const visibleChildren = visibleIds
    ? node.children.filter((c) => visibleIds.has(c.cat.id))
    : node.children;
  const allModelLeaves = modelsByCollection.get(node.cat.path) ?? [];
  const modelLeaves = visibleModelIds
    ? allModelLeaves.filter((m) => visibleModelIds.has(m.id))
    : allModelLeaves;
  const isOpen = visibleIds ? visibleChildren.length > 0 || modelLeaves.length > 0 : expanded.has(node.cat.path);
  const isSelected = selected === node.cat.path;
  const hasChildren = visibleChildren.length > 0;
  const hasNestedItems = hasChildren || modelLeaves.length > 0;
  const displayChildren = visibleIds ? visibleChildren : node.children;

  return (
    <div>
      <div
        className={`group/row flex items-center justify-between rounded px-2 py-1.5 cursor-pointer transition-colors ${
          isSelected
            ? "text-blue-700 dark:text-orange-400 bg-blue-50 dark:bg-orange-950/50"
            : "text-foreground hover:bg-muted"
        }`}
      >
        <div className="flex items-center gap-1 flex-1 min-w-0">
          {hasNestedItems ? (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); toggle(node.cat.path); }}
              className="rounded p-0.5 hover:bg-muted flex-shrink-0"
              aria-label={isOpen ? "Collapse" : "Expand"}
            >
              <ChevronRight className={`h-3.5 w-3.5 transition-transform ${isOpen ? "rotate-90" : ""}`} />
            </button>
          ) : (
            <span className="inline-block w-[18px] flex-shrink-0" />
          )}
          <button
            type="button"
            onClick={() => onSelect(isSelected ? null : node.cat.path)}
            className="flex flex-1 items-center justify-between gap-2 truncate text-left text-sm font-medium"
            title={node.cat.path}
          >
            <span className="flex min-w-0 items-center gap-1.5 truncate">
              {isOpen || isSelected ? (
                <FolderOpen className="h-3.5 w-3.5 flex-shrink-0 text-blue-600 dark:text-orange-500" />
              ) : (
                <Folder className="h-3.5 w-3.5 flex-shrink-0" />
              )}
              <span className="truncate">{node.cat.name}</span>
            </span>
          </button>
        </div>
        <span className="ml-2 min-w-6 rounded bg-muted px-1.5 py-0.5 text-center text-[10px] font-medium text-muted-foreground">
          {node.cat.model_count}
        </span>
      </div>
      {isOpen && hasNestedItems && (
        <div className="ml-5 border-l border-border pl-4 overflow-x-auto min-w-0">
          {displayChildren.map((child) => (
            <CollectionTreeRow
              key={child.cat.id}
              node={child}
              selected={selected}
              onSelect={onSelect}
              expanded={expanded}
              toggle={toggle}
              modelsByCollection={modelsByCollection}
              visibleIds={visibleIds}
              visibleModelIds={visibleModelIds}
            />
          ))}
          {modelLeaves.slice(0, 8).map((model) => (
            <div
              key={model.id}
              className="flex items-center gap-2 rounded px-2 py-1 text-[11px] text-muted-foreground"
              title={model.name}
            >
              <Box className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground/40" />
              <span className="truncate">{model.name}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function FilterSidebarContent({
  collections,
  models = [],
  tags,
  printers,
  selectedCollection,
  selectedTags,
  selectedPrinterId,
  selectedPrinterPresence,
  onCollectionChange,
  onTagsChange,
  onPrinterChange,
  onPrinterPresenceChange,
  onCreateCollection,
  loading,
  outlinerFilter,
}: FilterSidebarProps) {
  const tree = useMemo(() => buildTree(collections), [collections]);

  const outlinerQ = (outlinerFilter ?? "").trim().toLowerCase();

  const visibleModelIds = useMemo<Set<number> | null>(() => {
    if (!outlinerQ) return null;
    const result = new Set<number>();
    for (const m of models) {
      if (m.name.toLowerCase().includes(outlinerQ)) result.add(m.id);
    }
    return result;
  }, [models, outlinerQ]);

  const visibleCollectionIds = useMemo<Set<number> | null>(() => {
    if (!outlinerQ) return null;
    const byId = new Map(collections.map((c) => [c.id, c]));
    const addWithAncestors = (c: CollectionRead) => {
      result.add(c.id);
      let cur = c.parent_id != null ? byId.get(c.parent_id) : undefined;
      while (cur) { result.add(cur.id); cur = cur.parent_id != null ? byId.get(cur.parent_id) : undefined; }
    };
    const result = new Set<number>();
    for (const c of collections) {
      if (c.name.toLowerCase().includes(outlinerQ)) addWithAncestors(c);
    }
    for (const m of models) {
      if (!m.collection || !visibleModelIds?.has(m.id)) continue;
      const col = collections.find((c) => c.path === m.collection);
      if (col) addWithAncestors(col);
    }
    return result;
  }, [collections, models, outlinerQ, visibleModelIds]);

  const visibleRoots = visibleCollectionIds
    ? tree.filter((n) => visibleCollectionIds.has(n.cat.id))
    : tree;
  const modelsByCollection = useMemo(() => {
    const grouped = new Map<string, ModelListItem[]>();
    for (const model of models) {
      if (!model.collection) continue;
      const current = grouped.get(model.collection) ?? [];
      current.push(model);
      grouped.set(model.collection, current);
    }
    for (const items of grouped.values()) {
      items.sort((a, b) => a.name.localeCompare(b.name));
    }
    return grouped;
  }, [models]);
  const rootModels = useMemo(
    () => models.filter((m) => !m.collection).sort((a, b) => a.name.localeCompare(b.name)),
    [models],
  );
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [allModelsExpanded, setAllModelsExpanded] = useState(false);
  const [tagFilter, setTagFilter] = useState("");
  const [showAllTags, setShowAllTags] = useState(false);
  const [printerExpanded, setPrinterExpanded] = useState(false);

  const sortedTags = useMemo(
    () => [...tags].sort((a, b) => b.model_count - a.model_count),
    [tags],
  );

  const filteredTags = useMemo(() => {
    if (!tagFilter.trim()) return sortedTags;
    const q = tagFilter.toLowerCase();
    return sortedTags.filter((t) => t.name.toLowerCase().includes(q));
  }, [sortedTags, tagFilter]);

  const visibleTags = showAllTags ? filteredTags : filteredTags.slice(0, 10);
  const hiddenCount = filteredTags.length - 10;

  useEffect(() => {
    setExpanded((prev) => {
      const next = new Set(prev);
      const parentIds = new Set(collections.map((c) => c.parent_id).filter((id) => id != null));
      for (const collection of collections) {
        if (parentIds.has(collection.id) || modelsByCollection.has(collection.path)) {
          next.add(collection.path);
        }
      }
      return next;
    });
  }, [collections, modelsByCollection]);

  useEffect(() => {
    if (!selectedCollection) return;
    const parts = selectedCollection.split("/");
    const ancestors = new Set<string>();
    for (let i = 1; i < parts.length; i++) {
      ancestors.add(parts.slice(0, i).join("/"));
    }
    setExpanded((prev) => {
      const next = new Set(prev);
      ancestors.forEach((a) => next.add(a));
      return next;
    });
  }, [selectedCollection]);

  function toggleTag(slug: string) {
    if (selectedTags.includes(slug)) {
      onTagsChange(selectedTags.filter((t) => t !== slug));
    } else {
      onTagsChange([...selectedTags, slug]);
    }
  }

  function toggleExpanded(path: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  if (loading) {
    return (
      <div className="py-4 px-3 space-y-6">
        <Skeleton className="h-5 w-20" />
        <Skeleton className="h-36 w-full" />
        <Skeleton className="h-5 w-14" />
        <Skeleton className="h-28 w-full" />
      </div>
    );
  }

  const statusColor = (s: string) =>
    s === "printing" ? "bg-blue-500 dark:bg-orange-500" :
    s === "ready" ? "bg-green-500" :
    s === "paused" ? "bg-amber-500" :
    s === "error" ? "bg-red-500" :
    "bg-slate-400";

  const statusLabel = (s: string) =>
    s === "printing" ? "Printing" :
    s === "ready" ? "Ready" :
    s === "paused" ? "Paused" :
    s === "error" ? "Error" :
    s === "offline" ? "Offline" :
    "Unknown";

  const statusTextColor = (s: string) =>
    s === "printing" ? "text-blue-500 dark:text-orange-400" :
    s === "error" ? "text-red-500" :
    s === "ready" ? "text-green-500" :
    s === "paused" ? "text-amber-500" :
    "text-muted-foreground";

  return (
    <div className="flex-1 overflow-auto py-4 px-3 space-y-6">
      {/* Collections */}
      <section>
        <div className="flex items-center justify-between mb-2 pl-2 pr-1">
          <h3 className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">
            Collections
          </h3>
          <button
            onClick={onCreateCollection}
            className="p-0.5 text-muted-foreground hover:text-foreground hover:bg-muted rounded transition-colors"
            title="Create Collection"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path d="M12 4v16m8-8H4" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" />
            </svg>
          </button>
        </div>
        <div className="space-y-0.5">
          <button
            type="button"
            onClick={() => onCollectionChange(null)}
            className={`w-full flex items-center px-2 py-1.5 text-sm rounded font-medium group transition-colors ${
              selectedCollection === null
                ? "text-blue-700 dark:text-orange-400 bg-blue-50 dark:bg-orange-950/50"
                : "text-foreground hover:bg-muted"
            }`}
          >
            {rootModels.length > 0 ? (
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); setAllModelsExpanded((v) => !v); }}
                className="rounded p-0.5 hover:bg-muted flex-shrink-0 mr-1"
                aria-label={allModelsExpanded ? "Collapse" : "Expand"}
              >
                <ChevronRight className={`h-3.5 w-3.5 transition-transform ${allModelsExpanded ? "rotate-90" : ""}`} />
              </button>
            ) : (
              <ChevronRight className={`h-4 w-4 mr-1 rotate-90 ${selectedCollection === null ? "text-blue-500 dark:text-orange-400" : "text-muted-foreground"}`} />
            )}
            <FolderOpen className="h-4 w-4 mr-2 text-blue-500 dark:text-orange-400" />
            All Models
          </button>
          {(() => {
            const displayRootModels = visibleModelIds
              ? rootModels.filter((m) => visibleModelIds.has(m.id))
              : rootModels;
            const rootExpanded = allModelsExpanded || (!!outlinerQ && displayRootModels.length > 0);
            return rootExpanded && displayRootModels.length > 0 ? (
              <div className="ml-5 border-l border-border pl-4 overflow-x-auto min-w-0">
                {displayRootModels.slice(0, 8).map((model) => (
                  <div
                    key={model.id}
                    className="flex items-center gap-2 rounded px-2 py-1 text-[11px] text-muted-foreground"
                    title={model.name}
                  >
                    <Box className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground/40" />
                    <span className="truncate">{model.name}</span>
                  </div>
                ))}
                {displayRootModels.length > 8 && (
                  <div className="px-2 py-1 text-[10px] text-muted-foreground">
                    +{displayRootModels.length - 8} more
                  </div>
                )}
              </div>
            ) : null;
          })()}
          <div className="ml-5 border-l border-border pl-4 overflow-x-auto min-w-0">
            {visibleRoots.length === 0 && outlinerQ && (visibleModelIds?.size ?? 0) === 0 ? (
              <p className="py-2 text-[10px] text-muted-foreground font-mono">No results.</p>
            ) : (
              visibleRoots.map((node) => (
                <CollectionTreeRow
                  key={node.cat.id}
                  node={node}
                  selected={selectedCollection}
                  onSelect={onCollectionChange}
                  expanded={expanded}
                  toggle={toggleExpanded}
                  modelsByCollection={modelsByCollection}
                  visibleIds={visibleCollectionIds}
                  visibleModelIds={visibleModelIds}
                />
              ))
            )}
          </div>
        </div>
      </section>

      {/* Printer - always visible */}
      <section>
        <h3 className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider mb-2 pl-2">
          Printer
        </h3>
        <div className="space-y-0.5">
          <button
            type="button"
            onClick={() => {
              onPrinterChange(null);
              onPrinterPresenceChange(null);
            }}
            className={`w-full flex items-center px-2 py-1.5 text-sm rounded font-medium group transition-colors ${
              selectedPrinterId === null && selectedPrinterPresence === null
                ? "text-blue-700 dark:text-orange-400 bg-blue-50 dark:bg-orange-950/50"
                : "text-foreground hover:bg-muted"
            }`}
          >
            <svg className="h-4 w-4 mr-2 text-blue-500 dark:text-orange-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" />
            </svg>
            Any location
          </button>

          <div className="space-y-0.5">
            <button
              type="button"
              onClick={() => {
                onPrinterChange(null);
                onPrinterPresenceChange("any");
                setPrinterExpanded(!printerExpanded);
              }}
              className={`w-full flex items-center px-2 py-1.5 text-sm rounded font-medium group transition-colors ${
                selectedPrinterPresence === "any"
                  ? "text-blue-700 dark:text-orange-400 bg-blue-50 dark:bg-orange-950/50"
                  : "text-foreground hover:bg-muted"
              }`}
            >
              <ChevronRight className={`h-4 w-4 mr-1 text-muted-foreground transition-transform ${printerExpanded ? "rotate-90" : ""}`} />
              <svg className="h-4 w-4 mr-2 text-blue-500 dark:text-orange-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" />
              </svg>
              <span className="font-medium">On a printer</span>
            </button>
            {printerExpanded && (
              <div className="ml-4 border-l border-border">
                {printers.length === 0 ? (
                  <p className="pl-4 py-1 text-[11px] text-muted-foreground font-mono">
                    No printers configured
                  </p>
                ) : (
                  printers.map((printer) => (
                    <button
                      key={printer.id}
                      type="button"
                      onClick={() => {
                        onPrinterChange(printer.id);
                        onPrinterPresenceChange(null);
                      }}
                      className={`w-full flex items-center justify-between px-2 py-1.5 text-sm transition-colors rounded group pl-4 ${
                        selectedPrinterId === printer.id
                          ? "text-blue-700 dark:text-orange-400 bg-blue-50 dark:bg-orange-950/50"
                          : "text-foreground hover:bg-muted"
                      }`}
                    >
                      <span className="flex items-center">
                        <span className={`w-1.5 h-1.5 rounded-full ${statusColor(printer.status)} mr-2`} />
                        {printer.name}
                      </span>
                      <span className={`text-[10px] font-medium ${statusTextColor(printer.status)}`}>
                        {statusLabel(printer.status)}
                      </span>
                    </button>
                  ))
                )}
              </div>
            )}
          </div>

          <button
            type="button"
            onClick={() => {
              onPrinterChange(null);
              onPrinterPresenceChange("none");
            }}
            className={`w-full flex items-center px-2 py-1.5 text-sm rounded font-medium group transition-colors ${
              selectedPrinterPresence === "none"
                ? "text-blue-700 dark:text-orange-400 bg-blue-50 dark:bg-orange-950/50"
                : "text-foreground hover:bg-muted"
            }`}
          >
            <Folder className="h-4 w-4 mr-2 text-blue-500 dark:text-orange-400" />
            Vault only
          </button>
        </div>
      </section>

      {/* Tags */}
      {tags.length > 0 && (
        <section>
          <h3 className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider mb-2 pl-2">
            Tags
          </h3>

          <div className="relative mb-2">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3 w-3 text-muted-foreground" />
            <input
              type="text"
              placeholder="Filter tags..."
              value={tagFilter}
              onChange={(e) => {
                setTagFilter(e.target.value);
                setShowAllTags(false);
              }}
              className="w-full pl-7 pr-2 py-1 text-xs border border-border rounded bg-muted text-foreground font-mono placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-blue-500 dark:focus:ring-orange-500 focus:border-blue-500 dark:focus:border-blue-500 dark:border-orange-500 transition-colors"
            />
            {tagFilter && (
              <button
                type="button"
                onClick={() => setTagFilter("")}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              >
                <X className="h-3 w-3" />
              </button>
            )}
          </div>

          {filteredTags.length === 0 ? (
            <p className="text-[10px] text-muted-foreground font-mono px-1 py-2">No matching tags.</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {visibleTags.map((t) => {
                const active = selectedTags.includes(t.slug);
                return (
                  <button
                    type="button"
                    key={t.id}
                    onClick={() => toggleTag(t.slug)}
                    className={`flex items-center gap-1 px-2 py-1 rounded font-mono text-[10px] tracking-wider uppercase border transition-colors ${
                      active
                        ? "border-blue-500 dark:border-orange-500 bg-blue-50 dark:bg-orange-950/50 text-blue-700 dark:text-orange-400"
                        : "border-border text-muted-foreground hover:border-border hover:bg-muted"
                    }`}
                  >
                    {t.name}
                    <span className="opacity-60">{t.model_count}</span>
                    {active && <X className="h-3 w-3 ml-0.5" />}
                  </button>
                );
              })}
            </div>
          )}

          {!tagFilter && hiddenCount > 0 && (
            <button
              type="button"
              onClick={() => setShowAllTags(!showAllTags)}
              className="mt-2 w-full text-center font-mono text-[10px] text-muted-foreground hover:text-foreground transition-colors py-1"
            >
              {showAllTags ? "Show fewer" : `Show all ${filteredTags.length} tags`}
            </button>
          )}
        </section>
      )}
    </div>
  );
}

export interface FilterSidebarProps {
  collections: CollectionRead[];
  models?: ModelListItem[];
  tags: TagRead[];
  printers: PrinterRead[];
  selectedCollection: string | null;
  selectedTags: string[];
  selectedPrinterId: number | null;
  selectedPrinterPresence: "any" | "none" | null;
  onCollectionChange: (path: string | null) => void;
  onTagsChange: (tags: string[]) => void;
  onPrinterChange: (printerId: number | null) => void;
  onPrinterPresenceChange: (presence: "any" | "none" | null) => void;
  onCreateCollection: () => void;
  loading?: boolean;
  outlinerFilter?: string;
}

export function FilterSidebar(props: FilterSidebarProps) {
  const [outlinerFilter, setOutlinerFilter] = useState("");

  return (
    <aside className="w-64 bg-[var(--sidebar-bg)] border-r border-border flex flex-col shrink-0 hidden md:flex">
      <div className="p-2 border-b border-border bg-[var(--sidebar-bg)]">
        <div className="relative">
          <span className="absolute inset-y-0 left-0 pl-2 flex items-center text-muted-foreground">
            <Search className="h-3.5 w-3.5" />
          </span>
          <input
            className="block w-full pl-7 pr-6 py-1 text-xs border border-border rounded bg-muted text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-blue-500 dark:focus:ring-orange-500"
            placeholder="Filter outliner..."
            type="text"
            value={outlinerFilter}
            onChange={(e) => setOutlinerFilter(e.target.value)}
          />
          {outlinerFilter && (
            <button
              type="button"
              onClick={() => setOutlinerFilter("")}
              className="absolute inset-y-0 right-2 flex items-center text-muted-foreground hover:text-foreground"
            >
              <X className="h-3 w-3" />
            </button>
          )}
        </div>
      </div>
      <FilterSidebarContent {...props} outlinerFilter={outlinerFilter} />
    </aside>
  );
}
