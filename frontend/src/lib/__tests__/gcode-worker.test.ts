/** The disposable parser worker transfers geometry or a bounded, recognizable failure. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ToolpathWorkerReply } from "../gcode-worker-protocol";

interface WorkerScope {
  onmessage: ((event: MessageEvent<{ text: string }>) => void) | null;
  postMessage: ReturnType<
    typeof vi.fn<(message: ToolpathWorkerReply, transfer: Transferable[]) => void>
  >;
}

const boundary: WorkerScope = {
  onmessage: null,
  postMessage: vi.fn<(message: ToolpathWorkerReply, transfer: Transferable[]) => void>(),
};

beforeEach(async () => {
  vi.resetModules();
  boundary.onmessage = null;
  boundary.postMessage.mockReset();
  vi.stubGlobal("self", boundary);
  await import("../gcode-worker");
});
afterEach(() => vi.unstubAllGlobals());

describe("toolpath worker", () => {
  it("transfers all parsed geometry buffers", () => {
    boundary.onmessage?.(new MessageEvent("message", { data: { text: "G1 X10 E1" } }));

    const [reply, transfer] = boundary.postMessage.mock.calls[0];
    expect(reply.kind).toBe("ready");
    expect(transfer).toHaveLength(4);
    expect(transfer.map((buffer) => buffer instanceof ArrayBuffer)).toEqual([
      true,
      true,
      true,
      true,
    ]);
    expect(reply).toMatchObject({ data: { bounds: { sizeX: 10 } } });
  });
  it("reports segment refusal without transferring partial geometry", () => {
    boundary.onmessage?.(
      new MessageEvent("message", { data: { text: "G1 X1000000\nG2 I-1000000 E1" } }),
    );

    expect(boundary.postMessage.mock.calls).toEqual([[{ kind: "error", code: "limit" }, []]]);
  });
  it("reports malformed geometry without transferring buffers", () => {
    boundary.onmessage?.(new MessageEvent("message", { data: { text: "G1 X10\nG2 X-10 R1 E1" } }));

    expect(boundary.postMessage.mock.calls).toEqual([[{ kind: "error", code: "invalid" }, []]]);
  });
});
