// Execute the shipped worker, including its response and lifetime promises.
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { describe, expect, it, vi } from "vitest";

type WorkerEvent = {
  request: Request;
  waitUntil: (promise: Promise<unknown>) => void;
  respondWith: (promise: Promise<Response>) => void;
};
function worker({
  open,
  network,
}: {
  open: () => Promise<{
    match: (key: Request | string) => Promise<Response | undefined>;
    put: (key: Request | string, response: Response) => Promise<void>;
    addAll: (paths: string[]) => Promise<void>;
  }>;
  network: (request: Request, init?: RequestInit) => Promise<Response>;
}) {
  const handlers = new Map<string, (event: WorkerEvent) => void>();
  const deletions: string[] = [];
  runInNewContext(readFileSync(`${process.cwd()}/public/sw.js`, "utf8"), {
    self: {
      location: { origin: "https://printstash.test" },
      addEventListener: (name: string, handler: (event: WorkerEvent) => void) =>
        handlers.set(name, handler),
      skipWaiting: () => {},
      clients: { claim: () => {} },
    },
    caches: {
      open,
      keys: async () => ["printstash-shell-v5", "printstash-shell-v6", "unrelated"],
      delete: async (key: string) => {
        deletions.push(key);
      },
    },
    fetch: network,
    URL,
    Response,
    Promise,
  });
  const invoke = (type: string, path = "/theme-bootstrap.js", headers: HeadersInit = {}) => {
    const lifetimes: Promise<unknown>[] = [];
    let response: Promise<Response> | undefined;
    handlers.get(type)!({
      request: new Request(`https://printstash.test${path}`, { headers }),
      waitUntil: (promise) => {
        lifetimes.push(promise);
      },
      respondWith: (promise) => {
        response = promise;
      },
    });
    return { response, done: () => Promise.all(lifetimes) };
  };
  return { invoke, deletions };
}
const cache = () => ({
  match: vi
    .fn<(key: Request | string) => Promise<Response | undefined>>()
    .mockResolvedValue(undefined),
  put: vi
    .fn<(key: Request | string, response: Response) => Promise<void>>()
    .mockResolvedValue(undefined),
  addAll: vi.fn<(paths: string[]) => Promise<void>>().mockResolvedValue(undefined),
});

describe("bootstrap service worker", () => {
  it.each(["/private-export", "/api/v1/auth/me", "/api/v1/files/1/thumbnail"])(
    "leaves non-shell reads to the browser: %s",
    async (path) => {
      const stored = cache();
      const { response, done } = worker({
        open: async () => stored,
        network: async () => new Response("private bytes"),
      }).invoke("fetch", path);

      await done();

      expect(response).toBeUndefined();
      expect(stored.put).not.toHaveBeenCalled();
    },
  );
  it("leaves authenticated static requests to the browser", async () => {
    const stored = cache();
    const { response, done } = worker({
      open: async () => stored,
      network: async () => new Response("authorized bytes"),
    }).invoke("fetch", "/assets/private.js", { Authorization: "Bearer test-token" });

    await done();

    expect(response).toBeUndefined();
    expect(stored.put).not.toHaveBeenCalled();
  });
  it("delivers bootstrap when cache storage rejects", async () => {
    const { response, done } = worker({
      open: async () => {
        throw new Error("cache unavailable");
      },
      network: async () => new Response("network bootstrap"),
    }).invoke("fetch");

    expect(await (await response!).text()).toBe("network bootstrap");
    await done();
  });
  it.each(["/theme-bootstrap.js", "/locale-shell.js", "/assets/index-hashed.js"])(
    "delivers %s without waiting for Cache Storage",
    async (path) => {
      const open = vi
        .fn<() => Promise<ReturnType<typeof cache>>>()
        .mockReturnValue(new Promise(() => {}));
      const network = async () => new Response("network bootstrap");
      const { response } = worker({ open, network }).invoke("fetch", path);
      expect(await (await response!).text()).toBe("network bootstrap");
    },
  );
  it("keeps valid network bytes when a cache write fails", async () => {
    const stored = cache();
    stored.put.mockRejectedValue(new Error("quota"));
    const { response, done } = worker({
      open: async () => stored,
      network: async () => new Response("network"),
    }).invoke("fetch");
    expect(await (await response!).text()).toBe("network");
    await done();
  });
  it("uses only the named shell cache for an offline bootstrap", async () => {
    const stored = cache();
    stored.match.mockResolvedValue(new Response("offline bootstrap"));
    const open = vi.fn<() => Promise<ReturnType<typeof cache>>>().mockResolvedValue(stored);
    const { response, done } = worker({
      open,
      network: async () => {
        throw new Error("offline");
      },
    }).invoke("fetch");
    expect(await (await response!).text()).toBe("offline bootstrap");
    expect(open).toHaveBeenCalledWith("printstash-shell-v6");
    await done();
  });
  it("reuses HTTP-cached immutable assets during reload", async () => {
    const network = vi.fn(
      async (_request: Request, init?: RequestInit) =>
        new Response(init?.cache === "force-cache" ? "HTTP cached bytes" : "revalidated bytes"),
    );
    const { response, done } = worker({ open: async () => cache(), network }).invoke(
      "fetch",
      "/assets/index-hashed.js",
    );
    expect(await (await response!).text()).toBe("HTTP cached bytes");
    await done();
  });

  it.each(["/theme-bootstrap.js", "/locale-shell.js"])(
    "preserves revalidation for mutable bootstrap: %s",
    async (path) => {
      const network = vi.fn(
        async (_request: Request, init?: RequestInit) =>
          new Response(init?.cache === "force-cache" ? "stale bootstrap" : "current bootstrap"),
      );
      const { response, done } = worker({ open: async () => cache(), network }).invoke(
        "fetch",
        path,
      );
      expect(await (await response!).text()).toBe("current bootstrap");
      await done();
    },
  );

  it("does not rewrite an already cached hashed build asset", async () => {
    const stored = cache();
    stored.match.mockResolvedValue(new Response("cached asset"));
    const { response, done } = worker({
      open: async () => stored,
      network: async () => new Response("HTTP cached asset"),
    }).invoke("fetch", "/assets/index-hashed.js");
    expect(await (await response!).text()).toBe("HTTP cached asset");
    await done();
    expect(stored.put).not.toHaveBeenCalled();
  });
  it("precaches both bootstrap scripts on first installation", async () => {
    const stored = cache();
    await worker({ open: async () => stored, network: async () => new Response() })
      .invoke("install")
      .done();
    expect(stored.addAll).toHaveBeenCalledWith(
      expect.arrayContaining(["/theme-bootstrap.js", "/locale-shell.js"]),
    );
  });
  it("updates the shell without deleting unrelated caches", async () => {
    const runtime = worker({ open: async () => cache(), network: async () => new Response() });
    await runtime.invoke("activate").done();
    expect(runtime.deletions).toEqual(["printstash-shell-v5"]);
  });
});
