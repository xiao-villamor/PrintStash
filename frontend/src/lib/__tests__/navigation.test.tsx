/*
 * The router object keeps its identity across consumer re-renders.
 *
 * Every navigation callback in the app closes over this, and most of them end up
 * in a `useEffect` dependency array or a memo. A router that is a new object each
 * render re-runs all of them on every render — which is not a wrong screen, it is
 * a re-fetch and a re-subscribe per keystroke, and it looks like the app being
 * slow rather than like a bug here.
 */

import type { ReactNode } from "react";
import { renderHook } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { useRouter } from "@/lib/navigation";

function wrapper({ children }: { children: ReactNode }) {
  return <MemoryRouter>{children}</MemoryRouter>;
}

describe("useRouter", () => {
  it("keeps router identity stable across consumer rerenders", () => {
    const { result, rerender } = renderHook(() => useRouter(), { wrapper });
    const firstRouter = result.current;

    rerender();

    expect(result.current).toBe(firstRouter);
  });
});
