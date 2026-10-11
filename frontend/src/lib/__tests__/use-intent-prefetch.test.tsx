/** Speculative reads follow one sustained intent and never compete with navigation. */
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useIntentPrefetch } from "@/lib/use-intent-prefetch";

afterEach(() => vi.useRealTimers());

describe("useIntentPrefetch", () => {
  it("keeps pointer intent outside the rendering path", () => {
    vi.useFakeTimers();
    const loads: string[] = [];
    let renders = 0;
    const { result } = renderHook(() => {
      renders++;
      return useIntentPrefetch((path) => loads.push(path), true);
    });
    const initialRenders = renders;
    act(() => result.current("parts"));
    act(() => vi.advanceTimersByTime(150));
    expect(loads).toEqual(["parts"]);
    expect(renders).toBe(initialRenders);
  });

  it("waits for sustained intent", () => {
    vi.useFakeTimers();
    const loads: string[] = [];
    const { result } = renderHook(() => useIntentPrefetch((path) => loads.push(path), true));
    act(() => result.current("parts"));
    act(() => vi.advanceTimersByTime(149));
    expect(loads).toEqual([]);
    act(() => vi.advanceTimersByTime(1));
    expect(loads).toEqual(["parts"]);
  });
  it("loads only the latest intended destination", () => {
    vi.useFakeTimers();
    const loads: string[] = [];
    const { result } = renderHook(() => useIntentPrefetch((path) => loads.push(path), true));
    act(() => result.current("old"));
    act(() => vi.advanceTimersByTime(100));
    act(() => result.current("current"));
    act(() => vi.advanceTimersByTime(150));
    expect(loads).toEqual(["current"]);
  });
  it("preserves sustained intent when focus follows the pointer", () => {
    vi.useFakeTimers();
    const loads: string[] = [];
    const { result } = renderHook(() => useIntentPrefetch((path) => loads.push(path), true));
    act(() => result.current("parts"));
    act(() => vi.advanceTimersByTime(100));
    act(() => result.current("parts"));
    act(() => vi.advanceTimersByTime(50));
    expect(loads).toEqual(["parts"]);
  });
  it("uses the current reader after the intent began", () => {
    vi.useFakeTimers();
    const loads: string[] = [];
    const { result, rerender } = renderHook(
      ({ prefix }) => useIntentPrefetch((path) => loads.push(`${prefix}:${path}`), true),
      { initialProps: { prefix: "old" } },
    );
    act(() => result.current("parts"));
    rerender({ prefix: "current" });
    act(() => vi.advanceTimersByTime(150));
    expect(loads).toEqual(["current:parts"]);
  });
  it("cancels the pending timer on unmount", () => {
    vi.useFakeTimers();
    const loads: string[] = [];
    const { result, unmount } = renderHook(() =>
      useIntentPrefetch((path) => loads.push(path), true),
    );
    act(() => result.current("parts"));
    unmount();
    act(() => vi.advanceTimersByTime(150));
    expect(loads).toEqual([]);
  });
  it("cancels abandoned intent", () => {
    vi.useFakeTimers();
    const loads: string[] = [];
    const { result } = renderHook(() => useIntentPrefetch((path) => loads.push(path), true));
    act(() => result.current("parts"));
    act(() => result.current(null));
    act(() => vi.advanceTimersByTime(200));
    expect(loads).toEqual([]);
  });
  it("defers speculative reads during critical navigation", () => {
    vi.useFakeTimers();
    const loads: string[] = [];
    const { result, rerender } = renderHook(
      ({ enabled }) => useIntentPrefetch((path) => loads.push(path), enabled),
      { initialProps: { enabled: false } },
    );
    act(() => result.current("parts"));
    act(() => vi.advanceTimersByTime(200));
    expect(loads).toEqual([]);
    rerender({ enabled: true });
    act(() => vi.advanceTimersByTime(150));
    expect(loads).toEqual(["parts"]);
  });
  it("retires speculative work when navigation starts", () => {
    vi.useFakeTimers();
    const signals: AbortSignal[] = [];
    const { result, rerender } = renderHook(
      ({ enabled }) => useIntentPrefetch((_path, signal) => signals.push(signal), enabled),
      { initialProps: { enabled: true } },
    );
    act(() => result.current("parts"));
    act(() => vi.advanceTimersByTime(150));
    expect(signals[0].aborted).toBe(false);
    rerender({ enabled: false });
    expect(signals[0].aborted).toBe(true);
  });
});
