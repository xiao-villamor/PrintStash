/*
 * The router exposes real push, replace, Back and Forward history operations.
 * The router object keeps its identity across consumer re-renders.
 *
 * Every navigation callback in the app closes over this, and most of them end up
 * in a `useEffect` dependency array or a memo. A router that is a new object each
 * render re-runs all of them on every render — which is not a wrong screen, it is
 * a re-fetch and a re-subscribe per keystroke, and it looks like the app being
 * slow rather than like a bug here.
 */

import type { ReactNode } from "react";
import { act, renderHook } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { useRouter } from "@/lib/navigation";

function wrapper({ children }: { children: ReactNode }) {
  return <MemoryRouter>{children}</MemoryRouter>;
}

function renderNavigation(initialEntries: string[], initialIndex = initialEntries.length - 1) {
  return renderHook(() => ({ router: useRouter(), location: useLocation() }), {
    wrapper: ({ children }) => (
      <MemoryRouter initialEntries={initialEntries} initialIndex={initialIndex}>
        {children}
      </MemoryRouter>
    ),
  });
}

describe("useRouter", () => {
  it("pushes a destination onto history", () => {
    const { result } = renderNavigation(["/library"]);

    act(() => result.current.router.push("/models/7?tab=files#preview"));

    expect(result.current.location).toMatchObject({
      pathname: "/models/7",
      search: "?tab=files",
      hash: "#preview",
    });
    act(() => result.current.router.back());
    expect(result.current.location.pathname).toBe("/library");
  });

  it("replaces the current history entry", () => {
    const { result } = renderNavigation(["/earlier", "/library"]);

    act(() => result.current.router.replace("/search?q=bracket"));

    expect(result.current.location).toMatchObject({ pathname: "/search", search: "?q=bracket" });
    act(() => result.current.router.back());
    expect(result.current.location.pathname).toBe("/earlier");
  });

  it("returns to the previous history entry", () => {
    const { result } = renderNavigation(["/library", "/models/7"]);

    act(() => result.current.router.back());

    expect(result.current.location.pathname).toBe("/library");
  });

  it("advances to the next history entry", () => {
    const { result } = renderNavigation(["/library", "/models/7"], 0);

    act(() => result.current.router.forward());

    expect(result.current.location.pathname).toBe("/models/7");
  });

  it.each([{ direction: "back" }, { direction: "forward" }] as const)(
    "keeps the current route at the $direction history boundary",
    ({ direction }) => {
      const { result } = renderNavigation(["/library"]);

      act(() => result.current.router[direction]());

      expect(result.current.location.pathname).toBe("/library");
    },
  );

  it("keeps router identity stable across consumer rerenders", () => {
    const { result, rerender } = renderHook(() => useRouter(), { wrapper });
    const firstRouter = result.current;

    rerender();

    expect(result.current).toBe(firstRouter);
  });
});
