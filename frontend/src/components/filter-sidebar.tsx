"use client";

import { knownUiText } from "@/lib/locale";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { useLibraryStartup } from "@/lib/library-startup-context";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "@/lib/navigation";
import { useMediaQuery } from "@/lib/use-media-query";
import { CollectionNodeRead, OutlinerModelRead, PrinterRead, TagRead } from "@/types";
import { Skeleton } from "@/components/ui/skeleton";
import { ancestorPaths } from "@/lib/collection-tree";
import {
  useCollectionLookup,
  useOutlinerCollections,
  useOutlinerEntries,
  useOutlinerSearch,
} from "@/lib/queries";
import type {
  OutlinerCollection,
  OutlinerEntry,
  OutlinerFilters,
  OutlinerParams,
  OutlinerView,
} from "@/types/outliner";
import { Button } from "@/components/ui/button";
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

export type LibraryViewMode = OutlinerView;

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
        role="button"
        tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === "Enter") router.push(`/models/${model.id}`);
        }}
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

function MultipartLeaf({ multipart }: { multipart: OutlinerModelRead }) {
  useUiLocale();
  const router = useRouter();

  return (
    <Localized>
      <div
        onDoubleClick={() => router.push(`/multipart-models/${multipart.id}`)}
        role="button"
        tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === "Enter") router.push(`/multipart-models/${multipart.id}`);
        }}
        className="flex cursor-default select-none items-center gap-2 rounded px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted"
        title={uiText("{value1} · Multipart set", { value1: String(multipart.name) })}
      >
        <Boxes className="h-3.5 w-3.5 flex-shrink-0 text-primary" />
        <span className="truncate">{multipart.name}</span>
      </div>
    </Localized>
  );
}

function OutlinerLeaves({
  entries,
  dragging,
}: {
  entries: OutlinerEntry[];
  dragging: DragPayload | null;
}) {
  return entries.map((entry) =>
    entry.kind === "model" ? (
      <DraggableModelLeaf
        key={`model-${entry.id}`}
        model={entry}
        isDraggingThisModel={dragging?.type === "model" && dragging.model.id === entry.id}
      />
    ) : (
      <MultipartLeaf key={`multipart-${entry.id}`} multipart={entry} />
    ),
  );
}

interface TreeContext {
  selected: string | null;
  onSelect: (path: string | null) => void;
  onIntent?: (path: string) => void;
  expanded: Set<string>;
  toggle: (path: string) => void;
  params: OutlinerParams;
  revealNodes: CollectionNodeRead[];
  dragging: DragPayload | null;
  onDelete?: (id: number, recursive: boolean) => void;
}

function revealId(parentId: number | null, nodes: CollectionNodeRead[]): number | undefined {
  return parentId === null ? nodes[0]?.id : nodes.find((node) => node.parent_id === parentId)?.id;
}

interface PageState {
  isPending: boolean;
  isError: boolean;
  isFetchNextPageError: boolean;
  isFetchingNextPage: boolean;
  hasNextPage: boolean;
  refetch: () => Promise<object>;
  fetchNextPage: () => Promise<object>;
}

function PageControls({
  query,
  label,
  initial,
}: {
  query: PageState;
  label: string;
  initial: boolean;
}) {
  useUiLocale();
  const [requested, setRequested] = useState(false);
  if (initial && query.isPending) return <Skeleton className="my-1 h-4 w-3/4" />;
  if (query.isError)
    return (
      <div role="status" className="py-1 text-xs text-muted-foreground">
        {uiText("Could not load this list.")}
        <Button
          variant="ghost"
          size="sm"
          disabled={query.isFetchingNextPage}
          onClick={() =>
            void (query.isFetchNextPageError ? query.fetchNextPage() : query.refetch())
          }
        >
          {uiText("Retry")}
        </Button>
      </div>
    );
  if (!query.hasNextPage && !requested) return null;
  return (
    <Button
      variant="ghost"
      size="sm"
      aria-disabled={query.isFetchingNextPage || !query.hasNextPage}
      onClick={() => {
        if (query.isFetchingNextPage || !query.hasNextPage) return;
        setRequested(true);
        void query.fetchNextPage();
      }}
    >
      {query.hasNextPage ? label : uiText("All items loaded")}
    </Button>
  );
}

