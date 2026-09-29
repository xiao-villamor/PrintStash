"use client";

import { knownUiText } from "@/lib/locale";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "@/lib/navigation";
import {
  CollectionNodeRead,
  MultipartModelListItem,
  OutlinerModelRead,
  PrinterRead,
  TagRead,
} from "@/types";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ancestorPaths,
  buildFilteredTree,
  type FilteredFolder,
  type FolderEntry,
} from "@/lib/collection-tree";
import { useCollectionChildren, useCollectionSearch } from "@/lib/queries";
import { Localized } from "@/components/ui/localized";
import { useI18n } from "@/lib/i18n";
import { Box, Boxes, ChevronRight, Folder, FolderOpen, Search, Trash2, X } from "lucide-react";
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

export type LibraryViewMode = "organized" | "all" | "multipart" | "components";

const LIBRARY_VIEWS: LibraryViewMode[] = ["organized", "all", "multipart", "components"];

type DragPayload =
  | { type: "model"; model: OutlinerModelRead }
  | { type: "collection"; collection: CollectionNodeRead };

/** Data every collection drop target in this sidebar registers with dnd-kit. */
interface CollectionDropData {
  /** Destination collection, or null for the "All Models" root. */
  collectionPath: string | null;
  collectionId: number | null;
  collectionParentId?: number | null;
}

/**
 * The payload of the drag in flight, or null when dnd-kit reports no active
 * data. dnd-kit types `data.current` as an open bag, so it is narrowed back
 * here — once — instead of at every read.
 */
function activeDragPayload(event: DragStartEvent | DragEndEvent): DragPayload | null {
  const data = event.active.data.current;
  if (data === undefined) return null;
  // SAFETY: the only `useDraggable` calls in this file are DraggableModelLeaf
  // and the collection row, and both build their `data` with
  // `satisfies DragPayload`, so an active drag always carries one variant.
  return data as DragPayload;
}

/**
 * The collection under the pointer, or null when the drag ended outside one of
 * this sidebar's drop targets.
 */
function collectionDropTarget(event: DragEndEvent): CollectionDropData | null {
  const data = event.over?.data.current;
  if (data === undefined || !("collectionPath" in data)) return null;
  // SAFETY: `collectionPath` is registered by exactly two `useDroppable` calls
  // in this file — a collection row and the "All Models" root — and both build
  // their `data` with `satisfies CollectionDropData`.
  return data as CollectionDropData;
}

const EXPANDED_KEY = "ps-filter-expanded";
const ALL_EXPANDED_KEY = "ps-filter-all-expanded";

/** The expanded collection paths persisted this session, or null if none are. */
function readExpandedPaths(): Set<string> | null {
  try {
    const saved = sessionStorage.getItem(EXPANDED_KEY);
    if (!saved) return null;
    const parsed: unknown = JSON.parse(saved);
    return Array.isArray(parsed) ? new Set(parsed.map(String)) : null;
  } catch {
    return null;
  }
}

/** Is the "All Models" group expanded? Open unless this session closed it. */
function readAllModelsExpanded(): boolean {
  try {
    return sessionStorage.getItem(ALL_EXPANDED_KEY) !== "false";
  } catch {
    return true;
  }
}

function DraggableModelLeaf({
  model,
  isDraggingThisModel,
}: {
  model: OutlinerModelRead;
  isDraggingThisModel: boolean;
}) {
  useUiLocale();
  const router = useRouter();
  const { attributes, listeners, setNodeRef } = useDraggable({
    id: `model-${model.id}`,
    data: { type: "model", model } satisfies DragPayload,
  });

  // No transform: Blender-style — source stays put (dimmed), only target highlights.
  return (
    <Localized>
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
    </Localized>
  );
}

function MultipartLeaf({ multipart }: { multipart: MultipartModelListItem }) {
  useUiLocale();
  const router = useRouter();

  return (
    <Localized>
      <div
        onDoubleClick={() => router.push(`/multipart-models/${multipart.id}`)}
        className="flex cursor-default select-none items-center gap-2 rounded px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted"
        title={uiText("{value1} · Multipart set", { value1: String(multipart.name) })}
      >
        <Boxes className="h-3.5 w-3.5 flex-shrink-0 text-primary" />
        <span className="truncate">{multipart.name}</span>
      </div>
    </Localized>
  );
}

type OutlinerLeaf =
  | { kind: "model"; model: OutlinerModelRead }
  | { kind: "multipart"; multipart: MultipartModelListItem };

function mergeLeaves(
  models: OutlinerModelRead[],
  multipartModels: MultipartModelListItem[],
): OutlinerLeaf[] {
  return [
    ...models.map((model) => ({ kind: "model" as const, model })),
    ...multipartModels.map((multipart) => ({ kind: "multipart" as const, multipart })),
  ].sort((a, b) => {
    const aName = a.kind === "model" ? a.model.name : a.multipart.name;
    const bName = b.kind === "model" ? b.model.name : b.multipart.name;
    return aName.localeCompare(bName);
  });
}

