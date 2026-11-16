import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { useLocation, useRouter } from "@tanstack/react-router";
import { getSessionVersion } from "@/lib/session-transport";
import {
  readLibraryPosition,
  saveLibraryPosition,
  type LibraryEntry,
  type LibraryLayout,
  type LibraryReadingPosition,
} from "./navigation-state";

/** Reading anchors also cover Collection buttons without changing navigation-link identities. */
export function libraryReadingSelector(key?: string): string {
  if (key === undefined) return "[data-library-entry], [data-library-anchor]";
  const escaped = CSS.escape(key);
  return `[data-library-entry="${escaped}"], [data-library-anchor="${escaped}"]`;
}
function readingKey(node: HTMLElement | undefined): string | undefined {
  return node?.dataset.libraryEntry ?? node?.dataset.libraryAnchor;
}

export interface LibraryReadingRenderer {
  reveal: (key: string) => "mounted" | "pending" | "missing";
}

interface ReadingPages {
  count: number;
  more: boolean;
  pending: boolean;
  failed: boolean;
  next: () => void;
}
export interface LibraryReadingPagination {
  ready: boolean;
  models: ReadingPages;
  folders: ReadingPages;
}
type Recovery = {
  entry: LibraryEntry | undefined;
  layout: LibraryLayout;
  current: boolean;
} & (
  | { status: "restoring"; saved: LibraryReadingPosition; failed: boolean }
  | { status: "refreshing"; saved: LibraryReadingPosition }
  | { status: "ready" | "reset" }
);