function EntryLevel({ collectionId, ctx }: { collectionId: number | null; ctx: TreeContext }) {
  useUiLocale();
  const query = useOutlinerEntries({ ...ctx.params, collection_id: collectionId ?? undefined });
  const entries = query.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <>
      <OutlinerLeaves entries={entries} dragging={ctx.dragging} />
      <PageControls
        query={query}
        initial={query.data === undefined}
        label={uiText("Show more models")}
      />
    </>
  );
}

function CollectionLevel({ parentId, ctx }: { parentId: number | null; ctx: TreeContext }) {
  useUiLocale();
  const query = useOutlinerCollections({
    ...ctx.params,
    parent_id: parentId ?? undefined,
    reveal_id: revealId(parentId, ctx.revealNodes),
  });
  const nodes = new Map<number, OutlinerCollection>();
  for (const page of query.data?.pages ?? []) {
    for (const node of page.items) nodes.set(node.id, node);
    if (page.revealed) nodes.set(page.revealed.id, page.revealed);
  }
  return (
    <>
      {[...nodes.values()]
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((node) => (
          <CollectionTreeRow key={node.id} node={node} ctx={ctx} />
        ))}
      <PageControls
        query={query}
        initial={query.data === undefined}
        label={uiText("Show more folders")}
      />
    </>
  );
}

function SearchResults({
  text,
  ctx,
  clear,
}: {
  text: string;
  ctx: TreeContext;
  clear: () => void;
}) {
  useUiLocale();
  const router = useRouter();
  const query = useOutlinerSearch({ ...ctx.params, q: text });
  const matches = query.data?.pages.flatMap((page) => page.items) ?? [];
  function locate(path: string | null) {
    ctx.onSelect(path);
    clear();
  }
  return (
    <div aria-label={uiText("Search results")}>
      {matches.map((entry) => (
        <div key={`${entry.kind}-${entry.id}`} className="py-1 min-w-0">
          <Button
            variant="ghost"
            size="sm"
            className="max-w-full justify-start"
            onClick={() => {
              if (entry.kind === "collection") locate(entry.collection);
              else
                router.push(
                  entry.kind === "model" ? `/models/${entry.id}` : `/multipart-models/${entry.id}`,
                );
            }}
          >
            {entry.kind === "collection" ? (
              <Folder className="h-3 w-3 shrink-0" />
            ) : entry.kind === "model" ? (
              <Box className="h-3 w-3 shrink-0" />
            ) : (
              <Boxes className="h-3 w-3 shrink-0" />
            )}
            <span className="truncate">{entry.name}</span>
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="max-w-full text-muted-foreground"
            title={uiText("Open location")}
            onClick={() => locate(entry.collection)}
          >
            <span className="truncate">{entry.collection_label ?? uiText("All Models")}</span>
          </Button>
        </div>
      ))}
      {query.isSuccess && matches.length === 0 && (
        <p className="text-xs text-muted-foreground">{uiText("No results.")}</p>
      )}
      <PageControls
        query={query}
        initial={query.data === undefined}
        label={uiText("Show more results")}
      />
    </div>
  );
}