function OutlinerLeaves({
  leaves,
  dragging,
}: {
  leaves: OutlinerLeaf[];
  dragging: DragPayload | null;
}) {
  useUiLocale();
  return leaves.map((leaf) =>
    leaf.kind === "model" ? (
      <DraggableModelLeaf
        key={`model-${leaf.model.id}`}
        model={leaf.model}
        isDraggingThisModel={dragging?.type === "model" && dragging.model.id === leaf.model.id}
      />
    ) : (
      <MultipartLeaf key={`multipart-${leaf.multipart.id}`} multipart={leaf.multipart} />
    ),
  );
}

/** What every row of the folder tree reads, gathered so it is passed once. */
interface TreeContext {
  selected: string | null;
  onSelect: (path: string | null) => void;
  onIntent?: (path: string) => void;
  expanded: Set<string>;
  toggle: (path: string) => void;
  modelsByCollection: Map<string, OutlinerModelRead[]>;
  multipartByCollection: Map<string, MultipartModelListItem[]>;
  visibleModelIds: Set<number> | null;
  visibleMultipartIds: Set<number> | null;
  /** The badge for a folder: its subtree total, or the loaded leaves below it. */
  badge: (path: string, node: CollectionNodeRead | null) => number;
  dragging: DragPayload | null;
  onDelete?: (id: number, recursive: boolean) => void;
}

/** A folder's own leaves, narrowed to the ones the filter kept. */
function folderLeaves(path: string, ctx: TreeContext): OutlinerLeaf[] {
  const models = (ctx.modelsByCollection.get(path) ?? []).filter(
    (model) => !ctx.visibleModelIds || ctx.visibleModelIds.has(model.id),
  );
  const multipart = (ctx.multipartByCollection.get(path) ?? []).filter(
    (item) => !ctx.visibleMultipartIds || ctx.visibleMultipartIds.has(item.id),
  );
  return mergeLeaves(models, multipart);
}

/**
 * One level of the tree: the children of `parentId`, or the top level when it is
 * null. Mounted only for an open folder, so the sidebar loads what the user
 * opens and nothing else; a long level loads a page at a time.
 */
function CollectionLevel({ parentId, ctx }: { parentId: number | null; ctx: TreeContext }) {
  useUiLocale();
  const level = useCollectionChildren(parentId);
  if (level.isPending) {
    return <Skeleton className="my-1 h-4 w-3/4" />;
  }
  if (level.isError) {
    return (
      <p className="py-1 text-3xs text-muted-foreground font-mono">
        {uiText("Folders could not be loaded.")}
      </p>
    );
  }
  const nodes = level.data.pages.flatMap((page) => page.items);
  return (
    <Localized>
      <>
        {nodes.map((node) => (
          <CollectionTreeRow key={node.id} node={node} ctx={ctx} />
        ))}
        {level.hasNextPage && (
          <button
            type="button"
            disabled={level.isFetchingNextPage}
            onClick={() => void level.fetchNextPage()}
            className="w-full rounded px-2 py-1 text-left text-3xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            {uiText("Show more folders")}
          </button>
        )}
      </>
    </Localized>
  );
}

