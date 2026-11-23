/** Generated views replace what is shown only once the latest selection has its bytes. */
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { retirePrivateSessionScope } from "@/lib/auth-store";
import {
  PREVIEW_INTENT_MS,
  previewTransferPolicy,
  useModelPreviews,
} from "@/lib/use-model-previews";
import type { PreviewImage } from "@/types/previews";

const views: PreviewImage[] = [
  ...(["orbit-0", "orbit-1", "orbit-2", "orbit-3"] as const).map((id) => ({
    id,
    role: "orbit" as const,
    url: `/hook-previews/${id}`,
  })),
  { id: "top", role: "top", url: "/hook-previews/top" },
];
const planar: PreviewImage[] = (["face", "reverse", "edge", "top", "bottom"] as const).map(
  (id) => ({ id, role: id, url: `/hook-previews/${id}` }),
);

let responses: Map<string, PromiseWithResolvers<Response>>;
let requested: string[];
function respond(path: string, response = new Response("webp")) {
  const pending = responses.get(path);
  if (!pending) throw new Error(`no request for ${path}`);
  pending.resolve(response);
}
function stubPolicy({ hover, saveData = false }: { hover: boolean; saveData?: boolean }) {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: hover, media: query }));
  Object.defineProperty(navigator, "connection", {
    configurable: true,
    value: { saveData },
  });
}

beforeEach(() => {
  responses = new Map();
  requested = [];
  let next = 0;
  vi.stubGlobal("URL", {
    createObjectURL: () => `blob:preview-${++next}`,
    revokeObjectURL: () => {},
  });
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>((input) => {
      const path = String(input);
      requested.push(path);
      const pending = Promise.withResolvers<Response>();
      responses.set(path, pending);
      return pending.promise;
    }),
  );
  stubPolicy({ hover: true });
});
afterEach(() => {
  cleanup();
  retirePrivateSessionScope();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  Reflect.deleteProperty(navigator, "connection");
});

