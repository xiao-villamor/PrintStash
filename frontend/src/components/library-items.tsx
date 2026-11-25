import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useMemo,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import { defaultRangeExtractor, useVirtualizer } from "@tanstack/react-virtual";
import { PANE_SETTLED_EVENT } from "@/components/use-collections-pane-motion";
import {
  libraryReadingSelector,
  type LibraryReadingRenderer,
} from "@/features/library/reading-position";
import { cn } from "@/lib/utils";
import { durationToken } from "@/lib/css-time";
import {
  clampInspectorWidth,
  INSPECTOR_WILL_MOVE_EVENT,
  InspectorWillMoveEvent,
  LIBRARY_INSPECTOR_MAX_WIDTH,
  LIBRARY_INSPECTOR_MIN_WIDTH,
} from "./use-inspector-motion";
import "./library-items.css";

const NATIVE_RENDER_LIMIT = 120;
/** Narrowest card a column may hold. Columns share the row's measured width. */
const CARD_WIDTH = 280;
const COMPACT_CARD_WIDTH = 280;
const SINGLE_COLUMN_MAX = 400;

/** One width scale for collection and Model rows, independent of item count. */
function preferredCardWidth(compact: boolean) {
  return compact ? COMPACT_CARD_WIDTH : CARD_WIDTH;
}

function fittingColumns(width: number, track: number, gap: number) {
  return Math.max(1, Math.floor((width + gap) / (track + gap)));
}

function fittedTrack(width: number, preferredWidth: number, gap: number) {
  const columns = fittingColumns(width, preferredWidth, gap);
  return columns === 1
    ? Math.min(width || preferredWidth, SINGLE_COLUMN_MAX)
    : Math.max(preferredWidth, Math.floor((width - gap * (columns - 1)) / columns));
}

interface PaneCardSnapshot {
  closing: boolean;
  columns: number;
  track: number;
  width: number;
  scrollTop: number;
  cards: Map<string, { bounds: DOMRect; row: number }>;
  viewport: DOMRect;
}

function cardIsVisible(bounds: DOMRect, viewport: DOMRect, right = viewport.right) {
  return (
    bounds.width > 0 &&
    bounds.height > 0 &&
    bounds.left < right &&
    bounds.right > viewport.left &&
    bounds.top < viewport.bottom &&
    bounds.bottom > viewport.top
  );
}

export interface LibraryInspectorDock {
  maxWidth: number;
  width: number;
  paddingOffset: number;
}

interface LibraryItemsProps<T> {
  items: readonly T[];
  itemKey: (item: T) => string;
  renderItem: (item: T, index: number) => ReactNode;
  scrollRef: RefObject<HTMLElement | null>;
  readingRenderers: RefObject<Map<string, LibraryReadingRenderer>>;
  readingId: string;
  layout: "grid" | "list";
  compact?: boolean;
  estimateSize?: number;
  /** Keep the closed grid's tracks while the docked inspector consumes space. */
  freezeInspectorTracks?: boolean;
  /** Keep collection strip tracks the same at both sidebar positions. */
  keepCollectionCardWidth?: boolean;
  /** Only the Model results measure the dock's width. */
  onInspectorDockChange?: (dock: LibraryInspectorDock | null) => void;
  preferredInspectorWidth?: number | null;
  /** A production-build comparison can disable the renderer without changing cards or data. */
  virtualize?: boolean;
  className?: string;
  /** Narrowest card for this list when its cards are wider than model cards. */
  minCardWidth?: number;
  /** Keep a collapsed collection strip to one row during the layout commit. */
  singleRow?: boolean;
  /** Reports how many cards fit in a row and the width of its tracks. */
  onColumnsChange?: (columns: number, rowWidth: number) => void;
}

