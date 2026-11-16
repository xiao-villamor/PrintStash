/** Moving between a card's image and its overlaid controls is one continuous hover. */
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useModelPreviewCard } from "@/lib/use-model-preview-card";
import { NO_PREVIEWS, type ArtifactPreviews } from "@/types/previews";

const previews: ArtifactPreviews = {
  kind: "artifact",
  cover_camera: "unknown",
  file_id: 3,
  state: "ready",
  reason: null,
  published: {
    profile: "orbit",
    manifest_url: "/m",
    views: [
      { id: "orbit-0", role: "orbit", url: "/m/orbit-0" },
      { id: "orbit-1", role: "orbit", url: "/m/orbit-1" },
    ],
  },
};

function card() {
  document.body.innerHTML = `
    <article id="card">
      <a href="/models/1"><div data-model-preview="Bracket"><img alt="Bracket" /></div></a>
      <div data-model-preview-controls="Bracket"><button>Next</button></div>
      <h2>Bracket</h2>
    </article>
    <article id="other"><div data-model-preview="Other"></div></article>`;
  const query = (selector: string) => document.querySelector(selector)!;
  return {
    frame: query("#card [data-model-preview]"),
    arrow: query("#card button"),
    title: query("#card h2"),
    otherFrame: query("#other [data-model-preview]"),
  };
}
function pointer(from: Element, to: Element | null) {
  return { currentTarget: from, relatedTarget: to };
}

beforeEach(() => {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: true, media: query }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("useModelPreviewCard", () => {
  it("lists only published views", () => {
    const empty = renderHook(() => useModelPreviewCard("/cover", NO_PREVIEWS));
    expect(empty.result.current[1].views).toEqual([]);
    const ready = renderHook(() => useModelPreviewCard("/cover", previews));
    expect(ready.result.current[1].views.map((view) => view.id)).toEqual(["orbit-0", "orbit-1"]);
  });

  it("keeps the hover while the pointer moves onto the card's controls", () => {
    const { frame, arrow } = card();
    const hook = renderHook(() => useModelPreviewCard("/cover", previews));
    act(() => hook.result.current[1].enter());
    act(() => hook.result.current[1].leave(pointer(frame, arrow)));
    expect(hook.result.current[1].hovered).toBe(true);
  });

  it.each([
    ["the card's text", "title"],
    ["another card", "otherFrame"],
    ["outside the page", null],
  ] as const)("ends the hover when the pointer moves to %s", (_, target) => {
    const elements = card();
    const hook = renderHook(() => useModelPreviewCard("/cover", previews));
    act(() => hook.result.current[1].enter());
    act(() =>
      hook.result.current[1].leave(
        pointer(elements.arrow, target === null ? null : elements[target]),
      ),
    );
    expect(hook.result.current[1].hovered).toBe(false);
  });

  it("opens the gallery on request", () => {
    const hook = renderHook(() => useModelPreviewCard("/cover", previews));
    act(() => hook.result.current[1].setOpen(true));
    expect(hook.result.current[1].open).toBe(true);
  });
});