/** Own nested scroll restoration and its bounded page reconstruction, never remote data. */
export function useLibraryReadingPosition(
  entry: LibraryEntry | undefined,
  layout: LibraryLayout,
  mainRef: RefObject<HTMLElement | null>,
  listRef: RefObject<HTMLDivElement | null>,
  enabled: boolean,
  pagination: LibraryReadingPagination,
  renderers?: RefObject<Map<string, LibraryReadingRenderer>>,
) {
  const location = useLocation();
  const router = useRouter();
  const current =
    enabled && entry?.session === getSessionVersion() && entry.key === location.state.__TSR_key;
  const operation = useRef(0);
  const [renderAttempt, setRenderAttempt] = useState(0);
  const renderAttempts = useRef({ entry, count: 0 });
  const renderFrame = useRef<number | null>(null);
  const preparedDeparture = useRef<{
    entry: LibraryEntry;
    layout: LibraryLayout;
    position: LibraryReadingPosition;
  } | null>(null);
  const captured = useRef<{
    entry: LibraryEntry;
    layout: LibraryLayout;
    position: LibraryReadingPosition;
  } | null>(null);
  function beginRecovery(): Recovery {
    const saved = current && entry ? readLibraryPosition(entry, layout) : undefined;
    return saved
      ? { entry, layout, current, status: "restoring", saved, failed: false }
      : { entry, layout, current, status: "ready" };
  }
  const [recovery, setRecovery] = useState<Recovery>(beginRecovery);
  if (recovery.entry !== entry || recovery.layout !== layout || recovery.current !== current)
    setRecovery(beginRecovery());
  const requested = useRef<{ models: number | null; folders: number | null }>({
    models: null,
    folders: null,
  });
  useLayoutEffect(() => {
    return () => {
      operation.current += 1;
      if (renderFrame.current !== null) cancelAnimationFrame(renderFrame.current);
    };
  }, [entry, layout, current]);
  useLayoutEffect(() => {
    if (!current || !entry) return;
    requested.current = { models: null, folders: null };
    if (!readLibraryPosition(entry, layout)) {
      if (mainRef.current) mainRef.current.scrollTop = 0;
      if (listRef.current) listRef.current.scrollTop = 0;
    }
  }, [entry, layout, current, mainRef, listRef]);

  useLayoutEffect(() => {
    const main = mainRef.current;
    if (
      !current ||
      !entry ||
      !main ||
      recovery?.entry !== entry ||
      recovery.layout !== layout ||
      recovery.status !== "restoring"
    )
      return;
    const { saved } = recovery;
    const list = listRef.current;
    const failed = recovery.failed || pagination.models.failed || pagination.folders.failed;
    if (!pagination.ready && !failed) return;
    const missingPages =
      (pagination.models.count < saved.pages.models && pagination.models.more) ||
      (pagination.folders.count < saved.pages.folders && pagination.folders.more);
    if (!failed && (missingPages || pagination.models.pending || pagination.folders.pending))
      return;
    let restored = !failed;
    main.scrollTop = saved.main;
    if (list) list.scrollTop = saved.list ?? 0;
    if (saved.anchor && restored) {
      const anchor = main.querySelector<HTMLElement>(libraryReadingSelector(saved.anchor.key));
      if (
        renderers &&
        (renderAttempts.current.entry !== entry || renderAttempts.current.count < 8)
      ) {
        const pending = Array.from(renderers.current.values()).some(
          (renderer) => renderer.reveal(saved.anchor!.key) === "pending",
        );
        if (pending) {
          renderAttempts.current = {
            entry,
            count: renderAttempts.current.entry === entry ? renderAttempts.current.count + 1 : 1,
          };
          renderFrame.current = requestAnimationFrame(() =>
            setRenderAttempt((attempt) => attempt + 1),
          );
          return;
        }
      }
      const container = saved.anchor.container === "list" ? list : main;
      if (!anchor || !container) restored = false;
      else
        container.scrollTop +=
          anchor.getBoundingClientRect().top -
          container.getBoundingClientRect().top -
          saved.anchor.offset;
    } else if (restored) {
      restored =
        Math.abs(main.scrollTop - saved.main) < 1 &&
        (!list || Math.abs(list.scrollTop - (saved.list ?? 0)) < 1);
    }
    if (!restored) {
      main.scrollTop = 0;
      if (list) list.scrollTop = 0;
      saveLibraryPosition(entry, layout, {
        main: 0,
        list: list ? 0 : null,
        anchor: null,
        adjacent: null,
        pages: { models: pagination.models.count, folders: pagination.folders.count },
      });
    } else {
      // Geometry can require a different raw offset for the same anchor. Its
      // queued scroll notification must compare with these accepted pixels.
      const position = { ...saved, main: main.scrollTop, list: list?.scrollTop ?? null };
      saveLibraryPosition(entry, layout, position);
      captured.current = { entry, layout, position };
    }
    setRecovery({ entry, layout, current, status: restored ? "ready" : "reset" });
  }, [current, entry, layout, recovery, pagination, mainRef, listRef, renderers, renderAttempt]);

  useEffect(() => {
    if (
      !current ||
      !entry ||
      !pagination.ready ||
      recovery?.entry !== entry ||
      recovery.layout !== layout ||
      recovery.status !== "restoring" ||
      recovery.failed ||
      pagination.models.failed ||
      pagination.folders.failed
    )
      return;
    for (const kind of ["models", "folders"] as const) {
      const page = pagination[kind];
      if (
        page.count >= recovery.saved.pages[kind] ||
        !page.more ||
        page.pending ||
        requested.current[kind] === page.count
      )
        continue;
      requested.current[kind] = page.count;
      page.next();
    }
  }, [current, entry, layout, recovery, pagination]);

  const capturePosition = useCallback(
    (
      captureAnchor: boolean,
      target: HTMLElement | null = null,
      departing?: { main: HTMLElement; list: HTMLDivElement | null },
    ) => {
      const main = departing ? departing.main : mainRef.current;
      if (!current || !entry || !main) return;
      const prepared = preparedDeparture.current;
      if (departing && prepared?.entry === entry && prepared.layout === layout) {
        // An external Open page command already captured the visible source.
        // Route layout/row measurements can change before React detaches main.
        saveLibraryPosition(entry, layout, prepared.position);
        return prepared.position;
      }
      const list = departing ? departing.list : listRef.current;
      const previous = readLibraryPosition(entry, layout);
      const offsetChanged =
        previous?.main !== main.scrollTop || previous.list !== (list?.scrollTop ?? null);
      // A scroll event queued before the click may arrive after its snapshot.
      // Keep that anchor when no actual offset changed; real scrolling retires it.
      let anchor: LibraryReadingPosition["anchor"] = !offsetChanged
        ? (previous?.anchor ?? null)
        : null;
      let adjacent = anchor ? (previous?.adjacent ?? null) : null;
      // Scan visible entries only while recording an anchor, never for every scroll event.
      if (captureAnchor) {
        // A list may grow inside main instead of owning an independent scroll range.
        const container = list && list.scrollHeight > list.clientHeight ? list : main;
        const bounds = container.getBoundingClientRect();
        const seen = new Set<string>();
        const candidates = Array.from(
          main.querySelectorAll<HTMLElement>(libraryReadingSelector()),
        ).filter((node) => {
          const key = readingKey(node);
          if (!key || seen.has(key)) return false;
          seen.add(key);
          return true;
        });
        // A card and its explicit Open action name one entity. Its next reading
        // neighbor must be another entity rather than the same card's second link.
        const targetKey = readingKey(target ?? undefined);
        const visible =
          (targetKey ? candidates.find((node) => readingKey(node) === targetKey) : undefined) ??
          candidates.find((node) => {
            const rect = node.getBoundingClientRect();
            return rect.bottom > bounds.top && rect.top < bounds.bottom;
          });
        const key = readingKey(visible);
        if (visible && key) {
          anchor = {
            key,
            offset: visible.getBoundingClientRect().top - bounds.top,
            container: container === list ? "list" : "main",
          };
          const index = candidates.indexOf(visible);
          const neighbor = candidates[index + 1] ?? candidates[index - 1];
          const neighborKey = readingKey(neighbor);
          adjacent =
            neighbor && neighborKey
              ? {
                  key: neighborKey,
                  offset: neighbor.getBoundingClientRect().top - bounds.top,
                  container: container === list ? "list" : "main",
                }
              : null;
        }
      }
      saveLibraryPosition(entry, layout, {
        main: main.scrollTop,
        list: list?.scrollTop ?? null,
        anchor,
        adjacent,
        pages: { models: pagination.models.count, folders: pagination.folders.count },
      });
      const position = readLibraryPosition(entry, layout);
      if (
        position &&
        (captureAnchor ||
          offsetChanged ||
          captured.current?.entry !== entry ||
          captured.current.layout !== layout)
      )
        captured.current = { entry, layout, position };
      return position;
    },
    [current, entry, layout, mainRef, listRef, pagination.models.count, pagination.folders.count],
  );

  // An own confirmed removal promotes the saved neighbor in navigation-state.
  // Apply that promotion to the mounted view once its old card has actually left
  // the displayed snapshot. No new fetch, history entry or animation is involved.
  useLayoutEffect(() => {
    const previous = captured.current;
    const main = mainRef.current;
    if (
      !current ||
      !entry ||
      !main ||
      previous?.entry !== entry ||
      previous.layout !== layout ||
      recovery.status !== "ready"
    )
      return;
    const saved = readLibraryPosition(entry, layout);
    const before = previous.position.anchor;
    const after = saved?.anchor;
    if (
      !before ||
      !after ||
      before.key === after.key ||
      previous.position.adjacent?.key !== after.key
    )
      return;
    if (main.querySelector(libraryReadingSelector(before.key))) return;
    const anchor = main.querySelector<HTMLElement>(libraryReadingSelector(after.key));
    const container = after.container === "list" ? listRef.current : main;
    if (!anchor || !container) return;
    container.scrollTop +=
      anchor.getBoundingClientRect().top - container.getBoundingClientRect().top - after.offset;
    capturePosition(true, anchor);
  }, [current, entry, layout, recovery.status, pagination, mainRef, listRef, capturePosition]);

  async function refresh(replace: () => Promise<void>) {
    const saved = capturePosition(true);
    if (!saved || !entry || !current) return;
    const token = ++operation.current;
    renderAttempts.current = { entry, count: 0 };
    requested.current = { models: null, folders: null };
    setRecovery({ entry, layout, current, status: "refreshing", saved });
    let failed = false;
    try {
      await replace();
    } catch (error) {
      failed = true;
      throw error;
    } finally {
      if (operation.current === token && entry.session === getSessionVersion())
        setRecovery({ entry, layout, current, status: "restoring", saved, failed });
    }
  }

  useLayoutEffect(() => {
    const main = mainRef.current;
    if (
      !current ||
      !entry ||
      !main ||
      recovery?.entry !== entry ||
      recovery.layout !== layout ||
      recovery.status === "restoring" ||
      recovery.status === "refreshing"
    )
      return;
    const list = listRef.current;
    const capture = (event: Event) => {
      if (event.type === "click") preparedDeparture.current = null;
      capturePosition(
        event.type === "click",
        event.target instanceof Element
          ? (event.target.closest<HTMLElement>(libraryReadingSelector()) ??
              event.target
                .closest("article")
                ?.querySelector<HTMLElement>(libraryReadingSelector()) ??
              null)
          : null,
      );
    };
    main.addEventListener("scroll", capture, true);
    main.addEventListener("click", capture, true);
    return () => {
      main.removeEventListener("scroll", capture, true);
      main.removeEventListener("click", capture, true);
      // History traversal has no entry click to capture an anchor. Record the
      // visible entry before leaving: a revision notice can change the raw
      // offset without changing where the reader is. Retain the containers
      // because React may have detached their refs already.
      const anchor = readLibraryPosition(entry, layout)?.anchor;
      const target = anchor
        ? main.querySelector<HTMLElement>(libraryReadingSelector(anchor.key))
        : null;
      // A missing selected anchor must still reset on return, not adopt another row.
      capturePosition(!anchor || target !== null, target, { main, list });
    };
  }, [
    current,
    entry,
    layout,
    recovery,
    capturePosition,
    mainRef,
    listRef,
    pagination.models.count,
    pagination.folders.count,
  ]);
  const rememberAnchor = useCallback(
    (key: string) => {
      if (
        recovery?.entry !== entry ||
        recovery.layout !== layout ||
        recovery.status === "restoring" ||
        recovery.status === "refreshing"
      )
        return;
      const target = mainRef.current?.querySelector<HTMLElement>(libraryReadingSelector(key));
      const position = capturePosition(true, target ?? null);
      if (entry && position) preparedDeparture.current = { entry, layout, position };
    },
    [recovery, entry, layout, mainRef, capturePosition],
  );
  useLayoutEffect(() => {
    if (!current || !entry) return;
    return router.subscribe("onBeforeNavigate", ({ fromLocation, toLocation }) => {
      if (
        fromLocation?.state.__TSR_key !== entry.key ||
        toLocation.state.__TSR_key === entry.key ||
        entry.session !== getSessionVersion() ||
        recovery.entry !== entry ||
        recovery.layout !== layout ||
        recovery.status === "restoring" ||
        recovery.status === "refreshing"
      )
        return;
      const prepared = preparedDeparture.current;
      if (prepared?.entry === entry && prepared.layout === layout) return;
      // Browser Forward/Back has no card click. Capture before Router changes
      // the shell geometry, while the committed source DOM is still intact.
      const saved = readLibraryPosition(entry, layout);
      const target = saved?.anchor
        ? mainRef.current?.querySelector<HTMLElement>(libraryReadingSelector(saved.anchor.key))
        : null;
      const position = capturePosition(!saved?.anchor || !!target, target ?? null);
      if (position) preparedDeparture.current = { entry, layout, position };
    });
  }, [router, current, entry, layout, recovery, mainRef, capturePosition]);
  const status =
    recovery?.entry === entry && recovery.layout === layout ? recovery.status : "ready";
  return { status: status === "refreshing" ? "restoring" : status, refresh, rememberAnchor };
}