/** Render loaded pages only. Pagination, order and selection remain owned by ModelBrowser. */
export function LibraryItems<T>({
  items,
  itemKey,
  renderItem,
  scrollRef,
  readingRenderers,
  readingId,
  layout,
  compact = false,
  estimateSize = layout === "list" ? 65 : 320,
  virtualize = import.meta.env.VITE_LIBRARY_VIRTUALIZATION !== "off",
  className,
  freezeInspectorTracks = false,
  keepCollectionCardWidth = false,
  onInspectorDockChange,
  preferredInspectorWidth = null,
  minCardWidth,
  singleRow = false,
  onColumnsChange,
}: LibraryItemsProps<T>) {
  "use no memo";
  const root = useRef<HTMLDivElement>(null);
  const [geometry, setGeometry] = useState({
    columns: 1,
    margin: 0,
    width: 0,
    height: 0,
    closedFrameWidth: 0,
    inspectorMaxWidth: 0,
    inspectorWidth: 0,
    inspectorCustomWidth: false,
    inspectorPaddingOffset: 0,
    closedTrack: 0,
    track: 0,
    preferredWidth: 0,
    gap: 0,
    docked: false,
  });
  const [pinned, setPinned] = useState<string | null>(null);
  const [revealed, setRevealed] = useState<string | null>(null);
  const focusedEntry = useRef<{ key: string; index: number } | null>(null);
  const focusRestore = useRef<{ key: string; index: number } | null>(null);
  const restore = useRef<{ key: string; offset: number } | null>(null);
  const readyGeometry = useRef<typeof geometry | null>(null);
  const paneCards = useRef<PaneCardSnapshot | null>(null);
  const cardAnimations = useRef<Animation[]>([]);
  const snapshotExpiry = useRef<number | null>(null);
  const paintedGrid = useRef<{
    columns: number;
    track: number;
    width: number;
    keys: readonly string[];
  }>({
    columns: 1,
    track: 0,
    width: 0,
    keys: [],
  });
  const gap = layout === "list" ? 0 : compact ? 8 : 16;
  const preferredWidth = minCardWidth ?? preferredCardWidth(compact);
  const track = geometry.track || preferredWidth;
  const columns = layout === "list" ? 1 : geometry.columns;
  // Apply the strip's limit using the same geometry as its grid tracks. The
  // parent updates the hidden count separately and may still have the old limit.
  const displayedItems = useMemo(
    () => (singleRow ? items.slice(0, columns) : items),
    [columns, items, singleRow],
  );
  // Production-card measurements favor native rows for short loaded snapshots.
  // Larger snapshots bound their DOM while retaining the same row/card identities.
  const virtual =
    virtualize && displayedItems.length > NATIVE_RENDER_LIMIT && "ResizeObserver" in globalThis;
  useLayoutEffect(() => {
    if (geometry.width > 0)
      onColumnsChange?.(
        columns,
        layout === "list" ? geometry.width : columns * track + (columns - 1) * gap,
      );
  }, [layout, columns, geometry.width, track, gap, onColumnsChange]);
  const keys = useMemo(() => displayedItems.map(itemKey), [displayedItems, itemKey]);
  const pinnedIndex = pinned === null ? -1 : keys.indexOf(pinned);
  const pinnedRow = pinnedIndex < 0 ? null : Math.floor(pinnedIndex / columns);
  const revealedIndex = revealed === null ? -1 : keys.indexOf(revealed);
  const revealedRow = revealedIndex < 0 ? null : Math.floor(revealedIndex / columns);
  const rows = Math.ceil(displayedItems.length / columns);
  // TanStack exposes mutable measurements; this component opts out of React Compiler above.
  // oxlint-disable-next-line react/incompatible-library -- Virtualizer is consumed only within this uncompiled renderer.
  const virtualizer = useVirtualizer({
    count: rows,
    enabled: virtual,
    initialRect: { width: geometry.width, height: geometry.height },
    initialOffset: () => scrollRef.current?.scrollTop ?? 0,
    getScrollElement: () => scrollRef.current,
    getItemKey: (row) => keys[row * columns]!,
    estimateSize: () => estimateSize,
    gap,
    scrollMargin: geometry.margin,
    overscan: 2,
    useAnimationFrameWithResizeObserver: true,
    rangeExtractor: useCallback(
      (range) => {
        const visible = defaultRangeExtractor(range);
        const retained = [pinnedRow, revealedRow].flatMap((row) =>
          row === null ? [] : [row, Math.max(0, row - 1), Math.min(rows - 1, row + 1)],
        );
        if (!retained.length) return visible;
        // Keep focus/drag alive, including the following row for native Tab traversal.
        return Array.from(new Set([...visible, ...retained])).sort((a, b) => a - b);
      },
      [pinnedRow, revealedRow, rows],
    ),
  });

  useLayoutEffect(() => {
    const node = root.current;
    const scroll = scrollRef.current;
    if (!node || !scroll) return;
    const measure = () => {
      const measuredWidth = node.clientWidth;
      // Collections carries the existing grid with the workspace. Keep its
      // tracks pinned until the shell finishes moving.
      if (measuredWidth === 0 || "paneMoving" in document.documentElement.dataset) return;
      // The scroll surface docks the inspector with right padding. Recover the
      // closed width, including when the Collections pane targets a new width.
      const style =
        onInspectorDockChange || freezeInspectorTracks ? getComputedStyle(scroll) : null;
      const dockWidth =
        freezeInspectorTracks && style ? Math.round(parseFloat(style.paddingRight)) : 0;
      const docked = dockWidth > 0;
      // A scrollbar can appear as the cards rewrap. The outer frame tells us
      // whether the closed layout itself changed or just the open grid did.
      const scrollbarWidth = scroll.offsetWidth - scroll.clientWidth;
      const collectionsPaneWidth = keepCollectionCardWidth
        ? (document.querySelector<HTMLElement>("[data-collections-pane]")?.offsetWidth ?? 0)
        : 0;
      const closedFrameWidth = measuredWidth + dockWidth + scrollbarWidth;
      const inset = style?.getPropertyValue("--library-inspector-inset");
      const gutter = style?.getPropertyValue("--library-inspector-gutter");
      const inspector =
        onInspectorDockChange && style && inset && gutter && node.parentElement
          ? {
              baseWidth: parseFloat(style.getPropertyValue("--workbench-sidebar-width")),
              reservedSpace:
                parseFloat(inset) +
                parseFloat(gutter) -
                parseFloat(getComputedStyle(node.parentElement).paddingRight),
            }
          : null;
      const margin =
        node.getBoundingClientRect().top - scroll.getBoundingClientRect().top + scroll.scrollTop;
      setGeometry((previous) => {
        const frozen =
          docked &&
          previous.closedFrameWidth === closedFrameWidth &&
          previous.preferredWidth === preferredWidth &&
          previous.gap === gap;
        const closedTrack =
          layout === "list"
            ? 0
            : frozen
              ? previous.closedTrack
              : fittedTrack(measuredWidth + dockWidth + collectionsPaneWidth, preferredWidth, gap);
        let width = measuredWidth;
        let inspectorMaxWidth = 0;
        let inspectorWidth = 0;
        let inspectorCustomWidth = false;
        let inspectorPaddingOffset = 0;
        if (inspector) {
          const { baseWidth, reservedSpace } = inspector;
          const maxWidth = Math.min(
            LIBRARY_INSPECTOR_MAX_WIDTH,
            Math.floor(closedFrameWidth - reservedSpace - (closedTrack || CARD_WIDTH)),
          );
          inspectorMaxWidth = maxWidth;
          const available = closedFrameWidth - baseWidth - reservedSpace;
          if (preferredInspectorWidth !== null && maxWidth >= LIBRARY_INSPECTOR_MIN_WIDTH) {
            inspectorWidth = clampInspectorWidth(preferredInspectorWidth, maxWidth);
            inspectorCustomWidth = true;
            inspectorPaddingOffset = reservedSpace - scrollbarWidth;
          } else if (layout === "grid" && available >= closedTrack) {
            const columns = fittingColumns(available, closedTrack, gap);
            const gridWidth = columns * closedTrack + (columns - 1) * gap;
            inspectorWidth = Math.round(closedFrameWidth - reservedSpace - gridWidth);
            inspectorPaddingOffset = reservedSpace - scrollbarWidth;
          } else {
            inspectorWidth = baseWidth;
            inspectorPaddingOffset = 12;
          }
          // Use the fitted dock and grid width from the same layout pass.
          const fitted = inspectorCustomWidth || (layout === "grid" && available >= closedTrack);
          if (docked && fitted) width = closedFrameWidth - reservedSpace - inspectorWidth;
        }
        const nextColumns =
          layout === "list"
            ? 1
            : fittingColumns(
                width,
                docked || keepCollectionCardWidth ? closedTrack : preferredWidth,
                gap,
              );
        const customTracks = layout === "grid" && docked && inspectorCustomWidth;
        const nextTrack = customTracks
          ? (width - (nextColumns - 1) * gap) / nextColumns
          : keepCollectionCardWidth
            ? Math.min(closedTrack, width)
            : closedTrack;
        if (
          previous.columns === nextColumns &&
          previous.track === nextTrack &&
          previous.closedTrack === closedTrack &&
          previous.inspectorMaxWidth === inspectorMaxWidth &&
          previous.inspectorCustomWidth === inspectorCustomWidth &&
          previous.closedFrameWidth === closedFrameWidth &&
          previous.inspectorWidth === inspectorWidth &&
          previous.inspectorPaddingOffset === inspectorPaddingOffset &&
          previous.preferredWidth === preferredWidth &&
          previous.gap === gap &&
          previous.docked === docked &&
          Math.abs(previous.margin - margin) < 1 &&
          previous.width === width &&
          previous.height === scroll.clientHeight
        )
          return previous;
        const resized =
          previous.width > 0 &&
          (previous.columns !== nextColumns ||
            previous.track !== nextTrack ||
            previous.width !== width ||
            previous.height !== scroll.clientHeight);
        const focused = document.activeElement;
        const focusedCard =
          focused instanceof HTMLElement && node.contains(focused)
            ? focused.closest<HTMLElement>("[data-library-key]")
            : null;
        // Opening hands focus to the inspector heading. A virtual row remount
        // must not take it back on the following frame.
        if (docked && !previous.docked) focusRestore.current = null;
        else if (resized && focusedCard?.dataset.libraryKey)
          focusRestore.current = {
            key: focusedCard.dataset.libraryKey,
            index: Array.from(focusedCard.querySelectorAll("button,a,input,[tabindex]")).indexOf(
              focused!,
            ),
          };
        // Several collection grids can share this scroller. Only the grid at
        // the reading edge can restore an anchor, and the top stays at zero.
        if (resized && !restore.current && scroll.scrollTop > 0) {
          const top = scroll.getBoundingClientRect().top;
          for (const card of node.querySelectorAll<HTMLElement>("[data-library-key]")) {
            const bounds = card.getBoundingClientRect();
            if (bounds.top > top || bounds.bottom <= top) continue;
            restore.current = { key: card.dataset.libraryKey!, offset: bounds.top - top };
            break;
          }
        }
        return {
          columns: nextColumns,
          margin,
          width,
          height: scroll.clientHeight,
          closedFrameWidth,
          inspectorMaxWidth,
          inspectorWidth,
          inspectorCustomWidth,
          inspectorPaddingOffset,
          closedTrack,
          track: nextTrack,
          preferredWidth,
          gap,
          docked,
        };
      });
    };
    measure();
    if (!("ResizeObserver" in globalThis)) return;
    let frame: number | null = null;
    const observer = new ResizeObserver(() => {
      if (frame !== null) cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => measure());
    });
    observer.observe(node);
    observer.observe(scroll);
    const movingInspector = (event: Event) => {
      if (
        !(event instanceof InspectorWillMoveEvent) ||
        "paneMoving" in document.documentElement.dataset
      )
        return;
      if (layout === "grid" && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        const viewport = scroll.getBoundingClientRect();
        const closing = event.direction === "closing";
        const drawer = closing
          ? document.querySelector("#model-drawer:not([hidden])")?.getBoundingClientRect()
          : undefined;
        const right = drawer?.width ? Math.min(viewport.right, drawer.left) : viewport.right;
        const { columns, track, width, keys } = paintedGrid.current;
        const rows = new Map(keys.map((key, index) => [key, Math.floor(index / columns)]));
        const cards: PaneCardSnapshot["cards"] = new Map();
        for (const card of node.querySelectorAll<HTMLElement>("[data-library-key]")) {
          const key = card.dataset.libraryKey!;
          const row = rows.get(key);
          const bounds = card.getBoundingClientRect();
          if (row !== undefined && cardIsVisible(bounds, viewport, right))
            cards.set(key, { bounds, row });
        }
        paneCards.current = {
          closing,
          columns,
          track,
          width,
          cards,
          viewport,
          scrollTop: scroll.scrollTop,
        };
        if (snapshotExpiry.current !== null) cancelAnimationFrame(snapshotExpiry.current);
        // The projection is committed before paint. A dock that leaves the
        // grid unchanged must not carry this snapshot into a later resize.
        snapshotExpiry.current = requestAnimationFrame(() => {
          snapshotExpiry.current = null;
          paneCards.current = null;
        });
      }
    };
    window.addEventListener(PANE_SETTLED_EVENT, measure);
    window.addEventListener(INSPECTOR_WILL_MOVE_EVENT, movingInspector);
    return () => {
      window.removeEventListener(PANE_SETTLED_EVENT, measure);
      window.removeEventListener(INSPECTOR_WILL_MOVE_EVENT, movingInspector);
      observer.disconnect();
      if (frame !== null) cancelAnimationFrame(frame);
    };
  }, [
    scrollRef,
    layout,
    gap,
    preferredWidth,
    freezeInspectorTracks,
    keepCollectionCardWidth,
    onInspectorDockChange,
    preferredInspectorWidth,
  ]);

  const { inspectorMaxWidth, inspectorWidth, inspectorPaddingOffset } = geometry;
  useLayoutEffect(() => {
    onInspectorDockChange?.(
      inspectorWidth
        ? {
            width: inspectorWidth,
            maxWidth: inspectorMaxWidth,
            paddingOffset: inspectorPaddingOffset,
          }
        : null,
    );
  }, [inspectorMaxWidth, inspectorWidth, inspectorPaddingOffset, onInspectorDockChange]);

  useLayoutEffect(() => {
    return () => onInspectorDockChange?.(null);
  }, [onInspectorDockChange]);

  // Crossing the native/virtual threshold remounts row wrappers. Retain the
  // focused control's identity before that commit, then restore it without scrolling.
  useLayoutEffect(() => {
    const focused = focusedEntry.current;
    if (!focused) return;
    root.current
      ?.querySelector<HTMLElement>(`[data-library-key="${CSS.escape(focused.key)}"]`)
      ?.querySelectorAll<HTMLElement>("button,a,input,[tabindex]")
      [focused.index]?.focus({ preventScroll: true });
  }, [virtual, columns]);

  useLayoutEffect(() => {
    virtualizer.measure();
    for (const row of root.current?.querySelectorAll<HTMLElement>("[data-index]") ?? [])
      virtualizer.measureElement(row);
  }, [columns, track, compact, layout, virtual, virtualizer]);

  useLayoutEffect(() => {
    paintedGrid.current = { columns, track, width: geometry.width, keys };
  }, [columns, track, geometry.width, keys]);

  useLayoutEffect(() => {
    const before = paneCards.current;
    const node = root.current;
    const scroll = scrollRef.current;
    if (!before || !node || !scroll) return;
    if (before.columns === columns && before.track === track && before.width === geometry.width)
      return;
    paneCards.current = null;
    for (const animation of cardAnimations.current) animation.cancel();
    cardAnimations.current = [];

    const viewport = scroll.getBoundingClientRect();
    const scrollTop = scroll.scrollTop;
    const visible = [...node.querySelectorAll<HTMLElement>("[data-library-key]")]
      .map((card) => ({ card, bounds: card.getBoundingClientRect() }))
      .filter(({ bounds }) => cardIsVisible(bounds, viewport));
    if (visible.length === 0) return;
    const duration = durationToken(node, "--duration-fast") * 1000;
    const easing = getComputedStyle(node).getPropertyValue("--ease-dock").trim();
    const timing = { duration, easing };
    const arrival = [
      { opacity: 0.4, transform: `translateY(${gap}px)` },
      { opacity: 1, transform: "none" },
    ];
    const fade = [{ opacity: 0.9 }, { opacity: 1 }];
    const rows = new Map(keys.map((key, index) => [key, Math.floor(index / columns)]));
    for (const { card, bounds } of visible) {
      const key = card.dataset.libraryKey!;
      const origin = before.cards.get(key);
      if (!origin) {
        // A close can reveal cards even when only the track width changes.
        if (before.closing || before.columns !== columns)
          cardAnimations.current.push(card.animate(before.closing ? arrival : fade, timing));
        continue;
      }
      const x = origin.bounds.left - before.viewport.left - (bounds.left - viewport.left);
      const y = origin.bounds.top + before.scrollTop - (bounds.top + scrollTop);
      const changedRow = origin.row !== rows.get(key);
      // A long move enters from the gap nearest its origin. That gives each
      // reflowing row movement without passing over other cards.
      if (changedRow || Math.abs(x) > before.track + gap || Math.abs(y) > gap) {
        const offset =
          changedRow || Math.abs(y) > gap
            ? `translateY(${Math.sign(y) * gap}px)`
            : `translateX(${Math.sign(x) * gap}px)`;
        cardAnimations.current.push(
          card.animate(
            before.closing
              ? [
                  { opacity: 0.65, transform: offset },
                  { opacity: 1, transform: "none" },
                ]
              : fade,
            timing,
          ),
        );
        continue;
      }
      const scaleX = origin.bounds.width / bounds.width;
      const scaleY = origin.bounds.height / bounds.height;
      if (
        Math.abs(x) < 0.5 &&
        Math.abs(y) < 0.5 &&
        Math.abs(scaleX - 1) < 0.002 &&
        Math.abs(scaleY - 1) < 0.002
      )
        continue;
      cardAnimations.current.push(
        card.animate(
          [
            {
              transformOrigin: "top left",
              transform: `translate(${x}px, ${y}px) scale(${scaleX}, ${scaleY})`,
            },
            { transformOrigin: "top left", transform: "none" },
          ],
          timing,
        ),
      );
    }
  }, [geometry.width, columns, track, gap, keys, scrollRef]);

  useEffect(
    () => () => {
      if (snapshotExpiry.current !== null) cancelAnimationFrame(snapshotExpiry.current);
      for (const animation of cardAnimations.current) animation.cancel();
    },
    [],
  );

  useLayoutEffect(() => {
    const saved = restore.current;
    if (!saved && !focusRestore.current) return;
    const index = saved ? keys.indexOf(saved.key) : -1;
    if (virtual && saved && index >= 0) {
      setRevealed(saved.key);
      virtualizer.scrollToIndex(Math.floor(index / columns), { align: "start" });
    }
    const frame = requestAnimationFrame(() => {
      if (saved) {
        const node = root.current?.querySelector<HTMLElement>(
          `[data-library-key="${CSS.escape(saved.key)}"]`,
        );
        const scroll = scrollRef.current;
        virtualizer.cancelScroll();
        if (node && scroll)
          scroll.scrollTop +=
            node.getBoundingClientRect().top - scroll.getBoundingClientRect().top - saved.offset;
      }
      const focused = focusRestore.current;
      if (focused) {
        root.current
          ?.querySelector<HTMLElement>(`[data-library-key="${CSS.escape(focused.key)}"]`)
          ?.querySelectorAll<HTMLElement>("button,a,input,[tabindex]")
          [focused.index]?.focus({ preventScroll: true });
        focusRestore.current = null;
      }
      restore.current = null;
    });
    return () => cancelAnimationFrame(frame);
  }, [
    columns,
    track,
    geometry.width,
    geometry.height,
    compact,
    layout,
    virtual,
    keys,
    virtualizer,
    scrollRef,
  ]);

  useLayoutEffect(() => {
    const renderer: LibraryReadingRenderer = {
      reveal(key) {
        const index = keys.indexOf(key);
        if (index < 0) return "missing";
        if (geometry.width === 0) return "pending";
        if (readyGeometry.current !== geometry) {
          readyGeometry.current = geometry;
          return "pending";
        }
        if (root.current?.querySelector(libraryReadingSelector(key))) {
          let measuring = false;
          if (virtual)
            for (const row of virtualizer.getVirtualItems()) {
              const node = root.current.querySelector<HTMLElement>(`[data-index="${row.index}"]`);
              if (node && Math.abs(row.size - node.getBoundingClientRect().height) > 1) {
                virtualizer.measureElement(node);
                measuring = true;
              }
            }
          // Reading-position owns the final pixel correction. Retire Virtual's
          // index alignment reconciliation before handing the mounted anchor back.
          if (!measuring) virtualizer.cancelScroll();
          return measuring ? "pending" : "mounted";
        }
        if (!virtual) return "missing";
        setRevealed(key);
        virtualizer.scrollToIndex(Math.floor(index / columns), { align: "start" });
        return "pending";
      },
    };
    const registry = readingRenderers.current;
    registry.set(readingId, renderer);
    return () => {
      registry.delete(readingId);
    };
  }, [readingRenderers, readingId, keys, columns, virtual, virtualizer, geometry]);

  // A card's content depends on its item. Column changes reuse the same elements.
  const contents = useMemo(
    () => displayedItems.map((item, index) => renderItem(item, index)),
    [displayedItems, renderItem],
  );
  const child = (item: T, index: number) => (
    <div
      key={itemKey(item)}
      data-library-key={itemKey(item)}
      data-entry-slot={Math.min(index, 4)}
      className="min-w-0"
    >
      {contents[index]}
    </div>
  );
  // Native rendering is one grid: a card keeps its element (and decoded
  // thumbnail) when the column count changes. Virtual rows are keyed per row.
  const renderedRows = virtual
    ? virtualizer.getVirtualItems()
    : rows > 0
      ? [{ key: "all", index: 0, start: 0 }]
      : [];
  return (
    <div
      ref={root}
      className={cn(
        "library-items",
        layout === "grid" && "library-items-grid",
        compact && "library-items-compact",
        className,
      )}
      data-library-renderer={virtual ? "virtual" : "native"}
      onFocusCapture={(event) => {
        const card = event.target.closest<HTMLElement>("[data-library-key]");
        const key = card?.dataset.libraryKey;
        focusedEntry.current =
          key && card
            ? {
                key,
                index: Array.from(card.querySelectorAll("button,a,input,[tabindex]")).indexOf(
                  event.target,
                ),
              }
            : null;
        setPinned(key ?? null);
      }}
      onBlurCapture={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) {
          focusedEntry.current = null;
          setPinned(null);
        }
      }}
      onDragStartCapture={(event) =>
        setPinned(
          event.target instanceof Element
            ? (event.target.closest<HTMLElement>("[data-library-key]")?.dataset.libraryKey ?? null)
            : null,
        )
      }
      onDragEndCapture={() => setPinned(null)}
      style={{
        height: virtual ? virtualizer.getTotalSize() : undefined,
        position: "relative",
        display: "block",
      }}
    >
      {renderedRows.map((row) => (
        <div
          key={row.key}
          ref={virtual ? virtualizer.measureElement : undefined}
          data-index={row.index}
          className={layout === "grid" ? "library-virtual-row" : undefined}
          style={{
            position: virtual ? "absolute" : undefined,
            top: 0,
            left: 0,
            width: "100%",
            transform: virtual ? `translateY(${row.start - geometry.margin}px)` : undefined,
            // Grid tracks never shrink: while the pane moves, a frozen row is
            // clipped at the edge instead of every card resizing per frame.
            gridTemplateColumns:
              layout === "list"
                ? `repeat(${columns}, minmax(0, 1fr))`
                : columns === 1
                  ? `minmax(0, ${track}px)`
                  : `repeat(${columns}, ${track}px)`,
            gap,
            marginBottom: virtual || row.index === rows - 1 ? undefined : gap,
          }}
        >
          {(virtual
            ? displayedItems.slice(row.index * columns, (row.index + 1) * columns)
            : displayedItems
          ).map((item, index) => child(item, (virtual ? row.index * columns : 0) + index))}
        </div>
      ))}
    </div>
  );
}
