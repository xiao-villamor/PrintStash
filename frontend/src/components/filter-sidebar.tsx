"use client";

import { useEffect, useMemo, useState } from "react";
import { CollectionRead, PrinterRead, TagRead } from "@/types";
import { Skeleton } from "@/components/ui/skeleton";
import { ChevronRight, Folder, FolderOpen, Search, X } from "lucide-react";

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

function CollectionNodeRow({
  node,
  depth,
  selected,
  onSelect,
  expanded,
  toggle,
}: {
  node: CollectionNode;
  depth: number;
  selected: string | null;
  onSelect: (path: string | null) => void;
  expanded: Set<string>;
  toggle: (path: string) => void;
}) {
  const isOpen = expanded.has(node.cat.path);
  const isSelected = selected === node.cat.path;
  const hasChildren = node.children.length > 0;

  return (
    <div>
      <div
        className={`group/row flex items-center justify-between rounded py-1 cursor-pointer transition-colors ${
          isSelected
            ? "bg-[var(--secondary-container)] text-[var(--on-secondary-container)]"
            : "text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)] hover:text-[var(--on-surface)]"
        }`}
        style={{ paddingLeft: `${depth * 14 + 6}px`, paddingRight: "4px" }}
      >
        <div className="flex items-center gap-1 flex-1 min-w-0">
          {hasChildren ? (
            <button
              type="button"
              onClick={() => toggle(node.cat.path)}
              className="rounded p-0.5 hover:bg-[var(--surface-container-high)] flex-shrink-0"
              aria-label={isOpen ? "Collapse" : "Expand"}
            >
              <ChevronRight
                className={`h-3.5 w-3.5 transition-transform ${
                  isOpen ? "rotate-90" : ""
                }`}
              />
            </button>
          ) : (
            <span className="inline-block w-[18px] flex-shrink-0" />
          )}
          <button
            type="button"
            onClick={() => onSelect(isSelected ? null : node.cat.path)}
            className="flex flex-1 items-center justify-between gap-2 truncate text-left font-mono text-[13px]"
            title={node.cat.path}
          >
            <span className="flex min-w-0 items-center gap-1.5 truncate">
              {isOpen || isSelected ? (
                <FolderOpen className="h-3.5 w-3.5 flex-shrink-0 text-[var(--primary)]" />
              ) : (
                <Folder className="h-3.5 w-3.5 flex-shrink-0" />
              )}
              <span className="truncate">{node.cat.name}</span>
            </span>
          </button>
        </div>
        <span className="bg-[var(--surface-container)] px-1 rounded text-[10px] font-mono text-[var(--on-surface-variant)] mr-1">
          {node.cat.model_count}
        </span>
      </div>
      {isOpen &&
        node.children.map((child) => (
          <CollectionNodeRow
            key={child.cat.id}
            node={child}
            depth={depth + 1}
            selected={selected}
            onSelect={onSelect}
            expanded={expanded}
            toggle={toggle}
          />
        ))}
    </div>
  );
}