function CollectionTreeRow({ node, ctx }: { node: CollectionNodeRead; ctx: TreeContext }) {
  useUiLocale();
  const [confirming, setConfirming] = useState(false);
  const { selected, onSelect, onIntent, expanded, toggle, dragging, onDelete } = ctx;

  const {
    attributes,
    listeners,
    setNodeRef: setDragRef,
    isDragging,
  } = useDraggable({
    id: `collection-drag-${node.id}`,
    data: { type: "collection", collection: node } satisfies DragPayload,
  });

  const { setNodeRef: setDropRef, isOver } = useDroppable({
    id: `collection-drop-${node.id}`,
    data: {
      collectionPath: node.path,
      collectionId: node.id,
      collectionParentId: node.parent_id,
    } satisfies CollectionDropData,
  });

  const rowRef = (el: HTMLDivElement | null) => {
    setDragRef(el);
    setDropRef(el);
  };

  const expandTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (isOver && dragging !== null) {
      expandTimerRef.current = setTimeout(() => {
        if (!expanded.has(node.path)) toggle(node.path);
      }, 500);
    } else {
      if (expandTimerRef.current) clearTimeout(expandTimerRef.current);
    }
    return () => {
      if (expandTimerRef.current) clearTimeout(expandTimerRef.current);
    };
  }, [isOver, dragging]); // eslint-disable-line react-hooks/exhaustive-deps

  const isDraggingCollection = dragging?.type === "collection";
  const isSelf = isDraggingCollection && dragging.collection.id === node.id;
  const isDescendantOfDragged =
    isDraggingCollection && node.path.startsWith(dragging.collection.path + "/");
  const canDrop = !isSelf && !isDescendantOfDragged;

  const leaves = folderLeaves(node.path, ctx);
  const isOpen = expanded.has(node.path);
  const isSelected = selected === node.path;
  const hasNestedItems = node.child_count > 0 || leaves.length > 0;
  const descCount = node.descendant_count;
  const hasContent = descCount > 0 || node.model_count > 0;

  return (
    <Localized>
      <div
        style={isDragging ? { opacity: 0.3 } : undefined}
        className={isDragging ? "pointer-events-none" : undefined}
      >
        {confirming ? (
          <div className="my-0.5 rounded border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950/30 px-2 py-1.5">
            <p className="text-2xs font-medium text-red-700 dark:text-red-400 truncate mb-0.5">
              {uiText("Delete “{value1}”?", { value1: String(node.name ?? "") })}
            </p>
            {hasContent && (
              <p className="text-3xs text-muted-foreground mb-1.5 leading-snug">
                {descCount > 0 && (
                  <span>{uiText("counts.subcollections", { count: descCount })}</span>
                )}
                {descCount > 0 && node.model_count > 0 && " · "}
                {node.model_count > 0 && (
                  <span>
                    {uiText("counts.models", { count: node.model_count })}
                    {uiText(" → recycle bin")}
                  </span>
                )}
              </p>
            )}
            <div className="flex gap-1">
              <button
                type="button"
                onClick={() => setConfirming(false)}
                className="flex-1 rounded px-1.5 py-0.5 text-3xs font-medium bg-muted hover:bg-muted/70 text-muted-foreground transition-colors"
              >
                {uiText("Cancel")}
              </button>
              <button
                type="button"
                onClick={() => {
                  onDelete?.(node.id, hasContent);
                  setConfirming(false);
                }}
                className="flex-1 rounded px-1.5 py-0.5 text-3xs font-medium bg-red-600 hover:bg-red-700 text-white transition-colors"
              >
                {uiText("Delete")}
              </button>
            </div>
          </div>
        ) : (
          <div
            ref={rowRef}
            className={`group/row relative flex items-center gap-1 rounded px-2 py-1 transition-colors ${
              isOver && dragging !== null && canDrop
                ? "z-10 bg-accent"
                : isSelected
                  ? "text-accent-foreground bg-accent"
                  : "text-foreground hover:bg-muted"
            }`}
          >
            {hasNestedItems ? (
              <button
                type="button"
                onPointerDown={(e) => e.stopPropagation()}
                onClick={(e) => {
                  e.stopPropagation();
                  toggle(node.path);
                }}
                className="rounded p-0.5 hover:bg-muted/80 flex-shrink-0"
                aria-label={isOpen ? uiText("Collapse") : uiText("Expand")}
              >
                <ChevronRight
                  className={`h-3 w-3 transition-transform ${isOpen ? "rotate-90" : ""}`}
                />
              </button>
            ) : (
              <span className="inline-block w-4 flex-shrink-0" />
            )}
            <button
              type="button"
              onPointerDown={(e) => e.stopPropagation()}
              onPointerEnter={() => onIntent?.(node.path)}
              onFocus={() => onIntent?.(node.path)}
              onClick={() => onSelect(node.path)}
              className="flex flex-1 min-w-0 items-center gap-1.5 text-left text-sm font-medium truncate"
              title={node.name}
              {...attributes}
            >
              {isOpen || isSelected ? (
                <FolderOpen className="h-3.5 w-3.5 flex-shrink-0 text-primary" />
              ) : (
                <Folder className="h-3.5 w-3.5 flex-shrink-0" />
              )}
              <span className="truncate">{node.name}</span>
            </button>
            <span
              {...listeners}
              onPointerDown={(e) => e.stopPropagation()}
              className="p-0.5 text-muted-foreground/30 hover:text-muted-foreground cursor-grab active:cursor-grabbing opacity-0 group-hover/row:opacity-100 flex-shrink-0"
              title={uiText("Drag to reorder")}
            >
              <svg className="h-2.5 w-2.5" fill="currentColor" viewBox="0 0 16 16">
                <circle cx="5" cy="4" r="1.2" />
                <circle cx="11" cy="4" r="1.2" />
                <circle cx="5" cy="8" r="1.2" />
                <circle cx="11" cy="8" r="1.2" />
                <circle cx="5" cy="12" r="1.2" />
                <circle cx="11" cy="12" r="1.2" />
              </svg>
            </span>
            {onDelete && (
              <button
                type="button"
                onPointerDown={(e) => e.stopPropagation()}
                onClick={(e) => {
                  e.stopPropagation();
                  setConfirming(true);
                }}
                className="p-0.5 text-muted-foreground/30 hover:text-red-500 opacity-0 group-hover/row:opacity-100 flex-shrink-0 rounded transition-colors"
                title={uiText("Delete collection")}
              >
                <Trash2 className="h-2.5 w-2.5" />
              </button>
            )}
            <span className="flex-shrink-0 min-w-[18px] rounded bg-muted px-1 py-0.5 text-center text-2xs font-medium text-muted-foreground">
              {ctx.badge(node.path, node)}
            </span>
          </div>
        )}
        {isOpen && hasNestedItems && !confirming && (
          <div className="ml-4 border-l border-border pl-3 min-w-0">
            {node.child_count > 0 && <CollectionLevel parentId={node.id} ctx={ctx} />}
            <OutlinerLeaves leaves={leaves} dragging={dragging} />
          </div>
        )}
      </div>
    </Localized>
  );
}

