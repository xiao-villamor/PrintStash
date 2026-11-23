// @vitest-environment node
import { afterEach, beforeAll, expect, it, vi } from "vitest";
import { workerVaultNetwork, type VaultWorkerRequest } from "../vault-worker-transport.ts";
import { UploadRequest } from "./support/upload-request.ts";

type WorkerMessage = (event: MessageEvent<VaultWorkerRequest>) => void;
let receive: WorkerMessage;

beforeAll(async () => {
  vi.stubGlobal("addEventListener", (name: string, listener: WorkerMessage) => {
    if (name === "message") receive = listener;
  });
  await import("../vault-network-worker.ts");
  vi.unstubAllGlobals();
});

afterEach(() => vi.unstubAllGlobals());

class WorkerConnection extends EventTarget {
  postMessage(data: VaultWorkerRequest, ports: MessagePort[]) {
    receive(new MessageEvent("message", { data, ports }));
  }
}

const url = "http://192.168.0.5:3475/api/v1/health";

it("preserves request options, response metadata and the manual redirect signal", async () => {
  const fetch = vi.fn<typeof globalThis.fetch>();
  vi.stubGlobal("fetch", fetch);
  const redirect = Response.error();
  Object.defineProperty(redirect, "type", { value: "opaqueredirect" });
  const followed = Response.json({ status: "ok" });
  Object.defineProperties(followed, {
    redirected: { value: true },
    url: { value: "https://192.168.0.5:3475/api/v1/health" },
  });
  fetch.mockResolvedValueOnce(redirect).mockResolvedValueOnce(followed);

  const request = workerVaultNetwork(() => new WorkerConnection());
  const result = await request(url, { redirect: "manual", credentials: "omit", cache: "no-store" });
  expect(result.type).toBe("opaqueredirect");
  expect(fetch).toHaveBeenCalledWith(
    url,
    expect.objectContaining({ redirect: "manual", credentials: "omit", cache: "no-store" }),
  );
  const response = await request(url, { headers: { Authorization: "Bearer paired-browser" } });
  expect(response.url).toBe("https://192.168.0.5:3475/api/v1/health");
  expect(response.redirected).toBe(true);
  expect(response.headers.get("Content-Type")).toBe("application/json");
  expect(await response.json()).toEqual({ status: "ok" });
  expect(fetch).toHaveBeenLastCalledWith(
    url,
    expect.objectContaining({
      credentials: "include",
      headers: [["authorization", "Bearer paired-browser"]],
    }),
  );
});

it("reconstructs multipart data with the original filename and handles empty responses", async () => {
  const fetch = vi.fn<typeof globalThis.fetch>(async (_url, init) => {
    const form = init?.body;
    expect(form).toBeInstanceOf(FormData);
    if (!(form instanceof FormData)) throw new Error("Expected multipart body");
    expect(form.getAll("tag")).toEqual(["one", "two"]);
    const file = form.get("file");
    if (!(file instanceof File)) throw new Error("Expected the attached file");
    expect(file.name).toBe("part.stl");
    expect(file.type).toBe("model/stl");
    expect(await file.text()).toBe("bytes");
    return new Response(null, { status: 204 });
  });
  vi.stubGlobal("fetch", fetch);
  const form = new FormData();
  form.append("tag", "one");
  form.append("tag", "two");
  form.append("file", new Blob(["bytes"], { type: "model/stl" }), "part.stl");

  const request = workerVaultNetwork(() => new WorkerConnection());
  const response = await request(url, { method: "POST", body: form });
  expect(response.status).toBe(204);
  expect(response.body).toBeNull();
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("propagates network failures and aborts an in-flight request on the worker", async () => {
  const fetch = vi
    .fn<typeof globalThis.fetch>()
    .mockRejectedValueOnce(new TypeError("Failed to fetch"));
  vi.stubGlobal("fetch", fetch);
  const request = workerVaultNetwork(() => new WorkerConnection());
  await expect(request(url)).rejects.toThrow("Failed to fetch");

  const controller = new AbortController();
  let observeAbort: () => void = () => {};
  const workerAborted = new Promise<void>((resolve) => {
    observeAbort = resolve;
  });
  fetch.mockImplementationOnce(
    (_url, init) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          observeAbort();
          reject(init.signal?.reason);
        });
      }),
  );
  const pending = request(url, { signal: controller.signal });
  controller.abort();
  await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  await workerAborted;
  await expect(request(url, { signal: controller.signal })).rejects.toMatchObject({
    name: "AbortError",
  });
  expect(fetch).toHaveBeenCalledTimes(2);
});

it("reports upload progress before returning the server acknowledgement", async () => {
  vi.stubGlobal("XMLHttpRequest", UploadRequest);
  vi.stubGlobal(
    "ProgressEvent",
    class extends Event {
      constructor(type: string, init: ProgressEventInit) {
        super(type);
        Object.assign(this, init);
      }
    },
  );
  const fetch = vi
    .fn<typeof globalThis.fetch>()
    .mockResolvedValue(new Response("full", { status: 507 }));
  vi.stubGlobal("fetch", fetch);
  const request = workerVaultNetwork(() => new WorkerConnection());
  const progress = vi.fn();
  const response = await request(
    url,
    {
      method: "PUT",
      body: new Blob(["bytes"]),
      headers: { Authorization: "Bearer paired-browser" },
    },
    progress,
  );
  expect(progress).toHaveBeenCalledExactlyOnceWith(5, 5);
  expect(response.status).toBe(507);
  expect(await response.text()).toBe("full");
  expect(UploadRequest.instances[0].withCredentials).toBe(true);
  expect(UploadRequest.instances[0].headers.get("authorization")).toBe("Bearer paired-browser");
});

it("rejects a worker loading failure and creates a fresh worker for the next call", async () => {
  const broken = new WorkerConnection();
  vi.spyOn(broken, "postMessage").mockImplementation(() => {});
  const createWorker = vi.fn(() => new WorkerConnection()).mockReturnValueOnce(broken);
  const request = workerVaultNetwork(createWorker);
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof globalThis.fetch>().mockResolvedValue(Response.json({ ok: true })),
  );
  const failed = request(url);
  broken.dispatchEvent(new Event("error"));
  await expect(failed).rejects.toThrow("network worker failed");
  await expect((await request(url)).json()).resolves.toEqual({ ok: true });
  expect(createWorker).toHaveBeenCalledTimes(2);
});
