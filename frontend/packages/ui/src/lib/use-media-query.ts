"use client";

import { useCallback, useSyncExternalStore } from "react";

const noopSub = () => () => {};

/**
 * `matchMedia` is a browser-document API: it is absent during a non-browser
 * render (prerender/SSR, node test runner without jsdom), where every query
 * reports as unmatched and there is nothing to subscribe to.
 */
const hasMatchMedia = (): boolean => "matchMedia" in globalThis;

function getSnapshot(query: string) {
  if (!hasMatchMedia()) return false;
  return window.matchMedia(query).matches;
}

function subscribe(onChange: () => void, query: string) {
  if (!hasMatchMedia()) return noopSub();
  const mql = window.matchMedia(query);
  mql.addEventListener("change", onChange);
  return () => mql.removeEventListener("change", onChange);
}

export function useMediaQuery(query: string): boolean {
  const subscribeToMql = useCallback((onChange: () => void) => subscribe(onChange, query), [query]);
  const getSnap = useCallback(() => getSnapshot(query), [query]);
  return useSyncExternalStore(subscribeToMql, getSnap);
}