/**
 * A folder on the way to something the outliner filter matched. Always open:
 * the filtered tree exists only to reach its matches.
 */
function FilteredFolderRow({ folder, ctx }: { folder: FilteredFolder; ctx: TreeContext }) {
  useUiLocale();
  const leaves = folderLeaves(folder.path, ctx);
  const isSelected = ctx.selected === folder.path;
  return (
    <Localized>
      <div>
        <div
          className={`relative flex items-center gap-1 rounded px-2 py-1 transition-colors ${
            isSelected ? "text-accent-foreground bg-accent" : "text-foreground hover:bg-muted"
          }`}
        >
          <span className="inline-block w-4 flex-shrink-0" />
          <button
            type="button"
            onPointerEnter={() => ctx.onIntent?.(folder.path)}
            onFocus={() => ctx.onIntent?.(folder.path)}
            onClick={() => ctx.onSelect(folder.path)}
            className="flex flex-1 min-w-0 items-center gap-1.5 text-left text-sm font-medium truncate"
            title={folder.name}
          >
            <FolderOpen className="h-3.5 w-3.5 flex-shrink-0 text-primary" />
            <span className="truncate">{folder.name}</span>
          </button>
          <span className="flex-shrink-0 min-w-[18px] rounded bg-muted px-1 py-0.5 text-center text-2xs font-medium text-muted-foreground">
            {ctx.badge(folder.path, folder.node)}
          </span>
        </div>
        {(folder.children.length > 0 || leaves.length > 0) && (
          <div className="ml-4 border-l border-border pl-3 min-w-0">
            {folder.children.map((child) => (
              <FilteredFolderRow key={child.path} folder={child} ctx={ctx} />
            ))}
            <OutlinerLeaves leaves={leaves} dragging={ctx.dragging} />
          </div>
        )}
      </div>
    </Localized>
  );
}

function DroppableAllModels({
  selected,
  onClick,
  isExpanded,
  onToggleExpand,
  rootModels,
  rootMultipartModels,
  dragging,
  visibleModelIds,
  visibleMultipartIds,
}: {
  selected: boolean;
  onClick: () => void;
  isExpanded: boolean;
  onToggleExpand: () => void;
  rootModels: OutlinerModelRead[];
  rootMultipartModels: MultipartModelListItem[];
  dragging: DragPayload | null;
  visibleModelIds: Set<number> | null;
  visibleMultipartIds: Set<number> | null;
}) {
  useUiLocale();
  const { setNodeRef, isOver } = useDroppable({
    id: "collection-root",
    data: { collectionPath: null, collectionId: null } satisfies CollectionDropData,
  });

  const displayModels = visibleModelIds
    ? rootModels.filter((m) => visibleModelIds.has(m.id))
    : rootModels;
  const displayMultipartModels = visibleMultipartIds
    ? rootMultipartModels.filter((multipart) => visibleMultipartIds.has(multipart.id))
    : rootMultipartModels;
  const leaves = mergeLeaves(displayModels, displayMultipartModels);

  return (
    <Localized>
      <>
        <div
          ref={setNodeRef}
          role="button"
          tabIndex={0}
          aria-label={uiText("All Models")}
          onClick={onClick}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              onClick();
            }
          }}
          className={`relative w-full flex items-center px-2 py-1.5 text-sm rounded font-medium group transition-colors ${
            isOver && dragging !== null
              ? "z-10 bg-accent"
              : selected
                ? "text-accent-foreground bg-accent"
                : "text-foreground hover:bg-muted"
          }`}
        >
          {rootModels.length > 0 || rootMultipartModels.length > 0 ? (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onToggleExpand();
              }}
              className="rounded p-0.5 hover:bg-muted flex-shrink-0 mr-1"
              aria-label={isExpanded ? uiText("Collapse") : uiText("Expand")}
            >
              <ChevronRight
                className={`h-3.5 w-3.5 transition-transform ${isExpanded ? "rotate-90" : ""}`}
              />
            </button>
          ) : (
            <ChevronRight
              className={`h-4 w-4 mr-1 rotate-90 ${selected ? "text-primary" : "text-muted-foreground"}`}
            />
          )}
          <FolderOpen className="h-4 w-4 mr-2 text-primary" />
          {uiText("All Models")}
        </div>
        {isExpanded && leaves.length > 0 && (
          <div className="ml-5 border-l border-border pl-4 min-w-0">
            <OutlinerLeaves leaves={leaves} dragging={dragging} />
            {leaves.length > 8 && (
              <div className="px-2 py-1 text-3xs text-muted-foreground">
                +{leaves.length - 8}
                {uiText(" more")}
              </div>
            )}
          </div>
        )}
      </>
    </Localized>
  );
}