export function FilterSidebarContent({
  collections,
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
  loading,
}: FilterSidebarProps) {
  const tree = useMemo(() => buildTree(collections), [collections]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [tagFilter, setTagFilter] = useState("");
  const [showAllTags, setShowAllTags] = useState(false);

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
      <div className="p-4 space-y-6">
        <Skeleton className="h-5 w-20" />
        <Skeleton className="h-36 w-full" />
        <Skeleton className="h-5 w-14" />
        <Skeleton className="h-28 w-full" />
      </div>
    );
  }

  return (
    <div className="p-4 flex flex-col gap-5">
      {/* Collections */}
      <div>
        <h3 className="font-mono text-[10px] text-[var(--on-surface-variant)] tracking-wider uppercase mb-2">
          Collections
        </h3>
        <div className="flex flex-col gap-0.5 rounded border border-[var(--outline-variant)] bg-[var(--surface-container-lowest)] p-1">
          <button
            type="button"
            onClick={() => onCollectionChange(null)}
            className={`flex items-center justify-between px-2 py-1.5 rounded font-mono text-[13px] transition-colors ${
              selectedCollection === null
                ? "bg-[var(--secondary-container)] text-[var(--on-secondary-container)] font-medium"
                : "text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)] hover:text-[var(--on-surface)]"
            }`}
          >
            <span className="flex items-center gap-1.5">
              <FolderOpen className="h-3.5 w-3.5 text-[var(--primary)]" />
              All Models
            </span>
          </button>
          {tree.length === 0 ? (
            <p className="px-3 py-2 text-[11px] text-[var(--on-surface-variant)] font-mono">
              No collections yet.
            </p>
          ) : (
            tree.map((node) => (
              <CollectionNodeRow
                key={node.cat.id}
                node={node}
                depth={0}
                selected={selectedCollection}
                onSelect={onCollectionChange}
                expanded={expanded}
                toggle={toggleExpanded}
              />
            ))
          )}
        </div>
      </div>

      {/* Printer availability */}
      <div>
        <h3 className="font-mono text-[10px] text-[var(--on-surface-variant)] tracking-wider uppercase mb-2">
          Printer
        </h3>
        <div className="flex flex-col gap-0.5">
          <button
            type="button"
            onClick={() => {
              onPrinterChange(null);
              onPrinterPresenceChange(null);
            }}
            className={`flex items-center justify-between px-3 py-1.5 rounded-lg font-mono text-[13px] transition-colors ${
              selectedPrinterId === null && selectedPrinterPresence === null
                ? "bg-[var(--secondary-container)] text-[var(--on-secondary-container)] font-medium"
                : "text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)] hover:text-[var(--on-surface)]"
            }`}
          >
            <span>Any location</span>
          </button>
          <button
            type="button"
            onClick={() => {
              onPrinterChange(null);
              onPrinterPresenceChange("any");
            }}
            className={`flex items-center justify-between px-3 py-1.5 rounded-lg font-mono text-[13px] transition-colors ${
              selectedPrinterPresence === "any"
                ? "bg-[var(--secondary-container)] text-[var(--on-secondary-container)] font-medium"
                : "text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)] hover:text-[var(--on-surface)]"
            }`}
          >
            <span>On a printer</span>
          </button>
          <button
            type="button"
            onClick={() => {
              onPrinterChange(null);
              onPrinterPresenceChange("none");
            }}
            className={`flex items-center justify-between px-3 py-1.5 rounded-lg font-mono text-[13px] transition-colors ${
              selectedPrinterPresence === "none"
                ? "bg-[var(--secondary-container)] text-[var(--on-secondary-container)] font-medium"
                : "text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)] hover:text-[var(--on-surface)]"
            }`}
          >
            <span>Vault only</span>
          </button>
          {printers.map((printer) => (
            <button
              key={printer.id}
              type="button"
              onClick={() => {
                onPrinterChange(printer.id);
                onPrinterPresenceChange(null);
              }}
              className={`flex items-center justify-between gap-2 px-3 py-1.5 rounded-lg font-mono text-[13px] transition-colors ${
                selectedPrinterId === printer.id
                  ? "bg-[var(--secondary-container)] text-[var(--on-secondary-container)] font-medium"
                  : "text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)] hover:text-[var(--on-surface)]"
              }`}
              title={printer.name}
            >
              <span className="truncate">{printer.name}</span>
              <span className="h-2 w-2 rounded-full bg-[var(--primary)]" />
            </button>
          ))}
        </div>
      </div>

      {/* Tags */}
      {tags.length > 0 && (
        <>
          <hr className="border-t border-[var(--outline-variant)]" />
          <div>
            <h3 className="font-mono text-[10px] text-[var(--on-surface-variant)] tracking-wider uppercase mb-2">
              Tags
            </h3>

            <div className="relative mb-2">
              <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3 w-3 text-[var(--on-surface-variant)] opacity-50" />
              <input
                type="text"
                placeholder="Filter tags..."
                value={tagFilter}
                onChange={(e) => {
                  setTagFilter(e.target.value);
                  setShowAllTags(false);
                }}
                className="w-full pl-7 pr-2 py-1.5 rounded-lg border border-[var(--outline-variant)] bg-[var(--surface-container)] text-[var(--on-surface)] font-mono text-[10px] placeholder:text-[var(--on-surface-variant)]/50 focus:outline-none focus:border-[var(--primary)] transition-colors"
              />
              {tagFilter && (
                <button
                  type="button"
                  onClick={() => setTagFilter("")}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-[var(--on-surface-variant)] hover:text-[var(--on-surface)]"
                >
                  <X className="h-3 w-3" />
                </button>
              )}
            </div>

            {filteredTags.length === 0 ? (
              <p className="text-[10px] text-[var(--on-surface-variant)] font-mono px-1 py-2">
                No matching tags.
              </p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {visibleTags.map((t) => {
                  const active = selectedTags.includes(t.slug);
                  return (
                    <button
                      type="button"
                      key={t.id}
                      onClick={() => toggleTag(t.slug)}
                      className={`flex items-center gap-1 px-2 py-1 rounded-lg font-mono text-[10px] tracking-wider uppercase border transition-colors ${
                        active
                          ? "border-[var(--primary)] bg-[var(--primary)]/10 text-[var(--primary)]"
                          : "border-[var(--outline-variant)] text-[var(--on-surface-variant)] hover:border-[var(--outline)] hover:bg-[var(--surface-container-low)]"
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
                className="mt-2 w-full text-center font-mono text-[10px] text-[var(--on-surface-variant)] hover:text-[var(--on-surface)] transition-colors py-1"
              >
                {showAllTags
                  ? "Show fewer"
                  : `Show all ${filteredTags.length} tags`}
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}

export interface FilterSidebarProps {
  collections: CollectionRead[];
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
  loading?: boolean;
}

export function FilterSidebar(props: FilterSidebarProps) {
  return (
    <aside className="w-56 shrink-0 overflow-y-auto border-r border-[var(--outline-variant)] bg-[var(--surface-container-lowest)] hidden md:flex flex-col">
      <FilterSidebarContent {...props} />
    </aside>
  );
}
