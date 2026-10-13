/** Thumbnail completion requires loaded visible image bytes and distinguishes missing derivatives from pending images. */
import { useRef } from "react";
import { act, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useStartupThumbnails } from "@/lib/use-startup-thumbnails";
import { renderApp } from "@/test-support/render";

function Probe({ state }: { state: "pending" | "ready" | "missing" }) {
  const root = useRef<HTMLDivElement>(null);
  useStartupThumbnails(root, true);
  return (
    <div ref={root}>
      <div data-library-thumbnail={state}>
        {state === "ready" && <img alt="Bracket" src="/thumb.webp" />}
      </div>
    </div>
  );
}
function observed() {
  const mark = vi.fn<(name: string) => void>();
  const now = performance.now.bind(performance);
  vi.stubGlobal("performance", { now, mark, getEntriesByName: () => [] });
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 0, 100, 100));
  vi.spyOn(HTMLImageElement.prototype, "complete", "get").mockReturnValue(true);
  vi.spyOn(HTMLImageElement.prototype, "naturalWidth", "get").mockReturnValue(100);
  return mark;
}
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
async function frame() {
  await act(
    async () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
}

describe("startup thumbnail observation", () => {
  it("waits for authenticated bytes before declaring visible thumbnails", async () => {
    const mark = observed();
    const view = renderApp(<Probe state="pending" />);
    await frame();
    expect(mark).not.toHaveBeenCalled();
    view.rerender(<Probe state="ready" />);
    await waitFor(() => expect(mark).toHaveBeenCalledWith("printstash:library-thumbnails"));
  });
  it("keeps missing derivatives separate from a complete visual library", async () => {
    const mark = observed();
    renderApp(<Probe state="missing" />);
    await frame();
    expect(mark).not.toHaveBeenCalled();
  });
  it("does not let an unloaded image satisfy completion", async () => {
    const mark = observed();
    vi.spyOn(HTMLImageElement.prototype, "naturalWidth", "get").mockReturnValue(0);
    renderApp(<Probe state="ready" />);
    await frame();
    expect(mark).not.toHaveBeenCalled();
  });
});
