/** Geometry belongs to the current source and is released on replacement or unmount. */
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useParsedStl } from "../use-parsed-stl";
import type { StlParserWorker, StlParseReply } from "../stl-parser";

class ParserWorker implements StlParserWorker {
  onmessage: ((event: MessageEvent<StlParseReply>) => void) | null = null;
  onerror: ((event: ErrorEvent) => void) | null = null;
  terminated = false;
  postMessage() {}
  terminate() {
    this.terminated = true;
  }
  complete(value: number) {
    this.onmessage?.(
      new MessageEvent("message", {
        data: {
          state: "parsed",
          positions: new Float32Array(9).fill(value),
          normals: new Float32Array(9),
        } satisfies StlParseReply,
      }),
    );
  }
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("useParsedStl", () => {
  it("discards obsolete parsing results after source replacement", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockImplementation(async () => new Response(new Uint8Array([1]))),
    );
    const workers: ParserWorker[] = [];
    const createWorker = () => {
      const worker = new ParserWorker();
      workers.push(worker);
      return worker;
    };
    const view = renderHook(({ url }) => useParsedStl(url, createWorker), {
      initialProps: { url: "blob:first" },
    });
    await vi.waitFor(() => expect(workers).toHaveLength(1));
    const oldMessage = workers[0].onmessage;
    view.rerender({ url: "blob:second" });
    await vi.waitFor(() => expect(workers).toHaveLength(2));
    expect(workers[0].terminated).toBe(true);
    await act(async () => workers[1].complete(2));
    const current = view.result.current;
    expect(current.state).toBe("ready");
    await act(async () =>
      oldMessage?.(
        new MessageEvent("message", {
          data: {
            state: "parsed",
            positions: new Float32Array(9).fill(1),
            normals: new Float32Array(9),
          } satisfies StlParseReply,
        }),
      ),
    );
    expect(view.result.current).toBe(current);
    if (current.state !== "ready") throw new Error("expected current geometry");
    expect(current.geometry.getAttribute("position").getX(0)).toBe(2);
  });
  it("releases loaded geometry on unmount", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(new Response(new Uint8Array([1]))),
    );
    const worker = new ParserWorker();
    const createWorker = () => worker;
    const view = renderHook(() => useParsedStl("blob:source", createWorker));
    await vi.waitFor(() => expect(worker.onmessage).not.toBeNull());
    await act(async () => worker.complete(1));
    const result = view.result.current;
    if (result.state !== "ready") throw new Error("expected loaded geometry");
    const dispose = vi.fn<() => void>();
    result.geometry.addEventListener("dispose", dispose);
    view.unmount();
    expect(dispose).toHaveBeenCalledOnce();
  });
});
