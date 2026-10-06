import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { getSessionVersion } from "@/lib/session-transport";
import {
  readLibraryPosition,
  saveLibraryPosition,
  type LibraryEntry,
  type LibraryLayout,
  type LibraryReadingPosition,
} from "./navigation-state";

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
} & ({ status: "restoring"; saved: LibraryReadingPosition } | { status: "ready" | "reset" });

/** Own nested scroll restoration and its bounded page reconstruction, never remote data. */
export function useLibraryReadingPosition(
  entry: LibraryEntry | undefined,
  layout: LibraryLayout,
  mainRef: RefObject<HTMLElement | null>,
  listRef: RefObject<HTMLDivElement | null>,
  enabled: boolean,
  pagination: LibraryReadingPagination,
) {
  const current = enabled && entry?.session === getSessionVersion();
  function beginRecovery(): Recovery {
    const saved = current && entry ? readLibraryPosition(entry, layout) : undefined;
    return saved
      ? { entry, layout, current, status: "restoring", saved }
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
      !pagination.ready ||
      recovery?.entry !== entry ||
      recovery.layout !== layout ||
      recovery.status !== "restoring"
    )
      return;
    const { saved } = recovery;
    const list = listRef.current;
    const failed = pagination.models.failed || pagination.folders.failed;
    const missingPages =
      (pagination.models.count < saved.pages.models && pagination.models.more) ||
      (pagination.folders.count < saved.pages.folders && pagination.folders.more);
    if (!failed && (missingPages || pagination.models.pending || pagination.folders.pending))
      return;
    let restored = !failed;
    main.scrollTop = saved.main;
    if (list) list.scrollTop = saved.list ?? 0;
    if (saved.anchor && restored) {
      const anchor = main.querySelector<HTMLElement>(
        `[data-library-entry="${CSS.escape(saved.anchor.key)}"]`,
      );
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
    }
    setRecovery({ entry, layout, current, status: restored ? "ready" : "reset" });
  }, [current, entry, layout, recovery, pagination, mainRef, listRef]);

  useEffect(() => {
    if (
      !current ||
      !entry ||
      !pagination.ready ||
      recovery?.entry !== entry ||
      recovery.layout !== layout ||
      recovery.status !== "restoring" ||
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

  useLayoutEffect(() => {
    const main = mainRef.current;
    if (
      !current ||
      !entry ||
      !main ||
      recovery?.entry !== entry ||
      recovery.layout !== layout ||
      recovery.status === "restoring"
    )
      return;
    const list = listRef.current;
    const capture = (event: Event) => {
      const previous = readLibraryPosition(entry, layout);
      // A scroll event queued before the click may arrive after its snapshot.
      // Keep that anchor when no actual offset changed; real scrolling retires it.
      let anchor: LibraryReadingPosition["anchor"] =
        previous?.main === main.scrollTop && previous.list === (list?.scrollTop ?? null)
          ? previous.anchor
          : null;
      let adjacent = anchor ? (previous?.adjacent ?? null) : null;
      // Scan visible entries only at a gesture, never for every scroll event.
      if (event.type === "click") {
        const target =
          event.target instanceof Element
            ? event.target.closest<HTMLElement>("[data-library-entry]")
            : null;
        // A list may grow inside main instead of owning an independent scroll range.
        const container = list && list.scrollHeight > list.clientHeight ? list : main;
        const bounds = container.getBoundingClientRect();
        const candidates = Array.from(main.querySelectorAll<HTMLElement>("[data-library-entry]"));
        const visible =
          target ??
          candidates.find((node) => {
            const rect = node.getBoundingClientRect();
            return rect.bottom > bounds.top && rect.top < bounds.bottom;
          });
        const key = visible?.dataset.libraryEntry;
        if (visible && key) {
          anchor = {
            key,
            offset: visible.getBoundingClientRect().top - bounds.top,
            container: container === list ? "list" : "main",
          };
          const index = candidates.indexOf(visible);
          const neighbor = candidates[index + 1] ?? candidates[index - 1];
          const neighborKey = neighbor?.dataset.libraryEntry;
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
    };
    main.addEventListener("scroll", capture, true);
    main.addEventListener("click", capture, true);
    return () => {
      main.removeEventListener("scroll", capture, true);
      main.removeEventListener("click", capture, true);
    };
  }, [
    current,
    entry,
    layout,
    recovery,
    mainRef,
    listRef,
    pagination.models.count,
    pagination.folders.count,
  ]);
  return recovery?.entry === entry && recovery.layout === layout ? recovery.status : "ready";
}
