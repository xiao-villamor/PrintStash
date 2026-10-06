import { useLayoutEffect, type RefObject } from "react";
import {
  readLibraryPosition,
  saveLibraryPosition,
  type LibraryEntry,
  type LibraryLayout,
} from "./navigation-state";

/** Own the two real scroll containers; window restoration cannot observe either. */
export function useLibraryReadingPosition(
  entry: LibraryEntry | undefined,
  layout: LibraryLayout,
  mainRef: RefObject<HTMLElement | null>,
  listRef: RefObject<HTMLDivElement | null>,
  enabled: boolean,
) {
  useLayoutEffect(() => {
    const main = mainRef.current;
    if (!enabled || !entry || !main) return;
    const list = listRef.current;
    const saved = readLibraryPosition(entry, layout);
    main.scrollTop = saved?.main ?? 0;
    if (list) list.scrollTop = saved?.list ?? 0;
    const capture = () => {
      saveLibraryPosition(entry, layout, {
        main: main.scrollTop,
        list: list?.scrollTop ?? null,
      });
    };
    // Scroll does not bubble. Capture records list scrolling as well as main.
    // Click captures the final synchronous position before Router unmounts us.
    main.addEventListener("scroll", capture, true);
    main.addEventListener("click", capture, true);
    return () => {
      main.removeEventListener("scroll", capture, true);
      main.removeEventListener("click", capture, true);
    };
  }, [entry, layout, mainRef, listRef, enabled]);
}
