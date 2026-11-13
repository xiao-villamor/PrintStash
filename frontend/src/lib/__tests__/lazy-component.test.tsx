/** Obsolete lazy chunks recover once; repeated failures surface and unrelated successful imports cannot reset the recovery budget. */
import { Suspense } from "react";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { lazyImport, recoverRouteChunk } from "@/lib/lazy-component";

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
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

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
  it("keeps recovery bounded across successful imports", async () => {
    flags.set("chunk-reload", "1");
    const Successful = lazyImport(async () => ({ default: () => <span>usable form</span> }));
    render(Successful);
    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(render(Successful)).toContain("usable form");
    const Failed = lazyImport(() => Promise.reject(new Error("missing later chunk")));

    render(Failed);
    await new Promise<void>((resolve) => setImmediate(resolve));

    expect(render(Failed)).toContain("missing later chunk");
    expect(reload).not.toHaveBeenCalled();
  });

  it.each(["getter", "removeItem"])(
    "renders a successful chunk when retry storage %s is blocked",
    async (failure) => {
      const denied = () => {
        throw new DOMException("storage denied", "SecurityError");
      };
      flags.set("chunk-reload", "1");
      if (failure === "getter") {
        Object.defineProperty(globalThis, "sessionStorage", { configurable: true, get: denied });
      } else {
        vi.spyOn(sessionStorage, "removeItem").mockImplementation(denied);
      }
      const Component = lazyImport(async () => ({ default: () => <span>usable form</span> }));

      render(Component);
      await new Promise<void>((resolve) => setImmediate(resolve));

      expect(render(Component)).toContain("usable form");
      expect(render(Component)).not.toContain("storage denied");
      expect(reload).not.toHaveBeenCalled();
    },
  );

  it.each([
    { failure: "getter", errorName: "SecurityError" },
    { failure: "getItem", errorName: "SecurityError" },
    { failure: "setItem", errorName: "SecurityError" },
    { failure: "setItem", errorName: "QuotaExceededError" },
  ])(
    "preserves a failed chunk when retry storage $failure throws $errorName",
    async ({ failure, errorName }) => {
      const denied = () => {
        throw new DOMException("storage denied", errorName);
      };
      if (failure === "getter") {
        Object.defineProperty(globalThis, "sessionStorage", { configurable: true, get: denied });
      } else if (failure === "getItem") {
        vi.spyOn(sessionStorage, "getItem").mockImplementation(denied);
      } else {
        vi.spyOn(sessionStorage, "setItem").mockImplementation(denied);
      }
      const Component = lazyImport(() => Promise.reject(new Error("original chunk failure")));

      render(Component);
      await new Promise<void>((resolve) => setImmediate(resolve));

      expect(render(Component)).toContain("original chunk failure");
      expect(render(Component)).not.toContain("storage denied");
      expect(reload).not.toHaveBeenCalled();
      expect(flags.has("chunk-reload")).toBe(false);
    },
  );
});

describe("recoverRouteChunk", () => {
  it.each([
    { label: "Chromium", message: "Failed to fetch dynamically imported module: /assets/route.js" },
    { label: "Safari", message: "Importing a module script failed." },
    { label: "Firefox", message: "error loading dynamically imported module" },
  ])("reloads a missing automatic route chunk in $label", ({ message }) => {
    recoverRouteChunk(new TypeError(message));

    expect(reload).toHaveBeenCalledTimes(1);
    expect(flags.get("chunk-reload")).toBe("1");
  });
  it("shares the recovery budget with deferred page chunks", () => {
    flags.set("chunk-reload", "1");

    recoverRouteChunk(
      new TypeError("Failed to fetch dynamically imported module: /assets/route.js"),
    );

    expect(reload).not.toHaveBeenCalled();
  });
  it("leaves a screen render error to the route error boundary", () => {
    recoverRouteChunk(new Error("invalid model response"));

    expect(reload).not.toHaveBeenCalled();
    expect(flags.has("chunk-reload")).toBe(false);
  });
});
