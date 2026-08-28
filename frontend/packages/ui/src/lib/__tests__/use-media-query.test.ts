/*
 * The hook every responsive branch in the product reads: the drawer-versus-modal
 * choice, the mobile navigation, the reduced-motion opt-out.
 *
 * Two failure modes matter and neither is visible in a rendered component.
 *
 * `matchMedia` is a browser-document API, absent during a non-browser render. The
 * hook has to answer "no match" there instead of throwing, because a crash in a
 * layout hook takes the whole page down rather than degrading one breakpoint.
 *
 * And the subscription is the point: a hook that reads `matches` once and never
 * listens leaves the UI stuck in whichever layout the page happened to load with,
 * which looks correct until someone rotates a tablet. So the tests drive a real
 * change event and assert the render followed it — and assert the listener is
 * removed, because a `useSyncExternalStore` leak survives every unmount.
 */

import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useMediaQuery } from "../use-media-query";

const WIDE = "(min-width: 768px)";

/** A MediaQueryList whose `matches` we can flip, exposing its listener count. */
function fakeMatchMedia(matching: (query: string) => boolean) {
  const listeners = new Map<string, Set<() => void>>();
  const state = new Map<string, boolean>();

  function emit(query: string, matches: boolean) {
    state.set(query, matches);
    for (const listener of listeners.get(query) ?? []) listener();
  }

  function matchMedia(query: string) {
    return {
      get matches() {
        return state.get(query) ?? matching(query);
      },
      addEventListener: (_type: string, listener: () => void) => {
        const set = listeners.get(query) ?? new Set<() => void>();
        set.add(listener);
        listeners.set(query, set);
      },
      removeEventListener: (_type: string, listener: () => void) => {
        listeners.get(query)?.delete(listener);
      },
    };
  }

  vi.stubGlobal("matchMedia", matchMedia);
  return {
    emit,
    listenerCount: (query: string) => listeners.get(query)?.size ?? 0,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useMediaQuery", () => {
  it("reports a query the browser already matches", () => {
    fakeMatchMedia(() => true);

    const { result } = renderHook(() => useMediaQuery(WIDE));

    expect(result.current).toBe(true);
  });

  it("reports a query the browser does not match", () => {
    fakeMatchMedia(() => false);

    const { result } = renderHook(() => useMediaQuery(WIDE));

    expect(result.current).toBe(false);
  });

  it("follows the query when the viewport changes", () => {
    const media = fakeMatchMedia(() => false);
    const { result } = renderHook(() => useMediaQuery(WIDE));

    act(() => {
      media.emit(WIDE, true);
    });

    expect(result.current).toBe(true);
  });

  it("moves its subscription when the query changes", () => {
    const media = fakeMatchMedia(() => false);
    const { rerender } = renderHook(({ query }) => useMediaQuery(query), {
      initialProps: { query: WIDE },
    });

    rerender({ query: "(min-width: 1280px)" });

    expect(media.listenerCount(WIDE)).toBe(0);
    expect(media.listenerCount("(min-width: 1280px)")).toBe(1);
  });

  it("drops its subscription on unmount", () => {
    const media = fakeMatchMedia(() => true);
    const { unmount } = renderHook(() => useMediaQuery(WIDE));

    unmount();

    expect(media.listenerCount(WIDE)).toBe(0);
  });

  it("reports no match where matchMedia does not exist", () => {
    const descriptor = Object.getOwnPropertyDescriptor(globalThis, "matchMedia");
    Reflect.deleteProperty(globalThis, "matchMedia");

    try {
      const { result } = renderHook(() => useMediaQuery(WIDE));

      expect(result.current).toBe(false);
    } finally {
      if (descriptor) Object.defineProperty(globalThis, "matchMedia", descriptor);
    }
  });
});