describe("useModelPreviews", () => {
  it("leaves the cover to its own thumbnail", () => {
    const hook = renderHook(() => useModelPreviews("/cover", views, true));
    expect(hook.result.current.selected).toBe("cover");
    expect(hook.result.current.displayed).toBeNull();
    expect(requested).toEqual([]);
  });

  it("starts with the first view when there is no cover", async () => {
    const hook = renderHook(() => useModelPreviews(null, views, true));
    expect(hook.result.current.selected).toBe("orbit-0");
    await waitFor(() => expect(requested).toContain("/hook-previews/orbit-0"));
    act(() => respond("/hook-previews/orbit-0"));
    await waitFor(() => expect(hook.result.current.displayed?.id).toBe("orbit-0"));
  });

  it("waits for viewport admission before a primary transfer", () => {
    renderHook(() => useModelPreviews(null, views, false));
    expect(requested).toEqual([]);
  });

  it("shows a selected view only after its bytes arrive", async () => {
    const hook = renderHook(() => useModelPreviews("/cover", views, true));
    act(() => hook.result.current.select("orbit-1"));
    await waitFor(() => expect(requested).toContain("/hook-previews/orbit-1"));
    expect(hook.result.current.displayed).toBeNull();
    act(() => respond("/hook-previews/orbit-1"));
    await waitFor(() => expect(hook.result.current.displayed?.id).toBe("orbit-1"));
  });

  it("honors the latest selection when responses finish out of order", async () => {
    const hook = renderHook(() => useModelPreviews("/cover", views, true));
    act(() => hook.result.current.select("orbit-1"));
    act(() => hook.result.current.select("orbit-2"));
    await waitFor(() => expect(responses.has("/hook-previews/orbit-2")).toBe(true));
    act(() => respond("/hook-previews/orbit-2"));
    await waitFor(() => expect(hook.result.current.displayed?.id).toBe("orbit-2"));
    act(() => respond("/hook-previews/orbit-1"));
    await act(async () => {});
    expect(hook.result.current.displayed?.id).toBe("orbit-2");
  });

  it("drops a retained view when the cover is selected again", async () => {
    const hook = renderHook(() => useModelPreviews("/cover", views, true));
    act(() => hook.result.current.select("orbit-1"));
    await waitFor(() => expect(responses.has("/hook-previews/orbit-1")).toBe(true));
    act(() => respond("/hook-previews/orbit-1"));
    await waitFor(() => expect(hook.result.current.displayed?.id).toBe("orbit-1"));
    act(() => hook.result.current.select("cover"));
    expect(hook.result.current.selected).toBe("cover");
    expect(hook.result.current.displayed).toBeNull();
  });

  it("keeps the cover after an abandoned view finishes loading", async () => {
    const hook = renderHook(() => useModelPreviews("/cover", views, true));
    act(() => hook.result.current.select("orbit-1"));
    await waitFor(() => expect(responses.has("/hook-previews/orbit-1")).toBe(true));
    act(() => hook.result.current.select("cover"));
    act(() => respond("/hook-previews/orbit-1"));
    await act(async () => {});
    expect(hook.result.current.selected).toBe("cover");
    expect(hook.result.current.displayed).toBeNull();
  });

  it("reports a failed view as retryable", async () => {
    const hook = renderHook(() => useModelPreviews("/cover", views, true));
    act(() => hook.result.current.select("orbit-1"));
    await waitFor(() => expect(responses.has("/hook-previews/orbit-1")).toBe(true));
    act(() => respond("/hook-previews/orbit-1", new Response("no", { status: 503 })));
    await waitFor(() => expect(hook.result.current.failed).toBe(true));
    responses.delete("/hook-previews/orbit-1");
    act(() => hook.result.current.retry());
    await waitFor(() => expect(responses.has("/hook-previews/orbit-1")).toBe(true));
    expect(hook.result.current.failed).toBe(false);
    act(() => respond("/hook-previews/orbit-1"));
    await waitFor(() => expect(hook.result.current.displayed?.id).toBe("orbit-1"));
  });

  it("keeps the first orbit after a distinct cover", () => {
    const hook = renderHook(() => useModelPreviews("/cover", views, true));
    expect(hook.result.current.destination(1)?.id).toBe("orbit-0");
    expect(hook.result.current.destination(-1)?.id).toBe("top");
  });

  it.each([
    ["orbit-0", -1],
    ["top", 1],
  ] as const)("returns from %s to the cover in direction %s", (id, delta) => {
    const hook = renderHook(() => useModelPreviews("/cover", views, true));
    act(() => hook.result.current.select(id));
    expect(hook.result.current.destination(delta)?.id).toBe("cover");
  });

  it("retains the complete orbit when there is no cover", () => {
    const hook = renderHook(() => useModelPreviews(null, views, true));
    expect(hook.result.current.selected).toBe("orbit-0");
    expect(hook.result.current.destination(-1)?.id).toBe("top");
    act(() => hook.result.current.select("top"));
    expect(hook.result.current.destination(1)?.id).toBe("orbit-0");
  });

  it("does not prefetch when the next slide is the cover", async () => {
    const hook = renderHook(() => useModelPreviews("/cover", views, true));
    act(() => hook.result.current.select("top"));
    act(() => hook.result.current.prioritize(hook.result.current.destination(1)));
    await waitFor(() => expect(requested).toEqual(["/hook-previews/top"]));
  });

  it("omits the orbit image that already serves as the cover", () => {
    const hook = renderHook(() => useModelPreviews("/cover", views, true, "orbit-1"));
    expect(hook.result.current.navigationViews.map(({ id }) => id)).toEqual([
      "cover",
      "orbit-2",
      "orbit-3",
      "orbit-0",
      "top",
    ]);
  });

  it("has no navigation without generated views", () => {
    const hook = renderHook(() => useModelPreviews("/cover", [], true));
    expect(hook.result.current.destination(1)).toBeUndefined();
    expect(hook.result.current.destination(-1)).toBeUndefined();
  });

  it("includes all planar views in the cover cycle", () => {
    const hook = renderHook(() => useModelPreviews("/cover", planar, true));
    expect(hook.result.current.destination(-1)?.id).toBe("bottom");
    for (const id of ["face", "reverse", "edge", "top", "bottom", "cover"] as const) {
      expect(hook.result.current.destination(1)?.id).toBe(id);
      act(() => hook.result.current.select(id));
    }
    expect(hook.result.current.selected).toBe("cover");
  });

  it("wraps planar navigation without a cover", () => {
    const hook = renderHook(() => useModelPreviews(null, planar, true));
    expect(hook.result.current.selected).toBe("face");
    expect(hook.result.current.destination(-1)?.id).toBe("bottom");
    act(() => hook.result.current.select("bottom"));
    expect(hook.result.current.destination(1)?.id).toBe("face");
  });

  it("fetches only the selected planar view while saving data", async () => {
    stubPolicy({ hover: true, saveData: true });
    const hook = renderHook(() => useModelPreviews("/cover", planar, true));
    act(() => hook.result.current.select("edge"));
    await waitFor(() => expect(requested).toEqual(["/hook-previews/edge"]));
  });

  it("warms both neighbors after a sustained hover", async () => {
    vi.useFakeTimers();
    const hook = renderHook(() => useModelPreviews("/cover", views, true));
    act(() => hook.result.current.hover());
    act(() => vi.advanceTimersByTime(PREVIEW_INTENT_MS - 1));
    expect(requested).toEqual([]);
    act(() => vi.advanceTimersByTime(1));
    // At most one speculative transfer runs at a time; the other neighbor follows.
    expect(requested).toEqual(["/hook-previews/orbit-0"]);
    vi.useRealTimers();
    act(() => respond("/hook-previews/orbit-0"));
    await waitFor(() =>
      expect(requested).toEqual(["/hook-previews/orbit-0", "/hook-previews/top"]),
    );
  });

  it("ignores a fleeting hover", () => {
    vi.useFakeTimers();
    const hook = renderHook(() => useModelPreviews("/cover", views, true));
    act(() => hook.result.current.hover());
    act(() => hook.result.current.cancelIntent());
    act(() => vi.advanceTimersByTime(PREVIEW_INTENT_MS * 2));
    expect(requested).toEqual([]);
  });

  it("warms nothing on hover without a fine pointer", () => {
    vi.useFakeTimers();
    stubPolicy({ hover: false });
    const hook = renderHook(() => useModelPreviews("/cover", views, true));
    act(() => hook.result.current.hover());
    act(() => hook.result.current.prioritize(views[1]));
    act(() => vi.advanceTimersByTime(PREVIEW_INTENT_MS * 2));
    expect(requested).toEqual([]);
  });

  it("warms one neighbor after a deliberate selection", async () => {
    stubPolicy({ hover: false });
    const hook = renderHook(() => useModelPreviews("/cover", views, true));
    act(() => hook.result.current.select("orbit-1"));
    await waitFor(() =>
      expect(requested.sort()).toEqual(["/hook-previews/orbit-1", "/hook-previews/orbit-2"]),
    );
  });

  it("fetches only the selected view while saving data", async () => {
    stubPolicy({ hover: true, saveData: true });
    const hook = renderHook(() => useModelPreviews("/cover", views, true));
    act(() => hook.result.current.select("orbit-1"));
    await waitFor(() => expect(requested).toEqual(["/hook-previews/orbit-1"]));
  });

  it("prioritizes an arrow destination without a second transfer", async () => {
    const hook = renderHook(() => useModelPreviews("/cover", views, true));
    act(() => hook.result.current.prioritize(views[1]));
    act(() => hook.result.current.prioritize(views[1]));
    await waitFor(() => expect(requested).toEqual(["/hook-previews/orbit-1"]));
  });
});

describe("previewTransferPolicy", () => {
  it.each([
    [{ hover: true }, { hover: true, neighbor: true }],
    [{ hover: false }, { hover: false, neighbor: true }],
    [
      { hover: true, saveData: true },
      { hover: false, neighbor: false },
    ],
  ])("derives transfers from %o", (policy, expected) => {
    stubPolicy(policy);
    expect(previewTransferPolicy()).toEqual(expected);
  });
});