function CollectionTreeRow({ node, ctx }: { node: OutlinerCollection; ctx: TreeContext }) {
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

  const isOpen = expanded.has(node.path);
  const isSelected = selected === node.path;
  const hasNestedItems = node.visible_child_count > 0 || node.direct_entry_count > 0;
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
                aria-expanded={isOpen}
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
              {node.subtree_entry_count}
            </span>
          </div>
        )}
        {isOpen && hasNestedItems && !confirming && (
          <div className="ml-4 border-l border-border pl-3 min-w-0">
            {node.visible_child_count > 0 && <CollectionLevel parentId={node.id} ctx={ctx} />}
            {node.direct_entry_count > 0 && <EntryLevel collectionId={node.id} ctx={ctx} />}
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
  count,
  ctx,
}: {
  selected: boolean;
  onClick: () => void;
  isExpanded: boolean;
  onToggleExpand: () => void;
  count: number;
  ctx: TreeContext;
}) {
  useUiLocale();
  const { setNodeRef, isOver } = useDroppable({
    id: "collection-root",
    data: { collectionPath: null, collectionId: null } satisfies CollectionDropData,
  });
  return (
    <>
      <div
        ref={setNodeRef}
        className={`flex items-center rounded ${isOver || selected ? "bg-accent text-accent-foreground" : "text-foreground"}`}
      >
        {count > 0 && (
          <Button
            variant="ghost"
            size="sm"
            aria-label={isExpanded ? uiText("Collapse") : uiText("Expand")}
            aria-expanded={isExpanded}
            onClick={onToggleExpand}
          >
            <ChevronRight className={`h-3 w-3 ${isExpanded ? "rotate-90" : ""}`} />
          </Button>
        )}
        <Button variant="ghost" size="sm" aria-label={uiText("All Models")} onClick={onClick}>
          <FolderOpen className="h-4 w-4" />
          {uiText("All Models")}
        </Button>
      </div>
      {isExpanded && count > 0 && (
        <div className="ml-5 border-l border-border pl-4 min-w-0">
          <EntryLevel collectionId={null} ctx={ctx} />
        </div>
      )}
    </>
  );
}

export function FilterSidebarContent({
  outlinerFilters,
  onClearOutlinerFilter,
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
  const startup = useLibraryStartup();
  const settleStartup = startup.settle;
  const outlinerQ = (outlinerFilter ?? "").trim();
  const [searchText, setSearchText] = useState(outlinerQ);
  useEffect(() => {
    const timer = setTimeout(() => setSearchText(outlinerQ), 250);
    return () => clearTimeout(timer);
  }, [outlinerQ]);
  const lookup = useCollectionLookup(selectedCollection);
  const revealNodes =
    lookup.data?.collection.path === selectedCollection
      ? [...lookup.data.ancestors, lookup.data.collection]
      : [];
  const params: OutlinerParams = { ...outlinerFilters, view: libraryView };
  const roots = useOutlinerCollections(
    { ...params, reveal_id: revealId(null, revealNodes) },
    outlinerQ === "",
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
  useEffect(() => {
    if (roots.data !== undefined) settleStartup("tree", "ready");
    else if (roots.isError) settleStartup("tree", "failed");
  }, [roots.data, roots.isError, settleStartup]);
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
    params,
    revealNodes,
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
                {outlinerQ !== "" ? (
                  searchText !== outlinerQ ? (
                    <Skeleton className="h-5 w-full" />
                  ) : (
                    <SearchResults
                      text={searchText}
                      ctx={treeContext}
                      clear={() => onClearOutlinerFilter?.()}
                    />
                  )
                ) : (
                  <>
                    <DroppableAllModels
                      selected={selectedCollection === null}
                      onClick={() => onCollectionChange(null)}
                      isExpanded={allModelsExpanded}
                      onToggleExpand={() => setAllModelsExpanded((v) => !v)}
                      count={roots.data?.pages[0]?.parent_direct_entry_count ?? 0}
                      ctx={treeContext}
                    />
                    <div className="ml-5 border-l border-border pl-4 min-w-0">
                      <CollectionLevel parentId={null} ctx={treeContext} />
                    </div>
                  </>
                )}
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
  outlinerFilters: OutlinerFilters;
  onClearOutlinerFilter?: () => void;
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
  const desktop = useMediaQuery("(min-width: 768px)");
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

  if (!desktop) return null;

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
              onKeyDown={(e) => {
                if (e.key === "Escape") setOutlinerFilter("");
              }}
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
        <FilterSidebarContent
          {...props}
          outlinerFilter={outlinerFilter}
          onClearOutlinerFilter={() => setOutlinerFilter("")}
        />
        {/* Resize handle */}
        <div
          onMouseDown={handleResizeStart}
          className="absolute right-0 top-0 bottom-0 w-1.5 cursor-col-resize hover:bg-primary/50 transition-colors z-50"
        />
      </aside>
    </Localized>
  );
}
