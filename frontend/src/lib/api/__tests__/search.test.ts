/** Interactive searches have bounded waits and preserve navigation cancellation. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  getSearchPreferences,
  parseSearch,
  searchImage,
  searchLibrary,
  searchUsingModel,
} from "@/lib/api/search";
import { json } from "@/test-support/render";
import { searchResponse } from "@/test-support/search";

const fetcher = vi.fn<typeof fetch>();
describe("Interactive search requests", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    fetcher.mockReset();
    vi.stubGlobal("fetch", fetcher);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });
  function stall() {
    fetcher.mockImplementation(
      (_url, options) =>
        new Promise<Response>((_resolve, reject) => {
          const signal = options?.signal;
          if (signal?.aborted) reject(signal.reason);
          else signal?.addEventListener("abort", () => reject(signal.reason), { once: true });
        }),
    );
  }
  it.each([
    { label: "text search", run: () => searchLibrary({ q: "bracket" }) },
    {
      label: "image search",
      run: () => searchImage(new File(["image"], "query.png", { type: "image/png" })),
    },
    { label: "related model search", run: () => searchUsingModel(12) },
    { label: "filter interpretation", run: () => parseSearch("bracket") },
    { label: "search preferences", run: () => getSearchPreferences() },
  ])("ends a stalled $label with a recoverable timeout", async ({ run }) => {
    stall();
    await Promise.all([
      expect(run()).rejects.toMatchObject({ code: "search_timeout", status: 408 }),
      vi.advanceTimersByTimeAsync(30_000),
    ]);
    expect(vi.getTimerCount()).toBe(0);
  });
  it("cancels retrieval when navigation aborts", async () => {
    stall();
    const controller = new AbortController();
    await Promise.all([
      expect(searchLibrary({ q: "bracket" }, controller.signal)).rejects.toMatchObject({
        name: "AbortError",
      }),
      Promise.resolve().then(() => controller.abort()),
    ]);
    expect(vi.getTimerCount()).toBe(0);
  });
  it("rejects an already cancelled search", async () => {
    stall();
    const controller = new AbortController();
    controller.abort();
    await expect(searchLibrary({ q: "bracket" }, controller.signal)).rejects.toMatchObject({
      name: "AbortError",
    });
    expect(vi.getTimerCount()).toBe(0);
  });
  it("releases the deadline after successful retrieval", async () => {
    const response = searchResponse();
    fetcher.mockResolvedValue(json(response));
    expect(await searchLibrary({ q: "bracket" })).toEqual(response);
    expect(vi.getTimerCount()).toBe(0);
  });
  it("preserves server errors", async () => {
    fetcher.mockResolvedValue(json({ detail: "search_unavailable" }, 503));
    await expect(searchLibrary({ q: "bracket" })).rejects.toMatchObject({
      code: "search_unavailable",
      status: 503,
    });
    expect(vi.getTimerCount()).toBe(0);
  });
  it("preserves network errors", async () => {
    fetcher.mockRejectedValue(new TypeError("Network unavailable"));
    await expect(searchLibrary({ q: "bracket" })).rejects.toThrow("Network unavailable");
    expect(vi.getTimerCount()).toBe(0);
  });
});
