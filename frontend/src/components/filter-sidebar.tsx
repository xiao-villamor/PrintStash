"use client";

import {
  EXPANDED_KEY,
  readExpandedPaths,
  restoredExpansions,
  useOutlinerRestoration,
} from "@/lib/use-outliner-restoration";

import { TreeCount } from "@/components/tree-count";
import { captureModelDrag, type ModelDrag } from "@/lib/model-dnd";
import { knownUiText } from "@/lib/locale";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import type { StartupOutcome } from "@/lib/library-startup-context";
import { useLibraryStartup } from "@/lib/library-startup-context";
import { useViewportAdmission } from "@/lib/use-viewport-admission";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "@/lib/navigation";
import { Link } from "@/lib/link";
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
} from "@/types/outliner";
import { Button, buttonVariants } from "@/components/ui/button";
import { Localized } from "@/components/ui/localized";
import { useI18n } from "@/lib/i18n";
import {
  Box,
  Boxes,
  ChevronRight,
  Folder,
  FolderOpen,
  FolderPlus,
  ChevronsUp,
  Printer,
  Search,
  Trash2,
  X,
} from "lucide-react";
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

import type { LibraryViewMode } from "@/features/library/url";
export type { LibraryViewMode } from "@/features/library/url";

const LIBRARY_VIEWS: LibraryViewMode[] = ["all", "multipart"];

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

const ALL_EXPANDED_KEY = "ps-filter-all-expanded";

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
  enabled,
}: {
  model: OutlinerModelRead;
  isDraggingThisModel: boolean;
  enabled: boolean;
}) {
  "use memo";
  useUiLocale();
  const router = useRouter();
  const { attributes, listeners, setNodeRef } = useDraggable({
    id: `model-${model.id}`,
    disabled: !enabled,
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
        role="treeitem"
        aria-level={(model.collection?.split("/").length ?? 0) + 1}
        tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === "Enter") router.push(`/models/${model.id}`);
        }}
        className={`flex items-center gap-2 rounded pl-7 pr-2 py-1 text-xs select-none hover:bg-muted transition-colors ${enabled ? "cursor-grab active:cursor-grabbing" : "cursor-action"} ${
          isDraggingThisModel ? "opacity-30 pointer-events-none" : "text-muted-foreground"
        }`}
        title={model.name}
        data-outliner-entry={`/models/${model.id}`}
      >
        <Box className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground/40" />
        <span className="truncate">{model.name}</span>
      </div>
    </Localized>
  );
}

function MultipartLeaf({ multipart }: { multipart: OutlinerModelRead }) {
  "use memo";
  const { t: uiText } = useI18n();
  const router = useRouter();

  return (
    <Localized>
      <div
        onDoubleClick={() => router.push(`/multipart-models/${multipart.id}`)}
        role="treeitem"
        aria-level={(multipart.collection?.split("/").length ?? 0) + 1}
        tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === "Enter") router.push(`/multipart-models/${multipart.id}`);
        }}
        className="flex cursor-action select-none items-center gap-2 rounded pl-7 pr-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted"
        title={uiText("{value1} · Multipart set", { value1: String(multipart.name) })}
        data-outliner-entry={`/multipart-models/${multipart.id}`}
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
  dragEnabled,
}: {
  entries: OutlinerEntry[];
  dragging: DragPayload | null;
  dragEnabled: boolean;
}) {
  "use memo";
  return entries.map((entry) =>
    entry.kind === "model" ? (
      <DraggableModelLeaf
        key={`model-${entry.id}`}
        model={entry}
        enabled={dragEnabled}
        isDraggingThisModel={dragging?.type === "model" && dragging.model.id === entry.id}
      />
    ) : (
      <MultipartLeaf key={`multipart-${entry.id}`} multipart={entry} />
    ),
  );
}

interface TreeContext {
  dragEnabled: boolean;
  selected: string | null;
  onSelect: (path: string | null) => void;
  onIntent?: (path: string | null) => void;
  expanded: Set<string>;
  toggle: (path: string) => void;
  params: OutlinerParams;
  restoring: boolean;
  report: (key: string, status: StartupOutcome | null) => void;
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
  if (!query.hasNextPage) return null;
  return (
    <Button
      variant="ghost"
      size="sm"
      aria-disabled={query.isFetchingNextPage}
      onClick={() => {
        if (query.isFetchingNextPage) return;
        void query.fetchNextPage();
      }}
    >
      {label}
    </Button>
  );
}

