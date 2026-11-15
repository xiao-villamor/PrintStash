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
import {
  PANE_MOVING_EVENT,
  PANE_SETTLED_EVENT,
  PaneMovingEvent,
  type PaneMotion,
} from "@/components/use-collections-pane-motion";
import {
  libraryReadingSelector,
  type LibraryReadingRenderer,
} from "@/features/library/reading-position";
import { cn } from "@/lib/utils";
import "./library-items.css";

const NATIVE_RENDER_LIMIT = 120;
/**
 * Narrowest card a column may hold. Columns share the row's measured width.
 * When the Collections pane starts moving, the grid switches at once to the
 * tracks of the width it will rest at, and every card travels from where it
 * was to its new cell in lockstep with the pane (see `PaneMotion`), so the
 * reflow is one continuous motion instead of a pop when the pane rests.
 */
const CARD_WIDTH = 230;
const COMPACT_CARD_WIDTH = 280;
const SINGLE_COLUMN_MAX = 400;

/** One width scale for collection and Model rows, independent of item count. */
function preferredCardWidth(compact: boolean) {
  return compact ? COMPACT_CARD_WIDTH : CARD_WIDTH;
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
  /** A production-build comparison can disable the renderer without changing cards or data. */
  virtualize?: boolean;
  className?: string;
  /** Columns rendered beyond the measured ones, clipped at the row's edge (e.g.
   *  a collection the moving pane is about to uncover). Tracks keep their width. */
  extraColumns?: number;
  /** Narrowest card for this list when its cards are wider than model cards. */
  minCardWidth?: number;
  /** Reports how many cards fit one row, e.g. to show a single row of collections. */
  onColumnsChange?: (columns: number) => void;
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
  extraColumns = 0,
  minCardWidth,
  onColumnsChange,
}: LibraryItemsProps<T>) {
  "use no memo";
  const root = useRef<HTMLDivElement>(null);
  const [geometry, setGeometry] = useState({ columns: 1, margin: 0, width: 0, height: 0 });
  const [pinned, setPinned] = useState<string | null>(null);
  const [revealed, setRevealed] = useState<string | null>(null);
  const focusRestore = useRef<{ key: string; index: number } | null>(null);
  const restore = useRef<{ key: string; offset: number } | null>(null);
  const readyGeometry = useRef<typeof geometry | null>(null);
  // Card positions captured when the pane starts moving, consumed after the
  // grid commits its resting tracks. Flights are the running card motions.
  const flip = useRef<{ before: Map<string, DOMRect>; motion: PaneMotion } | null>(null);
  const flights = useRef<Animation[]>([]);
  const gap = layout === "list" ? 0 : compact ? 8 : 16;
  const preferredWidth = minCardWidth ?? preferredCardWidth(compact);
  const track =
    geometry.columns === 1
      ? `${Math.min(geometry.width || preferredWidth, SINGLE_COLUMN_MAX)}px`
      : `${Math.max(preferredWidth, Math.floor((geometry.width - gap * (geometry.columns - 1)) / geometry.columns))}px`;
  // Production-card measurements favor native rows for short loaded snapshots.
  // Larger snapshots bound their DOM while retaining the same row/card identities.
  const virtual =
    virtualize && items.length > NATIVE_RENDER_LIMIT && "ResizeObserver" in globalThis;
  const columns = layout === "list" ? 1 : geometry.columns + extraColumns;
  useEffect(() => {
    if (geometry.width > 0) onColumnsChange?.(layout === "list" ? 1 : geometry.columns);
  }, [layout, geometry.columns, geometry.width, onColumnsChange]);
  const keys = useMemo(() => items.map(itemKey), [items, itemKey]);
  const pinnedIndex = pinned === null ? -1 : keys.indexOf(pinned);
  const pinnedRow = pinnedIndex < 0 ? null : Math.floor(pinnedIndex / columns);
  const revealedIndex = revealed === null ? -1 : keys.indexOf(revealed);
  const revealedRow = revealedIndex < 0 ? null : Math.floor(revealedIndex / columns);
  const rows = Math.ceil(items.length / columns);
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
    const measure = (restingWidth?: number) => {
      const width = restingWidth ?? node.clientWidth;
      // Hidden surfaces have no layout yet; observe again when they become visible.
      if (node.clientWidth === 0) return;
      // While the Collections pane moves, the grid already holds the tracks of
      // its resting width (set when the motion started); never refit mid-motion.
      if (restingWidth === undefined && "paneMoving" in document.documentElement.dataset) return;
      const nextColumns =
        layout === "list"
          ? 1
          : Math.max(
              1,
              Math.floor((width + gap) / ((minCardWidth ?? preferredCardWidth(compact)) + gap)),
            );
      const margin =
        node.getBoundingClientRect().top - scroll.getBoundingClientRect().top + scroll.scrollTop;
      setGeometry((previous) => {
        if (
          previous.columns === nextColumns &&
          Math.abs(previous.margin - margin) < 1 &&
          // Tracks are derived from the measured width, which is never
          // measured while the pane moves (see above).
          (previous.width === width || layout === "list") &&
          previous.height === scroll.clientHeight
        )
          return previous;
        const resized =
          previous.width > 0 &&
          (previous.columns !== nextColumns ||
            previous.width !== width ||
            previous.height !== scroll.clientHeight);
        const focused = document.activeElement;
        const focusedCard =
          focused instanceof HTMLElement && node.contains(focused)
            ? focused.closest<HTMLElement>("[data-library-key]")
            : null;
        if (resized && focusedCard?.dataset.libraryKey)
          focusRestore.current = {
            key: focusedCard.dataset.libraryKey,
            index: Array.from(focusedCard.querySelectorAll("button,a,input,[tabindex]")).indexOf(
              focused!,
            ),
          };
        const bounds = scroll.getBoundingClientRect();
        const anchor = Array.from(node.querySelectorAll<HTMLElement>("[data-library-key]")).find(
          (candidate) => candidate.getBoundingClientRect().bottom > bounds.top,
        );
        if (resized && anchor?.dataset.libraryKey)
          restore.current = {
            key: anchor.dataset.libraryKey,
            offset: anchor.getBoundingClientRect().top - bounds.top,
          };
        return { columns: nextColumns, margin, width, height: scroll.clientHeight };
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
    const settled = () => measure();
    const moving = (event: Event) => {
      if (!(event instanceof PaneMovingEvent) || node.clientWidth === 0) return;
      // Visual positions, including any flight a reversed pane interrupts.
      const before = new Map<string, DOMRect>();
      for (const card of node.querySelectorAll<HTMLElement>("[data-library-key]"))
        before.set(card.dataset.libraryKey!, card.getBoundingClientRect());
      for (const flight of flights.current) flight.cancel();
      flights.current = [];
      flip.current = { before, motion: event.motion };
      measure(Math.max(1, Math.round(node.clientWidth + event.motion.contentDelta)));
    };
    window.addEventListener(PANE_SETTLED_EVENT, settled);
    window.addEventListener(PANE_MOVING_EVENT, moving);
    return () => {
      window.removeEventListener(PANE_SETTLED_EVENT, settled);
      window.removeEventListener(PANE_MOVING_EVENT, moving);
      observer.disconnect();
      if (frame !== null) cancelAnimationFrame(frame);
    };
  }, [scrollRef, layout, compact, gap, minCardWidth]);

  useLayoutEffect(() => {
    virtualizer.measure();
    for (const row of root.current?.querySelectorAll<HTMLElement>("[data-index]") ?? [])
      virtualizer.measureElement(row);
  }, [columns, compact, layout, virtual, virtualizer]);

  // Pane-driven reflow: keep the reading anchor, then fly every card from its
  // captured position to its new cell along the pane's own progress curve.
  useLayoutEffect(() => {
    const pending = flip.current;
    flip.current = null;
    const node = root.current;
    const scroll = scrollRef.current;
    if (!pending || !node) return;
    const saved = restore.current;
    restore.current = null;
    if (saved && scroll) {
      const anchor = node.querySelector<HTMLElement>(
        `[data-library-key="${CSS.escape(saved.key)}"]`,
      );
      if (anchor)
        scroll.scrollTop +=
          anchor.getBoundingClientRect().top - scroll.getBoundingClientRect().top - saved.offset;
    }
    const focused = focusRestore.current;
    focusRestore.current = null;
    if (focused)
      node
        .querySelector<HTMLElement>(`[data-library-key="${CSS.escape(focused.key)}"]`)
        ?.querySelectorAll<HTMLElement>("button,a,input,[tabindex]")
        [focused.index]?.focus({ preventScroll: true });
    const { progress, durationMs } = pending.motion;
    const last = progress.length - 1;
    if (last < 1 || durationMs <= 0) return;
    for (const card of node.querySelectorAll<HTMLElement>("[data-library-key]")) {
      const after = card.getBoundingClientRect();
      const before = pending.before.get(card.dataset.libraryKey!);
      let keyframes: Keyframe[];
      if (before) {
        const dx = before.left - after.left;
        const dy = before.top - after.top;
        if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) continue;
        // Transform only: it runs on the compositor. Animating width would
        // relayout every card on every frame of the pane motion.
        keyframes = progress.map((value, index) => ({
          offset: index / last,
          transform: `translate(${dx * (1 - value)}px, ${dy * (1 - value)}px)`,
        }));
      } else {
        // Newly rendered (e.g. a row the reflow brings into view) fades in.
        keyframes = progress.map((value, index) => ({ offset: index / last, opacity: value }));
      }
      flights.current.push(card.animate(keyframes, { duration: durationMs, easing: "linear" }));
    }
  }, [geometry, scrollRef]);
  useEffect(
    () => () => {
      for (const flight of flights.current) flight.cancel();
    },
    [],
  );

  useLayoutEffect(() => {
    const saved = restore.current;
    if (!saved) return;
    const index = keys.indexOf(saved.key);
    if (index < 0) {
      restore.current = null;
      return;
    }
    if (virtual) {
      setRevealed(saved.key);
      virtualizer.scrollToIndex(Math.floor(index / columns), { align: "start" });
    }
    const frame = requestAnimationFrame(() => {
      const node = root.current?.querySelector<HTMLElement>(
        `[data-library-key="${CSS.escape(saved.key)}"]`,
      );
      const scroll = scrollRef.current;
      virtualizer.cancelScroll();
      if (node && scroll)
        scroll.scrollTop +=
          node.getBoundingClientRect().top - scroll.getBoundingClientRect().top - saved.offset;
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
  }, [columns, compact, layout, virtual, keys, virtualizer, scrollRef]);

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

  // A card's content depends on its item, not on the grid: a column change
  // (e.g. the pane starting to move) reuses the same elements, so React skips
  // re-rendering every card and the motion's first frame is not delayed.
  const contents = useMemo(
    () => items.map((item, index) => renderItem(item, index)),
    [items, renderItem],
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
      onFocusCapture={(event) =>
        setPinned(
          event.target.closest<HTMLElement>("[data-library-key]")?.dataset.libraryKey ?? null,
        )
      }
      onBlurCapture={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setPinned(null);
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
                  ? `minmax(0, ${track})`
                  : `repeat(${columns}, ${track})`,
            gap,
            marginBottom: virtual || row.index === rows - 1 ? undefined : gap,
          }}
        >
          {(virtual ? items.slice(row.index * columns, (row.index + 1) * columns) : items).map(
            (item, index) => child(item, (virtual ? row.index * columns : 0) + index),
          )}
        </div>
      ))}
    </div>
  );
}
