/** Protected image ownership preserves visible URLs and bounds authorized work. */
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getCachedAssetUrl, invalidateCachedAsset } from "@/lib/asset-cache";
import { useAuthenticatedAssetUrl } from "@/lib/use-authenticated-asset-url";
import { retirePrivateSessionScope } from "@/lib/auth-store";

let revoked: string[];
beforeEach(() => {
  revoked = [];
  let next = 0;
  vi.stubGlobal("URL", {
    createObjectURL: () => `blob:hook-${++next}`,
    revokeObjectURL: (url: string) => revoked.push(url),
  });
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async () => new Response("png")),
  );
});
afterEach(() => {
  cleanup();
  retirePrivateSessionScope();
  vi.unstubAllGlobals();
});

describe("useAuthenticatedAssetUrl", () => {
  it("acquires a lease for an already cached image", async () => {
    const cached = await getCachedAssetUrl("/hook/cached");
    const hook = renderHook(() => useAuthenticatedAssetUrl("/hook/cached"));
    expect(hook.result.current).toBe(cached);
    await act(async () => {
      for (let i = 0; i < 405; i++) await getCachedAssetUrl(`/hook/pressure-${i}`);
    });
    expect(hook.result.current).toBe(cached);
    expect(revoked).not.toContain(cached);
  });

  it("clears a resolved image after private scope retirement", async () => {
    const hook = renderHook(() => useAuthenticatedAssetUrl("/hook/private"));
    await waitFor(() => expect(hook.result.current).toBe("blob:hook-1"));
    const pending = Promise.withResolvers<Response>();
    vi.mocked(fetch).mockReturnValueOnce(pending.promise);
    act(() => retirePrivateSessionScope());
    expect(hook.result.current).toBeNull();
    expect(revoked).toContain("blob:hook-1");
    pending.resolve(new Response("current"));
    await waitFor(() => expect(hook.result.current).toBe("blob:hook-2"));
  });

  it("rejects late image state after a path switch", async () => {
    const cached = await getCachedAssetUrl("/hook/B");
    const old = Promise.withResolvers<Response>();
    vi.mocked(fetch).mockReturnValueOnce(old.promise);
    const hook = renderHook(({ path }) => useAuthenticatedAssetUrl(path), {
      initialProps: { path: "/hook/A" },
    });
    hook.rerender({ path: "/hook/B" });
    expect(hook.result.current).toBe(cached);
    old.resolve(new Response("obsolete"));
    await act(async () => {
      await old.promise;
    });
    expect(hook.result.current).toBe(cached);
  });

  it("defers an unadmitted asset", async () => {
    const hook = renderHook(({ admitted }) => useAuthenticatedAssetUrl("/hook/admit", admitted), {
      initialProps: { admitted: false },
    });
    expect(fetch).not.toHaveBeenCalled();
    hook.rerender({ admitted: true });
    await waitFor(() => expect(hook.result.current).toBe("blob:hook-1"));
  });

  it("refetches a mounted image after explicit invalidation", async () => {
    const hook = renderHook(() => useAuthenticatedAssetUrl("/hook/replaced"));
    await waitFor(() => expect(hook.result.current).toBe("blob:hook-1"));
    const pending = Promise.withResolvers<Response>();
    vi.mocked(fetch).mockReturnValueOnce(pending.promise);
    act(() => invalidateCachedAsset("/hook/replaced"));
    expect(hook.result.current).toBeNull();
    pending.resolve(new Response("replacement"));
    await waitFor(() => expect(hook.result.current).toBe("blob:hook-2"));
  });
});
