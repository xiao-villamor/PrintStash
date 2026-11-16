import { useLayoutEffect, useMemo, useState, useSyncExternalStore } from "react";
import { useLocation, type ParsedLocation } from "@tanstack/react-router";
import { onAuthChange } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";

export type LibraryEntry = Readonly<{
  key: string;
  href: string;
  session: number;
  index: number | null;
}>;
export type LibraryLayout = "grid" | "list";
export interface LibraryReadingAnchor {
  key: string;
  offset: number;
  container: "main" | "list";
}
export interface LibraryReadingPosition {
  main: number;
  list: number | null;
  anchor: LibraryReadingAnchor | null;
  adjacent: LibraryReadingAnchor | null;
  pages: { models: number; folders: number };
}
interface RegisteredEntry {
  entry: LibraryEntry;
  positions: Partial<Record<LibraryLayout, LibraryReadingPosition>>;
}
const entries = new Map<string, RegisteredEntry>();
const MAX_HISTORY_ENTRIES = 64;
const stop = onAuthChange(() => entries.clear());
if (import.meta.hot) import.meta.hot.dispose(stop);

/** The installed TanStack history envelope owns the index, including memory history. */
export function historyIndex(state: ParsedLocation["state"]): number | null {
  return Number.isInteger(state.__TSR_index) ? state.__TSR_index : null;
}

/** Bind identity to a settled visual snapshot, rather than the requested route. */
export function useLibraryEntry(href: string, ready: boolean) {
  const location = useLocation();
  const [session] = useState(getSessionVersion);
  const current = useSyncExternalStore(onAuthChange, getSessionVersion);
  const key = location.state.__TSR_key;
  const index = historyIndex(location.state);
  if (key === undefined) throw new Error("Router history entry has no identity");
  const entry = useMemo(() => ({ key, href, session, index }), [key, href, session, index]);
  useLayoutEffect(() => {
    if (!ready || session !== current) return;
    const previous = entries.get(entry.key);
    entries.set(entry.key, {
      entry,
      positions: previous?.entry.href === entry.href ? previous.positions : {},
    });
    if (entries.size > MAX_HISTORY_ENTRIES) entries.delete(entries.keys().next().value!);
  }, [entry, ready, session, current]);
  return entry;
}

// oxlint-disable anti-slop/no-runtime-typeof, anti-slop/no-unknown-parameters -- Parse foreign Router state into a registered, session-owned entry; no unparsed payload escapes this boundary.
export function knownOrigin(state: unknown): LibraryEntry | undefined {
  if (
    state === null ||
    typeof state !== "object" ||
    !("libraryOrigin" in state) ||
    typeof state.libraryOrigin !== "string"
  )
    return undefined;
  const entry = entries.get(state.libraryOrigin)?.entry;
  return entry?.session === getSessionVersion() ? entry : undefined;
}

// oxlint-enable anti-slop/no-runtime-typeof, anti-slop/no-unknown-parameters

/** Reading metadata shares the bounded, session-owned entry lifetime. */
export function readLibraryPosition(entry: LibraryEntry, layout: LibraryLayout) {
  const registered = entries.get(entry.key);
  if (entry.session !== getSessionVersion() || registered?.entry.href !== entry.href) return;
  return registered.positions[layout];
}

export function saveLibraryPosition(
  entry: LibraryEntry,
  layout: LibraryLayout,
  position: LibraryReadingPosition,
) {
  const registered = entries.get(entry.key);
  if (entry.session !== getSessionVersion() || registered?.entry.href !== entry.href) return;
  registered.positions[layout] = position;
}

/** Own confirmed removals may preserve a neighbor; external missing anchors still reset. */
export function acknowledgeFavoriteRemoval(key: string, session: number) {
  if (session !== getSessionVersion()) return;
  for (const registered of entries.values()) {
    if (
      new URL(registered.entry.href, window.location.origin).searchParams.get("favorites") !==
      "true"
    )
      continue;
    for (const layout of ["grid", "list"] as const) {
      const position = registered.positions[layout];
      if (position?.anchor?.key !== key || !position.adjacent) continue;
      registered.positions[layout] = { ...position, anchor: position.adjacent, adjacent: null };
    }
  }
}