function useTreeReadiness(ctx: TreeContext, key: string, status: StartupOutcome) {
  const report = ctx.report;
  useEffect(() => {
    report(key, status);
    return () => report(key, null);
  }, [report, key, status]);
}

function EntryLevel({ collectionId, ctx }: { collectionId: number | null; ctx: TreeContext }) {
  "use memo";
  const { t: uiText } = useI18n();
  const query = useOutlinerEntries(
    { ...ctx.params, collection_id: collectionId ?? undefined },
    !ctx.restoring,
  );
  useTreeReadiness(
    ctx,
    `entries:${collectionId}`,
    query.isError ? "failed" : query.isPending ? "pending" : "ready",
  );
  const entries = query.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <>
      <OutlinerLeaves entries={entries} dragging={ctx.dragging} dragEnabled={ctx.dragEnabled} />
      <PageControls
        query={query}
        initial={query.data === undefined}
        label={uiText("Show more models")}
      />
    </>
  );
}

function CollectionLevel({ parentId, ctx }: { parentId: number | null; ctx: TreeContext }) {
  "use memo";
  const { t: uiText } = useI18n();
  const query = useOutlinerCollections(
    {
      ...ctx.params,
      parent_id: parentId ?? undefined,
    },
    !ctx.restoring,
  );
  const nodes = new Map<number, OutlinerCollection>();
  for (const page of query.data?.pages ?? []) {
    for (const node of page.items) nodes.set(node.id, node);
    if (page.revealed) nodes.set(page.revealed.id, page.revealed);
  }
  // Selection is independent of downloaded sibling pages. Only ask to reveal
  // a location when it is absent from the level the user already has open.
  const selectedId = revealId(parentId, ctx.revealNodes);
  const needsReveal = selectedId !== undefined && !nodes.has(selectedId);
  const reveal = useOutlinerCollections(
    { ...ctx.params, parent_id: parentId ?? undefined, reveal_id: selectedId },
    query.isSuccess && needsReveal,
  );
  useTreeReadiness(
    ctx,
    `collections:${parentId}`,
    query.isError || (needsReveal && reveal.isError)
      ? "failed"
      : query.isPending || (needsReveal && reveal.isPending)
        ? "pending"
        : "ready",
  );
  if (needsReveal) {
    for (const page of reveal.data?.pages ?? []) {
      if (page.revealed) nodes.set(page.revealed.id, page.revealed);
    }
  }
  return (
    <>
      {[...nodes.values()]
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((node) => (
          <CollectionTreeRow key={node.id} node={node} ctx={ctx} stale={query.isPlaceholderData} />
        ))}
      <PageControls
        query={query}
        initial={query.data === undefined}
        label={uiText("Show more folders")}
      />
      {needsReveal && reveal.isError && (
        <PageControls
          query={{ ...reveal, hasNextPage: false }}
          initial={false}
          label={uiText("Show more folders")}
        />
      )}
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
  useTreeReadiness(ctx, "search", query.isError ? "failed" : query.isPending ? "pending" : "ready");
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

function CollectionTreeRow({
  node,
  ctx,
  stale,
}: {
  node: OutlinerCollection;
  ctx: TreeContext;
  stale: boolean;
}) {
  "use memo";
  const { t: uiText } = useI18n();
  const [confirming, setConfirming] = useState(false);
  const { selected, onSelect, onIntent, expanded, toggle, dragging, onDelete } = ctx;

  const {
    attributes,
    listeners,
    setNodeRef: setDragRef,
    isDragging,
  } = useDraggable({
    id: `collection-drag-${node.id}`,
    disabled: !ctx.dragEnabled,
    data: { type: "collection", collection: node } satisfies DragPayload,
  });

  const { setNodeRef: setDropRef, isOver } = useDroppable({
    id: `collection-drop-${node.id}`,
    disabled: !ctx.dragEnabled,
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
        data-tree-node={node.path}
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
                  <span>{uiText("counts.models", { count: node.model_count })}</span>
                )}
              </p>
            )}
            <p className="text-3xs text-muted-foreground mb-1.5 leading-snug">
              {hasContent
                ? uiText(
                    "Folders cannot be restored. Their models can be restored from Trash to All Models.",
                  )
                : uiText("This folder cannot be restored.")}
            </p>
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
            className={`collection-tree-row group/row relative flex min-w-32 items-center gap-1 rounded px-2 py-1 transition-colors ${
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
                data-tree-toggle
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
              onPointerLeave={() => onIntent?.(null)}
              onBlur={() => onIntent?.(null)}
              onClick={() => onSelect(node.path)}
              className="flex flex-1 min-w-0 items-center gap-1.5 text-left text-sm font-medium truncate"
              title={node.name}
              data-outliner-collection={node.path}
              {...attributes}
              role="treeitem"
              aria-selected={isSelected}
              aria-expanded={hasNestedItems ? isOpen : undefined}
              aria-level={node.path.split("/").length}
            >
              {isOpen || isSelected ? (
                <FolderOpen className="h-3.5 w-3.5 flex-shrink-0 text-primary" />
              ) : (
                <Folder className="h-3.5 w-3.5 flex-shrink-0" />
              )}
              <span className="truncate">{node.name}</span>
            </button>
            <span className="relative flex-shrink-0">
              <span
                className={`absolute right-full top-1/2 -translate-y-1/2 mr-1 flex items-center rounded invisible group-hover/row:visible group-focus-within/row:visible ${isSelected ? "bg-accent" : "bg-sidebar"}`}
              >
                {ctx.dragEnabled && (
                  <span
                    {...listeners}
                    onPointerDown={(e) => e.stopPropagation()}
                    className="p-0.5 text-muted-foreground/30 hover:text-muted-foreground cursor-grab active:cursor-grabbing flex-shrink-0"
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
                )}
                {onDelete && (
                  <button
                    type="button"
                    onPointerDown={(e) => e.stopPropagation()}
                    onClick={(e) => {
                      e.stopPropagation();
                      setConfirming(true);
                    }}
                    className="p-0.5 text-muted-foreground/30 hover:text-red-500 flex-shrink-0 rounded transition-colors"
                    title={uiText("Delete collection")}
                  >
                    <Trash2 className="h-2.5 w-2.5" />
                  </button>
                )}
              </span>
              <TreeCount value={node.subtree_entry_count} stale={stale} />
            </span>
          </div>
        )}
        {isOpen && hasNestedItems && !confirming && (
          <div className="ml-1 border-l border-border pl-1 min-w-0">
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
  hasCollections,
  ctx,
}: {
  selected: boolean;
  onClick: () => void;
  isExpanded: boolean;
  onToggleExpand: () => void;
  count: number;
  hasCollections: boolean;
  ctx: TreeContext;
}) {
  useUiLocale();
  const { setNodeRef, isOver } = useDroppable({
    id: "collection-root",
    disabled: !ctx.dragEnabled,
    data: { collectionPath: null, collectionId: null } satisfies CollectionDropData,
  });
  return (
    <>
      <div
        ref={setNodeRef}
        className={`flex items-center rounded ${isOver || selected ? "bg-accent text-accent-foreground" : "text-foreground"}`}
      >
        {(count > 0 || hasCollections) && (
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
  selectedCollectionId = null,
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
  readinessEnabled = true,
  libraryView,
  onLibraryViewChange,
  collectionsOnly = false,
  navigationOnly = false,
  collapseVersion = 0,
}: FilterSidebarProps) {
  "use memo";
  const { t, locale } = useI18n();
  const startup = useLibraryStartup();
  const settleStartup = startup.settle;
  const requestSecondary = startup.request;
  const { ref: filtersRef, admitted: filtersVisible } = useViewportAdmission();
  const contentRef = useRef<HTMLDivElement>(null);
  const filtersRegionRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const content = contentRef.current;
    if (!content) return;
    let focused: HTMLElement | null = null;
    function updateStops() {
      if (!content) return;
      const rows = [
        ...content.querySelectorAll<HTMLElement>(
          "[data-outliner-collection], [data-outliner-entry]",
        ),
      ];
      const active = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      const current =
        rows.find((row) => row === active) ??
        (focused?.isConnected ? focused : null) ??
        rows.find((row) => row.getAttribute("aria-selected") === "true") ??
        rows[0];
      for (const row of rows) row.tabIndex = row === current ? 0 : -1;
    }
    function focus(event: FocusEvent) {
      if (event.target instanceof HTMLElement)
        focused = event.target.closest<HTMLElement>(
          "[data-outliner-collection], [data-outliner-entry]",
        );
      updateStops();
    }
    updateStops();
    const observer = new MutationObserver(updateStops);
    observer.observe(content, { childList: true, subtree: true });
    content.addEventListener("focusin", focus);
    return () => {
      observer.disconnect();
      content.removeEventListener("focusin", focus);
    };
  }, [loading]);
  useEffect(() => {
    // Canonical URL replacement creates a new startup generation while an
    // already focused filter remains the reader's explicit priority.
    if (
      readinessEnabled &&
      filtersOpen &&
      filtersRegionRef.current?.contains(document.activeElement)
    )
      requestSecondary("filters");
  }, [readinessEnabled, filtersOpen, requestSecondary]);
  useEffect(() => {
    if (!readinessEnabled) return;
    settleStartup("tree", "pending");
    return () => settleStartup("tree", "idle");
  }, [settleStartup, readinessEnabled]);
  const outlinerQ = (outlinerFilter ?? "").trim();
  const [searchText, setSearchText] = useState(outlinerQ);
  useEffect(() => {
    const timer = setTimeout(() => setSearchText(outlinerQ), 250);
    return () => clearTimeout(timer);
  }, [outlinerQ]);
  const lookup = useCollectionLookup(selectedCollection, selectedCollectionId);
  const revealNodes =
    lookup.data?.collection.path === selectedCollection
      ? [...lookup.data.ancestors, lookup.data.collection]
      : [];
  const params: OutlinerParams = { ...outlinerFilters, view: libraryView };
  const [expanded, setExpanded] = useState<Set<string>>(() => {
    // A first visit starts at the top level: the tree loads a level only when
    // it is opened, and opening a large library whole is what #295 was.
    return restoredExpansions(
      readExpandedPaths(),
      selectedCollection ? ancestorPaths(selectedCollection) : [],
    );
  });
  const [allModelsExpanded, setAllModelsExpanded] = useState(readAllModelsExpanded);
  const levelsVisible = collectionsOnly || allModelsExpanded;
  const [collapsedAt, setCollapsedAt] = useState(collapseVersion);
  if (collapsedAt !== collapseVersion) {
    setCollapsedAt(collapseVersion);
    setExpanded(new Set());
    setAllModelsExpanded(false);
  }
  const {
    restore,
    enabled: restoreEnabled,
    restoring,
  } = useOutlinerRestoration(
    params,
    selectedCollection,
    outlinerQ === "" && levelsVisible,
    expanded,
  );
  const roots = useOutlinerCollections(params, outlinerQ === "" && !restoring);
  const [levels, setLevels] = useState<ReadonlyMap<string, StartupOutcome>>(() => new Map());
  const treeLayoutReady =
    roots.data !== undefined &&
    !restoring &&
    (!levelsVisible ||
      (levels.size > 0 && [...levels.values()].every((state) => state === "ready")));
  const attachFiltersRegion = useCallback(
    (node: HTMLDivElement | null) => {
      filtersRegionRef.current = node;
      const detach = treeLayoutReady ? filtersRef(node) : undefined;
      return () => {
        detach?.();
        filtersRegionRef.current = null;
      };
    },
    [treeLayoutReady, filtersRef],
  );

  useEffect(() => {
    // A short loading placeholder can temporarily expose controls that the
    // restored tree will push below the viewport. Observe the settled layout.
    if (readinessEnabled && filtersOpen && treeLayoutReady && filtersVisible)
      requestSecondary("filters");
  }, [readinessEnabled, filtersOpen, treeLayoutReady, filtersVisible, requestSecondary]);
  const report = useCallback((key: string, status: StartupOutcome | null) => {
    setLevels((current) => {
      if (status === null ? !current.has(key) : current.get(key) === status) return current;
      const next = new Map(current);
      if (status === null) next.delete(key);
      else next.set(key, status);
      return next;
    });
  }, []);
  useEffect(() => {
    if (!readinessEnabled) return;
    const states = [...levels.values()];
    if (
      roots.isError ||
      (restoreEnabled && restore.isError) ||
      (levelsVisible && states.includes("failed"))
    )
      settleStartup("tree", "failed");
    else if (treeLayoutReady) {
      let frame = 0;
      const painted = () => {
        const drawer = contentRef.current?.closest('[role="dialog"]');
        // Restored rows in a sliding drawer are not yet usable. Observe the
        // primitive's real transition rather than duplicating its duration.
        if (
          drawer?.getAnimations &&
          (drawer.getAttribute("data-state") !== "open" ||
            drawer.getAnimations().some((animation) => animation.playState === "running"))
        ) {
          frame = requestAnimationFrame(painted);
          return;
        }
        settleStartup("tree", "ready");
      };
      frame = requestAnimationFrame(painted);
      return () => cancelAnimationFrame(frame);
    } else settleStartup("tree", "pending");
  }, [
    readinessEnabled,
    levelsVisible,
    treeLayoutReady,
    roots.isError,
    restoreEnabled,
    restore.isError,
    levels,
    settleStartup,
  ]);
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

  const modelDrag = useRef<ModelDrag | null>(null);
  function handleDragStart(event: DragStartEvent) {
    const payload = activeDragPayload(event);
    modelDrag.current = payload?.type === "model" ? captureModelDrag(payload.model) : null;
    setDragging(payload);
  }

  function handleDragEnd(event: DragEndEvent) {
    setDragging(null);
    const payload = activeDragPayload(event);
    const source = modelDrag.current;
    modelDrag.current = null;
    const target = collectionDropTarget(event);
    if (!payload || !target) return;

    const targetCollectionPath = target.collectionPath;
    const targetCollectionId = target.collectionId;

    if (payload.type === "model") {
      if (!source || targetCollectionPath === source.collection) return;
      onMoveModel?.(source, targetCollectionPath);
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
    dragEnabled: !navigationOnly,
    selected: selectedCollection,
    onSelect: onCollectionChange,
    onIntent: onCollectionIntent,
    expanded,
    toggle: toggleExpanded,
    params,
    restoring,
    report,
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
    knownUiText(status.charAt(0).toUpperCase() + status.slice(1), locale);

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
        sensors={navigationOnly ? [] : sensors}
        collisionDetection={pointerWithin}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
        onDragCancel={() => {
          modelDrag.current = null;
          setDragging(null);
        }}
      >
        <div
          ref={contentRef}
          className={`min-h-0 flex-1 overflow-auto space-y-6 ${collectionsOnly ? "px-1.5 pb-3" : "py-2 px-2"}`}
          onKeyDown={(event) => {
            if (
              event.defaultPrevented ||
              event.nativeEvent.isComposing ||
              event.ctrlKey ||
              event.metaKey ||
              event.altKey
            )
              return;
            const target = event.target instanceof HTMLElement ? event.target : null;
            const row = target?.closest<HTMLElement>(
              "[data-outliner-collection], [data-outliner-entry]",
            );
            if (!row || !contentRef.current) return;
            const rows = [
              ...contentRef.current.querySelectorAll<HTMLElement>(
                "[data-outliner-collection], [data-outliner-entry]",
              ),
            ];
            const index = rows.indexOf(row);
            const node = row.closest<HTMLElement>("[data-tree-node]");
            const toggle = node?.querySelector<HTMLButtonElement>("[data-tree-toggle]");
            let next: HTMLElement | undefined | null;
            if (event.key === "ArrowDown") next = rows[Math.min(index + 1, rows.length - 1)];
            else if (event.key === "ArrowUp") next = rows[Math.max(index - 1, 0)];
            else if (event.key === "Home") next = rows[0];
            else if (event.key === "End") next = rows.at(-1);
            else if (event.key === "ArrowRight") {
              if (!row.dataset.outlinerCollection) return;
              if (toggle?.getAttribute("aria-expanded") === "false") toggle.click();
              else if (node?.contains(rows[index + 1] ?? null)) next = rows[index + 1];
            } else if (event.key === "ArrowLeft") {
              if (!row.dataset.outlinerCollection)
                next = node?.querySelector<HTMLElement>("[data-outliner-collection]");
              else if (toggle?.getAttribute("aria-expanded") === "true") toggle.click();
              else
                next = node?.parentElement
                  ?.closest("[data-tree-node]")
                  ?.querySelector<HTMLElement>("[data-outliner-collection]");
            } else if (
              event.key === "F2" &&
              !navigationOnly &&
              row.dataset.outlinerCollection === selectedCollection
            ) {
              window.dispatchEvent(
                new CustomEvent("printstash:rename-collection", { detail: selectedCollection }),
              );
            } else return;
            event.preventDefault();
            event.stopPropagation();
            next?.focus();
          }}
        >
          {!collectionsOnly && (
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
                    {view === "all" ? t("libraryView.all") : t("libraryView.multipart")}
                  </button>
                ))}
              </div>
            </section>
          )}

          {/* Collections */}
          <section>
            {!collectionsOnly && (
              <div className="flex items-center justify-between mb-2 pl-2 pr-1">
                <h3 className="text-xs font-bold text-muted-foreground uppercase tracking-wider">
                  {t("Collections")}
                </h3>
                <button
                  onClick={onCreateCollection}
                  className="p-0.5 text-muted-foreground hover:text-foreground hover:bg-muted rounded transition-colors"
                  title={t("Create Collection")}
                >
                  <svg
                    className="w-3.5 h-3.5"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      d="M12 4v16m8-8H4"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth="2.5"
                    />
                  </svg>
                </button>
              </div>
            )}
            <div className="overflow-x-auto -mx-3 px-3">
              <div
                className="min-w-0 space-y-0.5 pr-2"
                role={outlinerQ === "" ? "tree" : undefined}
                aria-label={uiText("Collections")}
              >
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
                    {restoreEnabled && restore.isError && (
                      <div role="status" className="py-1 text-xs text-muted-foreground">
                        {t("Could not load this list.")}
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={restore.isFetching}
                          onClick={() => void restore.refetch()}
                        >
                          {t("Retry")}
                        </Button>
                      </div>
                    )}
                    {!collectionsOnly && (
                      <DroppableAllModels
                        selected={selectedCollection === null}
                        onClick={() => onCollectionChange(null)}
                        isExpanded={allModelsExpanded}
                        onToggleExpand={() => setAllModelsExpanded((v) => !v)}
                        count={roots.data?.pages[0]?.parent_direct_entry_count ?? 0}
                        hasCollections={
                          roots.data?.pages.some((page) => page.items.length > 0) ?? false
                        }
                        ctx={treeContext}
                      />
                    )}
                    {!levelsVisible && roots.isError && (
                      <PageControls
                        query={roots}
                        initial={roots.data === undefined}
                        label={t("Show more folders")}
                      />
                    )}
                    {levelsVisible && (
                      <div className="ml-1 border-l border-border pl-1 min-w-0">
                        <CollectionLevel parentId={null} ctx={treeContext} />
                        {collectionsOnly &&
                          (roots.data?.pages[0]?.parent_direct_entry_count ?? 0) > 0 && (
                            <EntryLevel collectionId={null} ctx={treeContext} />
                          )}
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
          </section>

          {filtersOpen && (
            <div
              ref={attachFiltersRegion}
              onFocusCapture={() => requestSecondary("filters")}
              onPointerDownCapture={() => requestSecondary("filters")}
              className="space-y-6"
              aria-label={t("Filters")}
              role="region"
            >
              {/* Printer */}
              {canViewPrinters && (
                <section>
                  <h3 className="text-xs font-bold text-muted-foreground uppercase tracking-wider mb-2 pl-2">
                    {t("Printer")}
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
                      {t("Any location")}
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
                        <span className="font-medium">{t("On a printer")}</span>
                      </button>
                      {printerExpanded && (
                        <div className="ml-4 border-l border-border">
                          {printers.length === 0 ? (
                            <p className="pl-4 py-1 text-2xs text-muted-foreground font-mono">
                              {t("No printers configured")}
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
                      {t("Vault only")}
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
                    {t("Tags")}
                  </h3>
                  <div className="relative mb-2">
                    <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3 w-3 text-muted-foreground" />
                    <input
                      type="text"
                      placeholder={t("Filter tags...")}
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
                      {t("No matching tags.")}
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
                        ? t("Show fewer")
                        : t("Show all {value1} tags", { value1: String(filteredTags.length) })}
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
  /** The global pane uses the reference's collections-only composition. */
  collectionsOnly?: boolean;
  /** Other screens may navigate the tree without borrowing a Library editing scope. */
  navigationOnly?: boolean;
  collapseVersion?: number;
  outlinerFilters: OutlinerFilters;
  onClearOutlinerFilter?: () => void;
  tags: TagRead[];
  printers: PrinterRead[];
  selectedCollection: string | null;
  selectedCollectionId?: number | null;
  selectedTags: string[];
  selectedPrinterId: number | null;
  selectedPrinterPresence: "any" | "none" | null;
  onCollectionChange: (path: string | null) => void;
  /** Hover/focus on a folder: the parent may warm that folder's data. */
  onCollectionIntent?: (path: string | null) => void;
  onTagsChange: (tags: string[]) => void;
  onPrinterChange: (printerId: number | null) => void;
  onPrinterPresenceChange: (presence: "any" | "none" | null) => void;
  onCreateCollection: () => void;
  onMoveModel?: (source: ModelDrag, targetCollection: string | null) => void;
  onMoveCollection?: (collectionId: number, newParentId: number | null) => void;
  onDeleteCollection?: (id: number, recursive: boolean) => void;
  canViewPrinters?: boolean;
  canManageLibrary?: boolean;
  /** Library actions kept outside the independently scrolling outliner. */
  quickAccess?: React.ReactNode;
  loading?: boolean;
  outlinerFilter?: string;
  structuredFilters?: React.ReactNode;
  filtersOpen?: boolean;
  readinessEnabled?: boolean;
  libraryView: LibraryViewMode;
  onLibraryViewChange: (view: LibraryViewMode) => void;
}

export function FilterSidebar(props: FilterSidebarProps) {
  useUiLocale();
  const desktop = useMediaQuery("(min-width: 768px)");
  const [outlinerFilter, setOutlinerFilter] = useState("");
  const [collapseVersion, setCollapseVersion] = useState(0);
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    try {
      return parseInt(localStorage.getItem("ps-sidebar-width") ?? "321", 10);
    } catch {
      return 321;
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
        aria-label={props.collectionsOnly ? uiText("Collections") : undefined}
        style={{ width: sidebarWidth }}
        className={`${props.collectionsOnly ? "collections-sidebar " : ""}bg-sidebar border-r border-border flex min-h-0 flex-col shrink-0 hidden md:flex relative`}
      >
        {props.collectionsOnly && (
          <div className="flex h-12 shrink-0 items-center justify-between pl-4 pr-2">
            <h2 className="font-mono text-2xs font-medium uppercase tracking-[.06em] text-muted-foreground">
              {uiText("Collections")}
            </h2>
            <div className="flex items-center">
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                aria-label={uiText("New collection")}
                onClick={props.onCreateCollection}
              >
                <FolderPlus className="h-4 w-4" strokeWidth={1.6} />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                aria-label={uiText("Collapse")}
                onClick={() => setCollapseVersion((version) => version + 1)}
              >
                <ChevronsUp className="h-4 w-4" strokeWidth={1.6} />
              </Button>
            </div>
          </div>
        )}
        <div
          className={
            props.collectionsOnly ? "mx-2.5 mb-2" : "p-2 border-b border-border bg-sidebar"
          }
        >
          <div className="relative">
            <span className="absolute inset-y-0 left-0 pl-2 flex items-center text-muted-foreground">
              <Search className="h-3.5 w-3.5" />
            </span>
            <input
              data-tree-filter
              className={`block w-full pl-7 pr-6 border border-border rounded text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring ${props.collectionsOnly ? "h-[34px] bg-card text-xs" : "py-1.5 text-sm bg-muted"}`}
              placeholder={
                props.collectionsOnly ? uiText("Filter collections") : uiText("Filter outliner...")
              }
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
          collapseVersion={collapseVersion}
          outlinerFilter={outlinerFilter}
          onClearOutlinerFilter={() => setOutlinerFilter("")}
        />
        {!props.collectionsOnly && (
          <nav
            aria-label={uiText("nav.quickAccess")}
            className="shrink-0 border-t border-border bg-sidebar p-2"
          >
            <h3 className="mb-1 px-2 text-xs font-bold uppercase tracking-wider text-muted-foreground">
              {uiText("nav.quickAccess")}
            </h3>
            <div className="space-y-0.5">
              {props.quickAccess}
              {props.canViewPrinters && (
                <Link
                  href="/printers"
                  className={buttonVariants({
                    variant: "ghost",
                    size: "sm",
                    className: "w-full justify-start",
                  })}
                >
                  <Printer className="h-4 w-4 shrink-0" />
                  {uiText("nav.printers")}
                </Link>
              )}
              {props.canManageLibrary && (
                <Link
                  href="/settings?section=trash"
                  className={buttonVariants({
                    variant: "ghost",
                    size: "sm",
                    className: "w-full justify-start",
                  })}
                >
                  <Trash2 className="h-4 w-4 shrink-0" />
                  {uiText("settings.trash")}
                </Link>
              )}
            </div>
          </nav>
        )}
        {/* Resize handle */}
        <div
          onMouseDown={handleResizeStart}
          className="absolute right-0 top-0 bottom-0 w-1.5 cursor-col-resize hover:bg-primary/50 transition-colors z-50"
        />
      </aside>
    </Localized>
  );
}
