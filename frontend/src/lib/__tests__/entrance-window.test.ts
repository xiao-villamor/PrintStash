/*
 * An entrance's motion lasts as long as the entrance and no longer.
 *
 * Library entrance classes sit on containers whose children mount later (a
 * virtual row after a reflow, a card scrolled back into view). If the class
 * outlived its entrance, every later mount would replay it and settled content
 * would slide in again. So the value must be present during the window, gone
 * after it, and present again for the next entrance.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useEntranceWindow } from "../entrance-window";

type Props = { entrance: object; value: string | null };

function mount(initial: Props) {
  return renderHook(({ entrance, value }: Props) => useEntranceWindow(entrance, value, 200), {
    initialProps: initial,
  });
}

describe("useEntranceWindow", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("applies the entrance value while the entrance plays", () => {
    const hook = mount({ entrance: {}, value: "folder-cards-forward" });
    act(() => vi.advanceTimersByTime(199));
    expect(hook.result.current).toBe("folder-cards-forward");
  });

  it("releases the entrance value once the entrance has played", () => {
    const hook = mount({ entrance: {}, value: "folder-cards-forward" });
    act(() => vi.advanceTimersByTime(200));
    expect(hook.result.current).toBeNull();
  });

  it("applies the value again for the next entrance", () => {
    const hook = mount({ entrance: {}, value: "results-settle" });
    act(() => vi.advanceTimersByTime(200));
    hook.rerender({ entrance: {}, value: "results-settle" });
    expect(hook.result.current).toBe("results-settle");
  });

  it("restarts the window when a new entrance begins before the last one ends", () => {
    const hook = mount({ entrance: {}, value: "results-settle" });
    act(() => vi.advanceTimersByTime(150));
    hook.rerender({ entrance: {}, value: "results-settle" });
    act(() => vi.advanceTimersByTime(150));
    expect(hook.result.current).toBe("results-settle");
  });

  it("applies nothing when nothing enters", () => {
    const hook = mount({ entrance: {}, value: null });
    expect(hook.result.current).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("keeps a released value released across re-renders of the same entrance", () => {
    const entrance = {};
    const hook = mount({ entrance, value: "folder-cards-back" });
    act(() => vi.advanceTimersByTime(200));
    hook.rerender({ entrance, value: "folder-cards-back" });
    expect(hook.result.current).toBeNull();
  });
});
