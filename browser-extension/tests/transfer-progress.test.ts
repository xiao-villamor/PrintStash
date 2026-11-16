import { readMarketplaceFile } from "../marketplace-download.ts";
import { readBoundedPrintablesResponse } from "../printables-capture.ts";
import { readBoundedMakerWorldResponse } from "../makerworld-capture.ts";
import { downloadThingiverseCandidate } from "../thingiverse-capture.ts";
/** Progress reports observed bytes, while server acknowledgement decides transfer success. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { captureTransferFetch, downloadProgress, progressLabel } from "../transfer-progress.ts";
import { UploadRequest } from "./support/upload-request.ts";

describe("transfer progress", () => {
  beforeEach(() => {
    UploadRequest.instances = [];
    vi.stubGlobal("XMLHttpRequest", UploadRequest);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });
  it.each([
    { headers: new Headers({ "Content-Length": "10" }), total: 10 },
    { headers: new Headers(), total: null },
    { headers: new Headers({ "Content-Length": "10", "Content-Encoding": "gzip" }), total: null },
  ])("reports download size truthfully: $headers", ({ headers, total }) => {
    const values: Array<[number, number | null]> = [];
    const report = downloadProgress(new Response("bytes", { headers }), (loaded, size) =>
      values.push([loaded, size]),
    );
    report(5);
    report(15);
    expect(values).toEqual([
      [5, total],
      [15, null],
    ]);
  });
  it("labels unknown size without a percentage", () => {
    expect(progressLabel(1024 * 1024, null)).toBe("1.0 MiB · size unknown");
    expect(progressLabel(512 * 1024, 1024 * 1024)).toBe("0.5 MiB · 50%");
  });
  it("returns the server rejection after all bytes were sent", async () => {
    vi.stubGlobal("fetch", async () => new Response("capacity", { status: 507 }));
    const values: Array<[number, number | null]> = [];
    const transfer = captureTransferFetch((loaded, total) => values.push([loaded, total]));
    const response = await transfer("https://vault.test/slot", {
      method: "PUT",
      body: new Blob(["bytes"]),
      headers: { Authorization: "Bearer test-device", "Content-Type": "model/stl" },
    });
    expect(values).toEqual([[5, 5]]);
    expect(response.status).toBe(507);
    expect(await response.text()).toBe("capacity");
    expect(UploadRequest.instances[0].withCredentials).toBe(false);
    expect(UploadRequest.instances[0].headers.get("Authorization")).toBe("Bearer test-device");
  });
  it("aborts a transfer on connection retirement", async () => {
    vi.stubGlobal("fetch", () => new Promise(() => {}));
    const controller = new AbortController();
    const pending = captureTransferFetch(() => {})("https://vault.test/slot", {
      method: "PUT",
      body: new Blob(["bytes"]),
      signal: controller.signal,
    });
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(UploadRequest.instances[0].controller.signal.aborted).toBe(true);
  });
  it("does not start an already cancelled transfer", async () => {
    await expect(
      captureTransferFetch(() => {})("https://vault.test/slot", {
        method: "PUT",
        body: new Blob(["bytes"]),
        signal: AbortSignal.abort(),
      }),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(UploadRequest.instances).toHaveLength(0);
  });
  it.each(["network", "timeout"])(
    "provides recovery instructions after %s failure",
    async (failure) => {
      vi.stubGlobal("fetch", () => new Promise(() => {}));
      const pending = captureTransferFetch(() => {})("https://vault.test/slot", {
        method: "PUT",
        body: new Blob(["bytes"]),
      });
      if (failure === "network") UploadRequest.instances[0].onerror?.();
      else UploadRequest.instances[0].ontimeout?.();
      await expect(pending).rejects.toThrow("recover confirmed files");
    },
  );
  it("keeps metadata requests on the existing transport", async () => {
    const response = Response.json({ id: 2 });
    const result = await captureTransferFetch(
      () => {
        throw new Error("No upload progress for metadata");
      },
      async () => response,
    )("https://vault.test/capture", { method: "POST", body: "{}" });
    expect(result).toBe(response);
    expect(UploadRequest.instances).toHaveLength(0);
  });
});

describe("provider download byte events", () => {
  it.each(["shared", "printables", "makerworld", "thingiverse"])(
    "reports consumed %s bytes without changing the file",
    async (provider) => {
      const values: Array<[number, number | null]> = [];
      const progress = (loaded: number, total: number | null) => values.push([loaded, total]);
      const response = new Response(
        new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(new Uint8Array([1, 2]));
            controller.enqueue(new Uint8Array([3, 4]));
            controller.close();
          },
        }),
        { headers: { "Content-Length": "4" } },
      );
      const candidate = {
        id: "thingiverse:7401604:file:991001",
        filename: "part.stl",
        fileType: "stl" as const,
      };
      const file =
        provider === "printables"
          ? await readBoundedPrintablesResponse(response, 4, undefined, progress)
          : provider === "makerworld"
            ? await readBoundedMakerWorldResponse(response, 4, 0, undefined, progress)
            : provider === "thingiverse"
              ? (
                  await downloadThingiverseCandidate({
                    candidate,
                    link: "https://www.thingiverse.com/download:991001",
                    progress,
                    fetchImpl: async () => response,
                  })
                ).file
              : (await readMarketplaceFile(response, candidate, "Provider", undefined, progress))
                  .file;
      expect(values).toEqual([
        [2, 4],
        [4, 4],
      ]);
      expect([...new Uint8Array(await file.arrayBuffer())]).toEqual([1, 2, 3, 4]);
    },
  );
});
