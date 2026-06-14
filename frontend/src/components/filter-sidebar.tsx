"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "@/lib/navigation";
import { CollectionRead, ModelListItem, PrinterRead, TagRead } from "@/types";
import { Skeleton } from "@/components/ui/skeleton";
import { Box, ChevronRight, Folder, FolderOpen, Search, Trash2, X } from "lucide-react";
import {
  DndContext,
  DragEndEvent,
  DragStartEvent,
  MouseSensor,
  pointerWithin,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
} from "@dnd-kit/core";

interface CollectionNode {
  cat: CollectionRead;
  children: CollectionNode[];
}

type DragPayload =
  | { type: "model"; model: ModelListItem }
  | { type: "collection"; collection: CollectionRead };

function buildTree(cats: CollectionRead[]): CollectionNode[] {
  const byId = new Map<number, CollectionNode>();
  for (const c of cats) byId.set(c.id, { cat: c, children: [] });
  const roots: CollectionNode[] = [];
  for (const node of byId.values()) {
    if (node.cat.parent_id == null) roots.push(node);
    else {
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

function DraggableModelLeaf({
  model,
  isDraggingThisModel,
}: {
  model: ModelListItem;
  isDraggingThisModel: boolean;
}) {
  const router = useRouter();
  const { attributes, listeners, setNodeRef } = useDraggable({
    id: `model-${model.id}`,
    data: { type: "model", model } satisfies DragPayload,
  });

  // No transform: Blender-style — source stays put (dimmed), only target highlights.
  return (
    <div
      ref={setNodeRef}
      {...listeners}
      {...attributes}
      onDoubleClick={() => router.push(`/models/${model.id}`)}
      className={`flex items-center gap-2 rounded px-2 py-1 text-xs cursor-grab active:cursor-grabbing select-none hover:bg-muted transition-colors ${
        isDraggingThisModel ? "opacity-30 pointer-events-none" : "text-muted-foreground"
      }`}
      title={model.name}
    >
      <Box className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground/40" />
      <span className="truncate">{model.name}</span>
    </div>
  );
}

function countDescendants(node: CollectionNode): number {
  return node.children.reduce((acc, c) => acc + 1 + countDescendants(c), 0);
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
  dragging,
  onDelete,
}: {
  node: CollectionNode;
  selected: string | null;
  onSelect: (path: string | null) => void;
  expanded: Set<string>;
  toggle: (path: string) => void;
  modelsByCollection: Map<string, ModelListItem[]>;
  visibleIds?: Set<number> | null;
  visibleModelIds?: Set<number> | null;
  dragging: DragPayload | null;
  onDelete?: (id: number, recursive: boolean) => void;
}) {
  const [confirming, setConfirming] = useState(false);

  const { attributes, listeners, setNodeRef: setDragRef, isDragging } = useDraggable({
    id: `collection-drag-${node.cat.id}`,
    data: { type: "collection", collection: node.cat } satisfies DragPayload,
  });

  const { setNodeRef: setDropRef, isOver } = useDroppable({
    id: `collection-drop-${node.cat.id}`,
    data: { collectionPath: node.cat.path, collectionId: node.cat.id, collectionParentId: node.cat.parent_id },
  });

  const rowRef = (el: HTMLDivElement | null) => { setDragRef(el); setDropRef(el); };

  const expandTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (isOver && dragging !== null) {
      expandTimerRef.current = setTimeout(() => {
        if (!expanded.has(node.cat.path)) toggle(node.cat.path);
      }, 500);
    } else {
      if (expandTimerRef.current) clearTimeout(expandTimerRef.current);
    }
    return () => { if (expandTimerRef.current) clearTimeout(expandTimerRef.current); };
  }, [isOver, dragging]); // eslint-disable-line react-hooks/exhaustive-deps

  const isDraggingCollection = dragging?.type === "collection";
  const isSelf = isDraggingCollection && dragging.collection.id === node.cat.id;
  const isDescendantOfDragged = isDraggingCollection && node.cat.path.startsWith(dragging.collection.path + "/");
  const canDrop = !isSelf && !isDescendantOfDragged;

  const visibleChildren = visibleIds ? node.children.filter((c) => visibleIds.has(c.cat.id)) : node.children;
  const allModelLeaves = modelsByCollection.get(node.cat.path) ?? [];
  const modelLeaves = visibleModelIds ? allModelLeaves.filter((m) => visibleModelIds.has(m.id)) : allModelLeaves;
  const isOpen = visibleIds ? visibleChildren.length > 0 || modelLeaves.length > 0 : expanded.has(node.cat.path);
  const isSelected = selected === node.cat.path;
  const hasNestedItems = visibleChildren.length > 0 || modelLeaves.length > 0;
  const displayChildren = visibleIds ? visibleChildren : node.children;

  const descCount = countDescendants(node);
  const hasContent = descCount > 0 || node.cat.model_count > 0;

  return (
    <div style={isDragging ? { opacity: 0.3 } : undefined} className={isDragging ? "pointer-events-none" : undefined}>
      {confirming ? (
        <div className="my-0.5 rounded border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950/30 px-2 py-1.5">
          <p className="text-[11px] font-medium text-red-700 dark:text-red-400 truncate mb-0.5">
            Delete &ldquo;{node.cat.name}&rdquo;?
          </p>
          {hasContent && (
            <p className="text-[10px] text-muted-foreground mb-1.5 leading-snug">
              {descCount > 0 && <span>{descCount} subcollection{descCount !== 1 ? "s" : ""}</span>}
              {descCount > 0 && node.cat.model_count > 0 && " · "}
              {node.cat.model_count > 0 && <span>{node.cat.model_count} model{node.cat.model_count !== 1 ? "s" : ""} → recycle bin</span>}
            </p>
          )}
          <div className="flex gap-1">
            <button type="button" onClick={() => setConfirming(false)}
              className="flex-1 rounded px-1.5 py-0.5 text-[10px] font-medium bg-muted hover:bg-muted/70 text-muted-foreground transition-colors">
              Cancel
            </button>
            <button type="button"
              onClick={() => { onDelete?.(node.cat.id, hasContent); setConfirming(false); }}
              className="flex-1 rounded px-1.5 py-0.5 text-[10px] font-medium bg-red-600 hover:bg-red-700 text-white transition-colors">
              Delete
            </button>
          </div>
        </div>
      ) : (
        <div
          ref={rowRef}
          className={`group/row relative flex items-center gap-1 rounded px-2 py-1 transition-colors ${
            isOver && dragging !== null && canDrop
              ? "z-10 bg-blue-100 dark:bg-orange-950/60 ring-1 ring-blue-400 dark:ring-orange-500"
              : isSelected
              ? "text-blue-700 dark:text-orange-400 bg-blue-50 dark:bg-orange-950/50"
              : "text-foreground hover:bg-muted"
          }`}
        >
          {hasNestedItems ? (
            <button type="button" onPointerDown={(e) => e.stopPropagation()}
              onClick={(e) => { e.stopPropagation(); toggle(node.cat.path); }}
              className="rounded p-0.5 hover:bg-muted/80 flex-shrink-0" aria-label={isOpen ? "Collapse" : "Expand"}>
              <ChevronRight className={`h-3 w-3 transition-transform ${isOpen ? "rotate-90" : ""}`} />
            </button>
          ) : (
            <span className="inline-block w-4 flex-shrink-0" />
          )}
          <button type="button" onPointerDown={(e) => e.stopPropagation()}
            onClick={() => onSelect(isSelected ? null : node.cat.path)}
            className="flex flex-1 min-w-0 items-center gap-1.5 text-left text-sm font-medium truncate"
            title={node.cat.path} {...attributes}>
            {isOpen || isSelected
              ? <FolderOpen className="h-3.5 w-3.5 flex-shrink-0 text-blue-600 dark:text-orange-500" />
              : <Folder className="h-3.5 w-3.5 flex-shrink-0" />}
            <span className="truncate">{node.cat.name}</span>
          </button>
          <span {...listeners} onPointerDown={(e) => e.stopPropagation()}
            className="p-0.5 text-muted-foreground/30 hover:text-muted-foreground cursor-grab active:cursor-grabbing opacity-0 group-hover/row:opacity-100 flex-shrink-0"
            title="Drag to reorder">
            <svg className="h-2.5 w-2.5" fill="currentColor" viewBox="0 0 16 16">
              <circle cx="5" cy="4" r="1.2" /><circle cx="11" cy="4" r="1.2" />
              <circle cx="5" cy="8" r="1.2" /><circle cx="11" cy="8" r="1.2" />
              <circle cx="5" cy="12" r="1.2" /><circle cx="11" cy="12" r="1.2" />
            </svg>
          </span>
          {onDelete && (
            <button type="button" onPointerDown={(e) => e.stopPropagation()}
              onClick={(e) => { e.stopPropagation(); setConfirming(true); }}
              className="p-0.5 text-muted-foreground/30 hover:text-red-500 opacity-0 group-hover/row:opacity-100 flex-shrink-0 rounded transition-colors"
              title="Delete collection">
              <Trash2 className="h-2.5 w-2.5" />
            </button>
          )}
          <span className="flex-shrink-0 min-w-[18px] rounded bg-muted px-1 py-0.5 text-center text-[11px] font-medium text-muted-foreground">
            {node.cat.model_count}
          </span>
        </div>
      )}
      {isOpen && hasNestedItems && !confirming && (
        <div className="ml-4 border-l border-border pl-3 min-w-0">
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
              dragging={dragging}
              onDelete={onDelete}
            />
          ))}
          {modelLeaves.map((model) => (
            <DraggableModelLeaf
              key={model.id}
              model={model}
              isDraggingThisModel={dragging?.type === "model" && dragging.model.id === model.id}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function DroppableAllModels({
  selected,
  onClick,
  isExpanded,
  onToggleExpand,
  rootModels,
  dragging,
  visibleModelIds,
}: {
  selected: boolean;
  onClick: () => void;
  isExpanded: boolean;
  onToggleExpand: () => void;
  rootModels: ModelListItem[];
  dragging: DragPayload | null;
  visibleModelIds: Set<number> | null;
}) {
  const { setNodeRef, isOver } = useDroppable({
    id: "collection-root",
    data: { collectionPath: null, collectionId: null },
  });

  const displayModels = visibleModelIds
    ? rootModels.filter((m) => visibleModelIds.has(m.id))
    : rootModels;

  return (
    <>
      <button
        ref={setNodeRef}
        type="button"
        onClick={onClick}
        className={`relative w-full flex items-center px-2 py-1.5 text-sm rounded font-medium group transition-colors ${
          isOver && dragging !== null
            ? "z-10 bg-blue-100 dark:bg-orange-950/60 ring-1 ring-blue-400 dark:ring-orange-500"
            : selected
            ? "text-blue-700 dark:text-orange-400 bg-blue-50 dark:bg-orange-950/50"
            : "text-foreground hover:bg-muted"
        }`}
      >
        {rootModels.length > 0 ? (
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onToggleExpand(); }}
            className="rounded p-0.5 hover:bg-muted flex-shrink-0 mr-1"
            aria-label={isExpanded ? "Collapse" : "Expand"}
          >
            <ChevronRight className={`h-3.5 w-3.5 transition-transform ${isExpanded ? "rotate-90" : ""}`} />
          </button>
        ) : (
          <ChevronRight className={`h-4 w-4 mr-1 rotate-90 ${selected ? "text-blue-500 dark:text-orange-400" : "text-muted-foreground"}`} />
        )}
        <FolderOpen className="h-4 w-4 mr-2 text-blue-500 dark:text-orange-400" />
        All Models
      </button>
      {isExpanded && displayModels.length > 0 && (
        <div className="ml-5 border-l border-border pl-4 min-w-0">
          {displayModels.map((model) => (
            <DraggableModelLeaf
              key={model.id}
              model={model}
              isDraggingThisModel={
                dragging?.type === "model" && dragging.model.id === model.id
              }
            />
          ))}
          {displayModels.length > 8 && (
            <div className="px-2 py-1 text-[10px] text-muted-foreground">
              +{displayModels.length - 8} more
            </div>
          )}
        </div>
      )}
    </>
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
  onMoveModel,
  onMoveCollection,
  onDeleteCollection,
  loading,
  outlinerFilter,
  canViewPrinters = true,
}: FilterSidebarProps) {
  const tree = useMemo(() => buildTree(collections), [collections]);
  const outlinerQ = (outlinerFilter ?? "").trim().toLowerCase();
  // When a tag/printer filter is active the `models` list is already narrowed to
  // matching models, so the tree should collapse to the collections that hold
  // them (mirroring how the text filter narrows the outliner).
  const facetFilterActive =
    selectedTags.length > 0 || selectedPrinterId !== null || selectedPrinterPresence !== null;
  const treeFiltered = !!outlinerQ || facetFilterActive;

  const visibleModelIds = useMemo<Set<number> | null>(() => {
    if (!treeFiltered) return null;
    const result = new Set<number>();
    for (const m of models) {
      if (!outlinerQ || m.name.toLowerCase().includes(outlinerQ)) result.add(m.id);
    }
    return result;
  }, [models, outlinerQ, treeFiltered]);

  const visibleCollectionIds = useMemo<Set<number> | null>(() => {
    if (!treeFiltered) return null;
    const byId = new Map(collections.map((c) => [c.id, c]));
    const result = new Set<number>();
    const addWithAncestors = (c: CollectionRead) => {
      result.add(c.id);
      let cur = c.parent_id != null ? byId.get(c.parent_id) : undefined;
      while (cur) { result.add(cur.id); cur = cur.parent_id != null ? byId.get(cur.parent_id) : undefined; }
    };
    // A text query also matches collections by name; a tag/printer filter only
    // surfaces collections that actually contain matching models.
    if (outlinerQ) {
      for (const c of collections) {
        if (c.name.toLowerCase().includes(outlinerQ)) addWithAncestors(c);
      }
    }
    for (const m of models) {
      if (!m.collection || !visibleModelIds?.has(m.id)) continue;
      const col = collections.find((c) => c.path === m.collection);
      if (col) addWithAncestors(col);
    }
    return result;
  }, [collections, models, outlinerQ, treeFiltered, visibleModelIds]);

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

  const autoExpandDoneRef = useRef(false);
  const [expanded, setExpanded] = useState<Set<string>>(() => {
    try {
      const saved = sessionStorage.getItem("ps-filter-expanded");
      if (saved) return new Set(JSON.parse(saved));
    } catch {}
    return new Set();
  });
  const [allModelsExpanded, setAllModelsExpanded] = useState(() => {
    try {
      const saved = sessionStorage.getItem("ps-filter-all-expanded");
      if (saved !== null) return JSON.parse(saved) as boolean;
    } catch {}
    return true;
  });
  const [tagFilter, setTagFilter] = useState("");
  const [showAllTags, setShowAllTags] = useState(false);
  const [printerExpanded, setPrinterExpanded] = useState(false);
  const [dragging, setDragging] = useState<DragPayload | null>(null);

  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
  );

  const sortedTags = useMemo(() => [...tags].sort((a, b) => b.model_count - a.model_count), [tags]);
  const filteredTags = useMemo(() => {
    if (!tagFilter.trim()) return sortedTags;
    const q = tagFilter.toLowerCase();
    return sortedTags.filter((t) => t.name.toLowerCase().includes(q));
  }, [sortedTags, tagFilter]);
  const visibleTags = showAllTags ? filteredTags : filteredTags.slice(0, 10);
  const hiddenCount = filteredTags.length - 10;

  useEffect(() => {
    if (autoExpandDoneRef.current) return;
    try {
      if (sessionStorage.getItem("ps-filter-expanded")) { autoExpandDoneRef.current = true; return; }
    } catch {}
    if (collections.length === 0) return;
    autoExpandDoneRef.current = true;
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
    try { sessionStorage.setItem("ps-filter-expanded", JSON.stringify([...expanded])); } catch {}
  }, [expanded]);

  useEffect(() => {
    try { sessionStorage.setItem("ps-filter-all-expanded", JSON.stringify(allModelsExpanded)); } catch {}
  }, [allModelsExpanded]);

  useEffect(() => {
    if (!selectedCollection) return;
    const parts = selectedCollection.split("/");
    const ancestors = new Set<string>();
    for (let i = 1; i < parts.length; i++) ancestors.add(parts.slice(0, i).join("/"));
    setExpanded((prev) => {
      const next = new Set(prev);
      ancestors.forEach((a) => next.add(a));
      return next;
    });
  }, [selectedCollection]);

  function toggleTag(slug: string) {
    if (selectedTags.includes(slug)) onTagsChange(selectedTags.filter((t) => t !== slug));
    else onTagsChange([...selectedTags, slug]);
  }

  function toggleExpanded(path: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path); else next.add(path);
      return next;
    });
  }

  function handleDragStart(event: DragStartEvent) {
    setDragging(event.active.data.current as DragPayload);
  }

  function handleDragEnd(event: DragEndEvent) {
    setDragging(null);
    const payload = event.active.data.current as DragPayload | undefined;
    if (!payload || !event.over) return;

    const targetCollectionPath = event.over.data.current?.collectionPath as string | null | undefined;
    const targetCollectionId = event.over.data.current?.collectionId as number | null | undefined;
    if (targetCollectionPath === undefined) return;

    if (payload.type === "model") {
      if (targetCollectionPath === (payload.model.collection ?? null)) return;
      onMoveModel?.(payload.model.id, targetCollectionPath);
    } else if (payload.type === "collection") {
      const col = payload.collection;
      if (targetCollectionId === col.id) return;
      if (targetCollectionPath !== null && targetCollectionPath.startsWith(col.path + "/")) return;
      // Drop on "All Models" → move to root (parent_id = null)
      // Drop on a collection → nest inside it (parent_id = that collection's id)
      const newParentId = targetCollectionId ?? null;
      if (newParentId === col.parent_id) return;
      onMoveCollection?.(col.id, newParentId);
    }
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
    <DndContext
      sensors={sensors}
      collisionDetection={pointerWithin}
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
      onDragCancel={() => setDragging(null)}
    >
      <div className="flex-1 overflow-auto py-4 px-3 space-y-6">
        {/* Collections */}
        <section>
          <div className="flex items-center justify-between mb-2 pl-2 pr-1">
            <h3 className="text-xs font-bold text-muted-foreground uppercase tracking-wider">
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
          <div className="overflow-x-auto -mx-3 px-3">
          <div className="min-w-max space-y-0.5 pr-2">
            <DroppableAllModels
              selected={selectedCollection === null}
              onClick={() => onCollectionChange(null)}
              isExpanded={allModelsExpanded}
              onToggleExpand={() => setAllModelsExpanded((v) => !v)}
              rootModels={rootModels}
              dragging={dragging}
              visibleModelIds={visibleModelIds}
            />
            <div className="ml-5 border-l border-border pl-4 min-w-0">
              {visibleRoots.length === 0 && treeFiltered && (visibleModelIds?.size ?? 0) === 0 ? (
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
                    dragging={dragging}
                    onDelete={onDeleteCollection}
                  />
                ))
              )}
            </div>
          </div>
          </div>
        </section>

        {/* Printer */}
        {canViewPrinters && (
        <section>
          <h3 className="text-xs font-bold text-muted-foreground uppercase tracking-wider mb-2 pl-2">
            Printer
          </h3>
          <div className="space-y-0.5">
            <button
              type="button"
              onClick={() => { onPrinterChange(null); onPrinterPresenceChange(null); }}
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
                    <p className="pl-4 py-1 text-[11px] text-muted-foreground font-mono">No printers configured</p>
                  ) : (
                    printers.map((printer) => (
                      <button
                        key={printer.id}
                        type="button"
                        onClick={() => { onPrinterChange(printer.id); onPrinterPresenceChange(null); }}
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
              onClick={() => { onPrinterChange(null); onPrinterPresenceChange("none"); }}
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
        )}

        {/* Tags */}
        {tags.length > 0 && (
          <section>
            <h3 className="text-xs font-bold text-muted-foreground uppercase tracking-wider mb-2 pl-2">
              Tags
            </h3>
            <div className="relative mb-2">
              <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3 w-3 text-muted-foreground" />
              <input
                type="text"
                placeholder="Filter tags..."
                value={tagFilter}
                onChange={(e) => { setTagFilter(e.target.value); setShowAllTags(false); }}
                className="w-full pl-7 pr-2 py-1.5 text-sm border border-border rounded bg-muted text-foreground font-mono placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-blue-500 dark:focus:ring-orange-500 focus:border-blue-500 dark:focus:border-blue-500 dark:border-orange-500 transition-colors"
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
                      className={`flex items-center gap-1 px-2 py-1 rounded font-mono text-[11px] tracking-wider uppercase border transition-colors ${
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

    </DndContext>
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
  onMoveModel?: (modelId: number, targetCollection: string | null) => void;
  onMoveCollection?: (collectionId: number, newParentId: number | null) => void;
  onDeleteCollection?: (id: number, recursive: boolean) => void;
  canViewPrinters?: boolean;
  loading?: boolean;
  outlinerFilter?: string;
}

export function FilterSidebar(props: FilterSidebarProps) {
  const [outlinerFilter, setOutlinerFilter] = useState("");
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    try { return parseInt(localStorage.getItem("ps-sidebar-width") ?? "220", 10); } catch { return 220; }
  });

  useEffect(() => {
    try { localStorage.setItem("ps-sidebar-width", String(sidebarWidth)); } catch {}
  }, [sidebarWidth]);

  function handleResizeStart(e: React.MouseEvent) {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = sidebarWidth;
    const onMove = (ev: MouseEvent) => {
      setSidebarWidth(Math.min(520, Math.max(180, startWidth + ev.clientX - startX)));
    };
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }

  return (
    <aside style={{ width: sidebarWidth }} className="bg-[var(--sidebar-bg)] border-r border-border flex flex-col shrink-0 hidden md:flex relative">
      <div className="p-2 border-b border-border bg-[var(--sidebar-bg)]">
        <div className="relative">
          <span className="absolute inset-y-0 left-0 pl-2 flex items-center text-muted-foreground">
            <Search className="h-3.5 w-3.5" />
          </span>
          <input
            className="block w-full pl-7 pr-6 py-1.5 text-sm border border-border rounded bg-muted text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-blue-500 dark:focus:ring-orange-500"
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
      {/* Resize handle */}
      <div
        onMouseDown={handleResizeStart}
        className="absolute right-0 top-0 bottom-0 w-1.5 cursor-col-resize hover:bg-blue-400/50 dark:hover:bg-orange-400/50 transition-colors z-50"
      />
    </aside>
  );
}
