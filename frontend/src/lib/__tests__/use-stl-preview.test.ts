/** STL preparation is polled as status; terminal failures never become STL bytes. */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { getDerivedBlob } from "@/lib/api/request";
import { stlPreviewMessage, useStlPreview } from "../use-stl-preview";

describe("useStlPreview", () => {
  const request = vi.fn<typeof fetch>();
  beforeEach(() => {
    vi.stubGlobal("fetch", request);
    request.mockReset();
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:preview");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("delegates STL preparation to an explicit fetcher", async () => {
    const fetcher = vi.fn<typeof getDerivedBlob>().mockResolvedValue({ ready: false });
    const { unmount } = renderHook(() => useStlPreview("/public/stl", undefined, fetcher));
    await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
    expect(fetcher).toHaveBeenCalledWith("/public/stl", expect.any(AbortSignal));
    expect(request).not.toHaveBeenCalled();
    unmount();
  });

  it("hides STL bytes when the preparation fetcher changes", async () => {
    const first = vi
      .fn<typeof getDerivedBlob>()
      .mockResolvedValue({ ready: true, blob: new Blob(["first"]) });
    const pending = Promise.withResolvers<Awaited<ReturnType<typeof getDerivedBlob>>>();
    const second = vi.fn<typeof getDerivedBlob>().mockReturnValue(pending.promise);
    const { result, rerender, unmount } = renderHook(
      ({ fetcher }) => useStlPreview("/public/stl", undefined, fetcher),
      { initialProps: { fetcher: first } },
    );
    await waitFor(() => expect(result.current.state).toBe("ready"));
    rerender({ fetcher: second });
    expect(result.current).toEqual({ state: "pending" });
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:preview");
    unmount();
    await act(async () => pending.resolve({ ready: false }));
  });

  it("waits for prepared bytes", async () => {
    vi.useFakeTimers();
    request
      .mockResolvedValueOnce(new Response(JSON.stringify({ state: "pending" }), { status: 202 }))
      .mockResolvedValueOnce(new Response(new Blob(["stl"])));
    const { result } = renderHook(() => useStlPreview("/api/v1/files/1/stl"));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });

    expect(result.current).toEqual({ state: "ready", url: "blob:preview" });
    expect(request).toHaveBeenCalledTimes(2);
  });

  it("stops polling a terminal failure", async () => {
    request.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "resource_limit" }), { status: 422 }),
    );
    const { result } = renderHook(() => useStlPreview("/api/v1/files/1/stl"));

    await waitFor(() =>
      expect(result.current).toEqual({ state: "failed", reason: "resource_limit" }),
    );

    expect(request).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["timeout", "timeout"],
    ["invalid_source", "invalid_source"],
    ["derivative_group_disabled", "derivative_group_disabled"],
    ["cancelled", "cancelled"],
    ["unexpected_provider_failure", "worker_failed"],
  ])("reports terminal %s without retrying or creating a preview", async (code, reason) => {
    request.mockResolvedValueOnce(new Response(JSON.stringify({ detail: code }), { status: 422 }));
    const { result } = renderHook(() => useStlPreview("/api/v1/files/1/stl"));

    await waitFor(() => expect(result.current).toEqual({ state: "failed", reason }));

    expect(request).toHaveBeenCalledTimes(1);
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it("does not request an absent preview source", () => {
    const { result } = renderHook(() => useStlPreview(null));
    expect(result.current).toEqual({ state: "pending" });
    expect(request).not.toHaveBeenCalled();
  });

  it("discards late bytes from an obsolete source", async () => {
    const old = Promise.withResolvers<Response>();
    request.mockReturnValueOnce(old.promise).mockResolvedValueOnce(new Response(new Blob(["new"])));
    const { result, rerender } = renderHook(({ url }) => useStlPreview(url), {
      initialProps: { url: "/api/v1/files/1/stl" },
    });
    const oldSignal = request.mock.calls[0][1]?.signal;

    rerender({ url: "/api/v1/files/2/stl" });
    await waitFor(() => expect(result.current).toEqual({ state: "ready", url: "blob:preview" }));
    await act(async () => old.resolve(new Response(new Blob(["obsolete"]))));

    expect(oldSignal?.aborted).toBe(true);
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledTimes(2);
    expect(result.current).toEqual({ state: "ready", url: "blob:preview" });
  });

  it("does not surface a canceled load rejection after unmount", async () => {
    const pending = Promise.withResolvers<Response>();
    request.mockReturnValueOnce(pending.promise);
    const { unmount } = renderHook(() => useStlPreview("/api/v1/files/1/stl"));
    unmount();

    await act(async () => pending.reject(new DOMException("Cancelled", "AbortError")));

    expect(request).toHaveBeenCalledTimes(1);
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it("releases the browser preview", async () => {
    request.mockResolvedValueOnce(new Response(new Blob(["stl"])));
    const { result, unmount } = renderHook(() => useStlPreview("/api/v1/files/1/stl"));
    await waitFor(() => expect(result.current.state).toBe("ready"));

    unmount();

    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:preview");
  });

  it("aborts a pending load on unmount", () => {
    request.mockImplementation(() => new Promise(() => {}));
    const { unmount } = renderHook(() => useStlPreview("/api/v1/files/1/stl"));

    unmount();

    expect(request.mock.calls[0][1]?.signal?.aborted).toBe(true);
  });
});

describe("stlPreviewMessage", () => {
  it.each([
    ["resource_limit", "3D preview omitted due to memory limits"],
    ["timeout", "3D preview preparation timed out"],
    ["invalid_source", "This file cannot be converted to a 3D preview"],
    ["derivative_group_disabled", "3D preview processing is disabled"],
    ["cancelled", "3D preview preparation was cancelled"],
    ["worker_failed", "Failed to load 3D preview"],
  ] as const)("explains %s to the user", (reason, message) => {
    expect(stlPreviewMessage(reason)).toBe(message);
  });
});
