/** STL parsing owns a disposable worker and transfers the downloaded buffer. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { parseStl, type StlParserWorker, type StlParseReply } from "../stl-parser";

class ParserWorker implements StlParserWorker {
  onmessage: ((event: MessageEvent<StlParseReply>) => void) | null = null;
  onerror: ((event: ErrorEvent) => void) | null = null;
  terminated = 0;
  buffer: ArrayBuffer | null = null;
  transfer: Transferable[] = [];
  postMessage(buffer: ArrayBuffer, transfer: Transferable[]) {
    this.buffer = buffer;
    this.transfer = transfer;
  }
  terminate() {
    this.terminated += 1;
  }
  complete() {
    const reply: StlParseReply = {
      state: "parsed",
      positions: new Float32Array(9),
      normals: new Float32Array(9),
    };
    this.onmessage?.(new MessageEvent("message", { data: reply }));
  }
}

afterEach(() => vi.unstubAllGlobals());

describe("parseStl", () => {
  it("transfers source bytes to its worker", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(new Response(new Uint8Array([1, 2, 3]))),
    );
    const worker = new ParserWorker();
    const promise = parseStl("blob:source", new AbortController().signal, () => worker);
    await vi.waitFor(() => expect(worker.buffer).not.toBeNull());
    expect(worker.transfer).toEqual([worker.buffer]);
    worker.complete();
    await expect(promise).resolves.toMatchObject({ state: "parsed" });
    expect(worker.terminated).toBe(1);
  });
  it("terminates parsing on cancellation", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(new Response(new Uint8Array([1]))),
    );
    const worker = new ParserWorker();
    const controller = new AbortController();
    const promise = parseStl("blob:source", controller.signal, () => worker);
    await vi.waitFor(() => expect(worker.buffer).not.toBeNull());
    controller.abort();
    await expect(promise).rejects.toThrow("STL parsing cancelled");
    expect(worker.terminated).toBe(1);
    expect(worker.onmessage).toBeNull();
  });
  it("releases the worker after native failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(new Response(new Uint8Array([1]))),
    );
    const worker = new ParserWorker();
    const promise = parseStl("blob:source", new AbortController().signal, () => worker);
    await vi.waitFor(() => expect(worker.buffer).not.toBeNull());
    worker.onerror?.(new ErrorEvent("error"));
    await expect(promise).rejects.toThrow("stl_worker_failed");
    expect(worker.terminated).toBe(1);
  });
  it("refuses failed downloads before creating a worker", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 403 })),
    );
    const createWorker = vi.fn<() => StlParserWorker>();
    await expect(parseStl("/denied", new AbortController().signal, createWorker)).rejects.toThrow(
      "stl_download_failed:403",
    );
    expect(createWorker).not.toHaveBeenCalled();
  });
});
