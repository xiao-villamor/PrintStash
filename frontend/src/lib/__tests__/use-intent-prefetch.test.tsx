/** Speculative reads follow one sustained intent and never compete with navigation. */
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useIntentPrefetch } from "@/lib/use-intent-prefetch";

afterEach(() => vi.useRealTimers());

describe("useIntentPrefetch", () => {
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
