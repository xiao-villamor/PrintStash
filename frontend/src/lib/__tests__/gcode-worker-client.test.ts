/** A preview owns one worker and releases it on result, failure or cancellation. */
import { describe, expect, it, vi } from "vitest";
import {
  parseGcodeInWorker,
  type ToolpathWorker,
  type ToolpathWorkerReply,
} from "../gcode-worker-client";
import { parseGcode } from "../gcode";

function workerBoundary() {
  const worker: ToolpathWorker = {
    onmessage: null,
    onerror: null,
    postMessage: vi.fn<ToolpathWorker["postMessage"]>(),
    terminate: vi.fn<ToolpathWorker["terminate"]>(),
  };
  const send = (data: ToolpathWorkerReply) =>
    worker.onmessage?.(new MessageEvent("message", { data }));
  return { worker, send, create: () => worker };
}

describe("Cancelable toolpath parsing", () => {
  it("resolves geometry through the worker", async () => {
    const boundary = workerBoundary();
    const signal = new AbortController().signal;
    const result = parseGcodeInWorker("G1 X10 E1", signal, boundary.create);
    expect(boundary.worker.postMessage).toHaveBeenCalledWith({ text: "G1 X10 E1" });
    boundary.send({ kind: "ready", data: parseGcode("G1 X10 E1") });
    expect((await result).bounds.sizeX).toBe(10);
    expect(boundary.worker.terminate).toHaveBeenCalledTimes(1);
  });
  it("terminates a busy parser when the user leaves the preview", async () => {
    const boundary = workerBoundary();
    const controller = new AbortController();
    const result = parseGcodeInWorker("G1 X10 E1", controller.signal, boundary.create);
    controller.abort();
    await expect(result).rejects.toMatchObject({ name: "AbortError" });
    expect(boundary.worker.terminate).toHaveBeenCalledTimes(1);
  });
  it("does not create a worker for an already canceled request", async () => {
    const create = vi.fn<() => ToolpathWorker>();
    const controller = new AbortController();
    controller.abort();
    await expect(parseGcodeInWorker("", controller.signal, create)).rejects.toMatchObject({
      name: "AbortError",
    });
    expect(create).not.toHaveBeenCalled();
  });
  it("returns a recognizable segment-limit error", async () => {
    const boundary = workerBoundary();
    const result = parseGcodeInWorker("", new AbortController().signal, boundary.create);
    boundary.send({ kind: "error", code: "limit" });
    await expect(result).rejects.toMatchObject({ code: "limit" });
    expect(boundary.worker.terminate).toHaveBeenCalledTimes(1);
  });
  it("removes its abort listener after completion", async () => {
    const boundary = workerBoundary();
    const controller = new AbortController();
    const result = parseGcodeInWorker("", controller.signal, boundary.create);
    boundary.send({ kind: "ready", data: parseGcode("") });
    await result;
    controller.abort();
    expect(boundary.worker.terminate).toHaveBeenCalledTimes(1);
  });
});
