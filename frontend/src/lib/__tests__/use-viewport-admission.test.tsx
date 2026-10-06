/** Protected image ownership preserves visible URLs and bounds authorized work. */
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useViewportAssetUrl } from "@/lib/use-viewport-admission";
import { retirePrivateSessionScope } from "@/lib/auth-store";

class FakeObserver {
  static instances: FakeObserver[] = [];
  readonly nodes = new Set<Element>();
  constructor(
    readonly callback: (entries: { target: Element; isIntersecting: boolean }[]) => void,
  ) {
    FakeObserver.instances.push(this);
  }
  observe(node: Element) {
    this.nodes.add(node);
  }
  unobserve(node: Element) {
    this.nodes.delete(node);
  }
  disconnect() {
    this.nodes.clear();
  }
  enter(node: Element) {
    this.callback([{ target: node, isIntersecting: true }]);
  }
}
function Preview() {
  const { ref, url } = useViewportAssetUrl("/viewport/image");
  return (
    <div ref={ref} data-testid="preview">
      {url}
    </div>
  );
}
beforeEach(() => {
  FakeObserver.instances = [];
  vi.stubGlobal("IntersectionObserver", FakeObserver);
  vi.stubGlobal("URL", {
    createObjectURL: () => "blob:viewport",
    revokeObjectURL: vi.fn<(url: string) => void>(),
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
describe("useViewportAssetUrl", () => {
  it("admits an image only near the viewport", async () => {
    render(<Preview />);
    expect(fetch).not.toHaveBeenCalled();
    const node = screen.getByTestId("preview");
    const observer = FakeObserver.instances.at(-1)!;
    expect(observer.nodes.has(node)).toBe(true);
    act(() => observer.enter(node));
    await waitFor(() => expect(node.textContent).toBe("blob:viewport"));
    expect(fetch).toHaveBeenCalledOnce();
    expect(observer.nodes.has(node)).toBe(false);
  });
  it("admits images when intersection observation is unavailable", async () => {
    vi.stubGlobal("IntersectionObserver", undefined);
    render(<Preview />);
    await waitFor(() => expect(screen.getByTestId("preview").textContent).toBe("blob:viewport"));
  });
  it("unobserves a removed thumbnail", () => {
    const page = render(<Preview />);
    const observer = FakeObserver.instances.at(-1)!;
    page.unmount();
    expect(observer.nodes.size).toBe(0);
    expect(fetch).not.toHaveBeenCalled();
  });
});