export function FilterSidebarContent({
  models = [],
  multipartModels = [],
  tags,
  printers,
  selectedCollection,
  selectedTags,
  selectedPrinterId,
  selectedPrinterPresence,
  onCollectionChange,
  onCollectionIntent,
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
  structuredFilters,
  filtersOpen = true,
  libraryView,
  onLibraryViewChange,
}: FilterSidebarProps) {
  useUiLocale();
  const { t } = useI18n();
  const outlinerQ = (outlinerFilter ?? "").trim().toLowerCase();
  // When a tag/printer filter is active the `models` list is already narrowed to
  // matching models, so the tree should collapse to the collections that hold
  // them (mirroring how the text filter narrows the outliner).
  const facetFilterActive =
    selectedTags.length > 0 || selectedPrinterId !== null || selectedPrinterPresence !== null;
  const treeFiltered = !!outlinerQ || facetFilterActive;

  const memberModelIds = useMemo(
    () => new Set(multipartModels.flatMap((multipart) => multipart.member_model_ids)),
    [multipartModels],
  );
  const treeModels = useMemo(() => {
    if (libraryView === "multipart") return [];
    if (libraryView === "components") {
      return models.filter((model) => memberModelIds.has(model.id));
    }
    if (libraryView === "organized") {
      return models.filter((model) => !memberModelIds.has(model.id));
    }
    return models;
  }, [libraryView, memberModelIds, models]);
  const treeMultipartModels = useMemo(
    () => (libraryView === "components" ? [] : multipartModels),
    [libraryView, multipartModels],
  );

  const visibleModelIds = useMemo<Set<number> | null>(() => {
    if (!treeFiltered) return null;
    const result = new Set<number>();
    for (const m of treeModels) {
      if (!outlinerQ || m.name.toLowerCase().includes(outlinerQ)) result.add(m.id);
    }
    return result;
  }, [outlinerQ, treeFiltered, treeModels]);

  const visibleMultipartIds = useMemo<Set<number> | null>(() => {
    if (!treeFiltered) return null;
    const result = new Set<number>();
    for (const multipart of treeMultipartModels) {
      if (!outlinerQ || multipart.name.toLowerCase().includes(outlinerQ)) {
        result.add(multipart.id);
      }
    }
    return result;
  }, [outlinerQ, treeFiltered, treeMultipartModels]);

  // The name filter also matches folders; the server searches them, since the
  // sidebar no longer holds a list to search (#295).
  const [nameQuery, setNameQuery] = useState(outlinerQ);
  useEffect(() => {
    const timer = setTimeout(() => setNameQuery(outlinerQ), 250);
    return () => clearTimeout(timer);
  }, [outlinerQ]);
  const nameMatches = useCollectionSearch(nameQuery, "view", { enabled: nameQuery !== "" });

  const modelsByCollection = useMemo(() => {
    const grouped = new Map<string, OutlinerModelRead[]>();
    for (const model of treeModels) {
      if (!model.collection) continue;
      const current = grouped.get(model.collection) ?? [];
      current.push(model);
      grouped.set(model.collection, current);
    }
    for (const items of grouped.values()) {
      items.sort((a, b) => a.name.localeCompare(b.name));
    }
    return grouped;
  }, [treeModels]);

  const multipartByCollection = useMemo(() => {
    const grouped = new Map<string, MultipartModelListItem[]>();
    for (const multipart of treeMultipartModels) {
      if (!multipart.collection) continue;
      const current = grouped.get(multipart.collection) ?? [];
      current.push(multipart);
      grouped.set(multipart.collection, current);
    }
    for (const items of grouped.values()) {
      items.sort((a, b) => a.name.localeCompare(b.name));
    }
    return grouped;
  }, [treeMultipartModels]);

  // While filtering, the tree is only the folders on the way to what matched:
  // folders named like the query, and the folders holding matching leaves, all
  // named from the labels the server sends with them.
  const filteredTree = useMemo(() => {
    if (!treeFiltered) return [];
    const entries: FolderEntry[] = [];
    if (outlinerQ) {
      for (const node of nameMatches.data?.pages.flatMap((page) => page.items) ?? []) {
        entries.push({ path: node.path, label: node.display_path, node });
      }
    }
    for (const model of treeModels) {
      if (model.collection && model.collection_label && visibleModelIds?.has(model.id)) {
        entries.push({ path: model.collection, label: model.collection_label });
      }
    }
    for (const multipart of treeMultipartModels) {
      if (
        multipart.collection &&
        multipart.collection_label &&
        visibleMultipartIds?.has(multipart.id)
      ) {
        entries.push({ path: multipart.collection, label: multipart.collection_label });
      }
    }
    return buildFilteredTree(entries);
  }, [
    nameMatches.data,
    outlinerQ,
    treeFiltered,
    treeModels,
    treeMultipartModels,
    visibleModelIds,
    visibleMultipartIds,
  ]);

  // A badge is the folder's subtree total from the server, unless the view or a
  // filter narrows what counts; then it counts the loaded leaves below it.
  const useCatalogCounts = !treeFiltered && (libraryView === "organized" || libraryView === "all");
  const badge = useCallback(
    (path: string, node: CollectionNodeRead | null) => {
      const below = (key: string) => key === path || key.startsWith(`${path}/`);
      let modelLeaves = 0;
      for (const [key, items] of modelsByCollection) {
        if (below(key)) {
          modelLeaves += items.filter((m) => !visibleModelIds || visibleModelIds.has(m.id)).length;
        }
      }
      let multipartLeaves = 0;
      for (const [key, items] of multipartByCollection) {
        if (below(key)) {
          multipartLeaves += items.filter(
            (item) => !visibleMultipartIds || visibleMultipartIds.has(item.id),
          ).length;
        }
      }
      return (useCatalogCounts && node !== null ? node.model_count : modelLeaves) + multipartLeaves;
    },
    [
      modelsByCollection,
      multipartByCollection,
      useCatalogCounts,
      visibleModelIds,
      visibleMultipartIds,
    ],
  );

  const rootModels = useMemo(
    () => treeModels.filter((m) => !m.collection).sort((a, b) => a.name.localeCompare(b.name)),
    [treeModels],
  );
  const rootMultipartModels = useMemo(
    () =>
      treeMultipartModels
        .filter((multipart) => !multipart.collection)
        .sort((a, b) => a.name.localeCompare(b.name)),
    [treeMultipartModels],
  );

  const [expanded, setExpanded] = useState<Set<string>>(() => {
    // A first visit starts at the top level: the tree loads a level only when
    // it is opened, and opening a large library whole is what #295 was.
    const initial = readExpandedPaths() ?? new Set<string>();
    if (selectedCollection) {
      for (const ancestor of ancestorPaths(selectedCollection)) initial.add(ancestor);
    }
    return initial;
  });
  const [allModelsExpanded, setAllModelsExpanded] = useState(readAllModelsExpanded);
  const [tagFilter, setTagFilter] = useState("");
  const [showAllTags, setShowAllTags] = useState(false);
  const [printerExpanded, setPrinterExpanded] = useState(false);
  const [dragging, setDragging] = useState<DragPayload | null>(null);

  const sensors = useSensors(useSensor(MouseSensor, { activationConstraint: { distance: 6 } }));

  const sortedTags = useMemo(
    () =>
      [...tags].sort(
        (a, b) =>
          b.model_count +
          (b.multipart_model_count ?? 0) -
          (a.model_count + (a.multipart_model_count ?? 0)),
      ),
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
    try {
      sessionStorage.setItem(EXPANDED_KEY, JSON.stringify([...expanded]));
    } catch {}
  }, [expanded]);

  useEffect(() => {
    try {
      sessionStorage.setItem(ALL_EXPANDED_KEY, JSON.stringify(allModelsExpanded));
    } catch {}
  }, [allModelsExpanded]);

  // Selecting a nested collection reveals it: open its ancestors as the
  // selection changes, rather than re-syncing from an effect.
  const [revealedSelection, setRevealedSelection] = useState(selectedCollection);
  if (revealedSelection !== selectedCollection) {
    setRevealedSelection(selectedCollection);
    if (selectedCollection) {
      const ancestors = ancestorPaths(selectedCollection);
      setExpanded((prev) => new Set([...prev, ...ancestors]));
    }
  }

  function toggleTag(slug: string) {
    if (selectedTags.includes(slug)) onTagsChange(selectedTags.filter((t) => t !== slug));
    else onTagsChange([...selectedTags, slug]);
  }

  function toggleExpanded(path: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  function handleDragStart(event: DragStartEvent) {
    setDragging(activeDragPayload(event));
  }

  function handleDragEnd(event: DragEndEvent) {
    setDragging(null);
    const payload = activeDragPayload(event);
    const target = collectionDropTarget(event);
    if (!payload || !target) return;

    const targetCollectionPath = target.collectionPath;
    const targetCollectionId = target.collectionId;

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

  const treeContext: TreeContext = {
    selected: selectedCollection,
    onSelect: onCollectionChange,
    onIntent: onCollectionIntent,
    expanded,
    toggle: toggleExpanded,
    modelsByCollection,
    multipartByCollection,
    visibleModelIds,
    visibleMultipartIds,
    badge,
    dragging,
    onDelete: onDeleteCollection,
  };

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
    s === "printing"
      ? "bg-primary"
      : s === "ready"
        ? "bg-green-500"
        : s === "paused"
          ? "bg-amber-500"
          : s === "error"
            ? "bg-red-500"
            : "bg-slate-400";

  const statusLabel = (status: string) =>
    knownUiText(status.charAt(0).toUpperCase() + status.slice(1));

  const statusTextColor = (s: string) =>
    s === "printing"
      ? "text-primary"
      : s === "error"
        ? "text-red-500"
        : s === "ready"
          ? "text-green-500"
          : s === "paused"
            ? "text-amber-500"
            : "text-muted-foreground";

  return (
    <Localized>
      <DndContext
        sensors={sensors}
        collisionDetection={pointerWithin}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
        onDragCancel={() => setDragging(null)}
      >
        <div className="flex-1 overflow-auto py-4 px-3 space-y-6">
          <section>
            <h3 className="mb-2 pl-2 text-xs font-bold uppercase tracking-wider text-muted-foreground">
              {t("libraryView.title")}
            </h3>
            <div className="space-y-0.5">
              {LIBRARY_VIEWS.map((view) => (
                <button
                  key={view}
                  type="button"
                  aria-pressed={libraryView === view}
                  onClick={() => onLibraryViewChange(view)}
                  className={`w-full rounded px-2 py-1.5 text-left text-sm font-medium transition-colors ${
                    libraryView === view
                      ? "bg-accent text-accent-foreground"
                      : "text-foreground hover:bg-muted"
                  }`}
                >
                  {view === "organized"
                    ? t("libraryView.organized")
                    : view === "all"
                      ? t("libraryView.all")
                      : view === "multipart"
                        ? t("libraryView.multipart")
                        : t("libraryView.components")}
                </button>
              ))}
            </div>
          </section>

          {/* Collections */}
          <section>
            <div className="flex items-center justify-between mb-2 pl-2 pr-1">
              <h3 className="text-xs font-bold text-muted-foreground uppercase tracking-wider">
                {uiText("Collections")}
              </h3>
              <button
                onClick={onCreateCollection}
                className="p-0.5 text-muted-foreground hover:text-foreground hover:bg-muted rounded transition-colors"
                title={uiText("Create Collection")}
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path
                    d="M12 4v16m8-8H4"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth="2.5"
                  />
                </svg>
              </button>
            </div>
            <div className="overflow-x-auto -mx-3 px-3">
              <div className="min-w-0 space-y-0.5 pr-2">
                <DroppableAllModels
                  selected={selectedCollection === null}
                  onClick={() => onCollectionChange(null)}
                  isExpanded={allModelsExpanded}
                  onToggleExpand={() => setAllModelsExpanded((v) => !v)}
                  rootModels={rootModels}
                  rootMultipartModels={rootMultipartModels}
                  dragging={dragging}
                  visibleModelIds={visibleModelIds}
                  visibleMultipartIds={visibleMultipartIds}
                />
                <div className="ml-5 border-l border-border pl-4 min-w-0">
                  {!treeFiltered ? (
                    <CollectionLevel parentId={null} ctx={treeContext} />
                  ) : filteredTree.length === 0 &&
                    (visibleModelIds?.size ?? 0) === 0 &&
                    (visibleMultipartIds?.size ?? 0) === 0 ? (
                    <p className="py-2 text-3xs text-muted-foreground font-mono">
                      {uiText("No results.")}
                    </p>
                  ) : (
                    filteredTree.map((folder) => (
                      <FilteredFolderRow key={folder.path} folder={folder} ctx={treeContext} />
                    ))
                  )}
                </div>
              </div>
            </div>
          </section>

          {filtersOpen && (
            <div className="space-y-6" aria-label={uiText("Filters")} role="region">
              {/* Printer */}
              {canViewPrinters && (
                <section>
                  <h3 className="text-xs font-bold text-muted-foreground uppercase tracking-wider mb-2 pl-2">
                    {uiText("Printer")}
                  </h3>
                  <div className="space-y-0.5">
                    <button
                      type="button"
                      onClick={() => {
                        onPrinterChange(null);
                      }}
                      className={`w-full flex items-center px-2 py-1.5 text-sm rounded font-medium group transition-colors ${
                        selectedPrinterId === null && selectedPrinterPresence === null
                          ? "text-accent-foreground bg-accent"
                          : "text-foreground hover:bg-muted"
                      }`}
                    >
                      <svg
                        className="h-4 w-4 mr-2 text-primary"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                      >
                        <path
                          d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth="2"
                        />
                      </svg>
                      {uiText("Any location")}
                    </button>
                    <div className="space-y-0.5">
                      <button
                        type="button"
                        onClick={() => {
                          onPrinterPresenceChange("any");
                          setPrinterExpanded(!printerExpanded);
                        }}
                        className={`w-full flex items-center px-2 py-1.5 text-sm rounded font-medium group transition-colors ${
                          selectedPrinterPresence === "any"
                            ? "text-accent-foreground bg-accent"
                            : "text-foreground hover:bg-muted"
                        }`}
                      >
                        <ChevronRight
                          className={`h-4 w-4 mr-1 text-muted-foreground transition-transform ${printerExpanded ? "rotate-90" : ""}`}
                        />
                        <svg
                          className="h-4 w-4 mr-2 text-primary"
                          fill="none"
                          stroke="currentColor"
                          viewBox="0 0 24 24"
                        >
                          <path
                            d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            strokeWidth="2"
                          />
                        </svg>
                        <span className="font-medium">{uiText("On a printer")}</span>
                      </button>
                      {printerExpanded && (
                        <div className="ml-4 border-l border-border">
                          {printers.length === 0 ? (
                            <p className="pl-4 py-1 text-2xs text-muted-foreground font-mono">
                              {uiText("No printers configured")}
                            </p>
                          ) : (
                            printers.map((printer) => (
                              <button
                                key={printer.id}
                                type="button"
                                onClick={() => {
                                  onPrinterChange(printer.id);
                                }}
                                className={`w-full flex items-center justify-between px-2 py-1.5 text-sm transition-colors rounded group pl-4 ${
                                  selectedPrinterId === printer.id
                                    ? "text-accent-foreground bg-accent"
                                    : "text-foreground hover:bg-muted"
                                }`}
                              >
                                <span className="flex items-center">
                                  <span
                                    className={`w-1.5 h-1.5 rounded-full ${statusColor(printer.status)} mr-2`}
                                  />
                                  {printer.name}
                                </span>
                                <span
                                  className={`text-3xs font-medium ${statusTextColor(printer.status)}`}
                                >
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
                        onPrinterPresenceChange("none");
                      }}
                      className={`w-full flex items-center px-2 py-1.5 text-sm rounded font-medium group transition-colors ${
                        selectedPrinterPresence === "none"
                          ? "text-accent-foreground bg-accent"
                          : "text-foreground hover:bg-muted"
                      }`}
                    >
                      <Folder className="h-4 w-4 mr-2 text-primary" />
                      {uiText("Vault only")}
                    </button>
                  </div>
                </section>
              )}

              {/* Tags */}
              {structuredFilters}

              {/* Tags */}
              {tags.length > 0 && (
                <section>
                  <h3 className="text-xs font-bold text-muted-foreground uppercase tracking-wider mb-2 pl-2">
                    {uiText("Tags")}
                  </h3>
                  <div className="relative mb-2">
                    <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3 w-3 text-muted-foreground" />
                    <input
                      type="text"
                      placeholder={uiText("Filter tags...")}
                      value={tagFilter}
                      onChange={(e) => {
                        setTagFilter(e.target.value);
                        setShowAllTags(false);
                      }}
                      className="w-full pl-7 pr-2 py-1.5 text-sm border border-border rounded bg-muted text-foreground font-mono placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring focus:border-primary transition-colors"
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
                    <p className="text-3xs text-muted-foreground font-mono px-1 py-2">
                      {uiText("No matching tags.")}
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
                            className={`flex items-center gap-1 px-2 py-1 rounded font-mono text-2xs tracking-wider border transition-colors ${
                              active
                                ? "border-primary bg-accent text-accent-foreground"
                                : "border-border text-muted-foreground hover:border-border hover:bg-muted"
                            }`}
                          >
                            {t.name}
                            <span className="opacity-60">
                              {t.model_count + (t.multipart_model_count ?? 0)}
                            </span>
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
                      className="mt-2 w-full text-center font-mono text-3xs text-muted-foreground hover:text-foreground transition-colors py-1"
                    >
                      {showAllTags
                        ? uiText("Show fewer")
                        : uiText("Show all {value1} tags", { value1: String(filteredTags.length) })}
                    </button>
                  )}
                </section>
              )}
            </div>
          )}
        </div>
      </DndContext>
    </Localized>
  );
}

export interface FilterSidebarProps {
  models?: OutlinerModelRead[];
  multipartModels?: MultipartModelListItem[];
  tags: TagRead[];
  printers: PrinterRead[];
  selectedCollection: string | null;
  selectedTags: string[];
  selectedPrinterId: number | null;
  selectedPrinterPresence: "any" | "none" | null;
  onCollectionChange: (path: string | null) => void;
  /** Hover/focus on a folder: the parent may warm that folder's data. */
  onCollectionIntent?: (path: string) => void;
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
  structuredFilters?: React.ReactNode;
  filtersOpen?: boolean;
  libraryView: LibraryViewMode;
  onLibraryViewChange: (view: LibraryViewMode) => void;
}

export function FilterSidebar(props: FilterSidebarProps) {
  useUiLocale();
  const [outlinerFilter, setOutlinerFilter] = useState("");
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    try {
      return parseInt(localStorage.getItem("ps-sidebar-width") ?? "220", 10);
    } catch {
      return 220;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem("ps-sidebar-width", String(sidebarWidth));
    } catch {}
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
    <Localized>
      <aside
        style={{ width: sidebarWidth }}
        className="bg-sidebar border-r border-border flex flex-col shrink-0 hidden md:flex relative"
      >
        <div className="p-2 border-b border-border bg-sidebar">
          <div className="relative">
            <span className="absolute inset-y-0 left-0 pl-2 flex items-center text-muted-foreground">
              <Search className="h-3.5 w-3.5" />
            </span>
            <input
              className="block w-full pl-7 pr-6 py-1.5 text-sm border border-border rounded bg-muted text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
              placeholder={uiText("Filter outliner...")}
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
          className="absolute right-0 top-0 bottom-0 w-1.5 cursor-col-resize hover:bg-primary/50 transition-colors z-50"
        />
      </aside>
    </Localized>
  );
}
