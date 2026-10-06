/** Obsolete lazy chunks recover once; repeated failures surface and successful imports clear the recovery latch. */
import { Suspense } from "react";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { lazyImport } from "@/lib/lazy-component";

const flags = new Map<string, string>();
const reload = vi.fn<() => void>();
function render(Component: ReturnType<typeof lazyImport>) {
  return renderToString(
    <Suspense fallback="loading">
      <Component />
    </Suspense>,
  );
}
beforeEach(() => {
  flags.clear();
  reload.mockClear();
  vi.stubGlobal("sessionStorage", {
    getItem: (key: string) => flags.get(key) ?? null,
    setItem: (key: string, value: string) => flags.set(key, value),
    removeItem: (key: string) => flags.delete(key),
  });
  vi.stubGlobal("window", { location: { reload } });
});
afterEach(() => vi.unstubAllGlobals());

describe("lazy chunk recovery", () => {
  it("reloads once when a deferred chunk is unavailable", async () => {
    const Component = lazyImport(() => Promise.reject(new Error("missing chunk")));
    render(Component);
    await vi.waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
    expect(flags.get("chunk-reload")).toBe("1");
    expect(render(Component)).toContain("loading");
  });
  it("surfaces a repeated failure without reloading forever", async () => {
    flags.set("chunk-reload", "1");
    const Component = lazyImport(() => Promise.reject(new Error("missing chunk")));
    render(Component);
    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(render(Component)).toContain("missing chunk");
    expect(reload).not.toHaveBeenCalled();
  });
  it("clears the recovery latch after a successful deferred import", async () => {
    flags.set("chunk-reload", "1");
    const Component = lazyImport(async () => ({ default: () => <span>usable form</span> }));
    render(Component);
    await vi.waitFor(() => expect(flags.has("chunk-reload")).toBe(false));
    expect(render(Component)).toContain("usable form");
    expect(reload).not.toHaveBeenCalled();
  });
});
