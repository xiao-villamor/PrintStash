/** Backend eligibility and readback layout preserve the browser compatibility path. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { createMeshRenderer, normalizeCaptureRows, selectMeshBackend } from "../mesh-renderer";

describe("selectMeshBackend", () => {
  it("retains explicit WebGL preference on capable devices", () => {
    expect(selectMeshBackend("webgl", true, true)).toEqual({ backend: "webgl", fallback: null });
  });
  it.each(["auto", "webgpu"] as const)(
    "uses WebGPU for capable secure contexts with %s",
    (preference) => {
      expect(selectMeshBackend(preference, true, true)).toEqual({
        backend: "webgpu",
        fallback: null,
      });
    },
  );
  it.each(["auto", "webgpu"] as const)("recovers insecure LAN HTTP with %s", (preference) => {
    expect(selectMeshBackend(preference, false, true)).toEqual({
      backend: "webgl",
      fallback: "insecure_context",
    });
  });
  it("recovers absent browser support", () => {
    expect(selectMeshBackend("webgpu", true, false)).toEqual({
      backend: "webgl",
      fallback: "webgpu_unavailable",
    });
  });
});

describe("normalizeCaptureRows", () => {
  it("normalizes WebGL bottom-up pixels", () => {
    const result = normalizeCaptureRows(
      new Uint8Array([1, 2, 3, 255, 4, 5, 6, 255]),
      1,
      2,
      "webgl",
    );
    expect([...result]).toEqual([4, 5, 6, 255, 1, 2, 3, 255]);
  });
  it("preserves WebGPU top-down pixels", () => {
    const result = normalizeCaptureRows(
      new Uint8Array([1, 2, 3, 255, 4, 5, 6, 255]),
      1,
      2,
      "webgpu",
    );
    expect([...result]).toEqual([1, 2, 3, 255, 4, 5, 6, 255]);
  });
  it("removes WebGPU row alignment padding", () => {
    const pixels = new Uint8Array(260);
    pixels.set([1, 2, 3, 255]);
    pixels.set([4, 5, 6, 255], 256);
    expect([...normalizeCaptureRows(pixels, 1, 2, "webgpu")]).toEqual([1, 2, 3, 255, 4, 5, 6, 255]);
  });
  it("refuses truncated readback", () => {
    expect(() => normalizeCaptureRows(new Uint8Array(7), 1, 2, "webgpu")).toThrow(
      "screenshot_readback_incomplete",
    );
  });
});

const initialization = vi.hoisted(() => ({
  constructionFails: false,
  disposalFails: false,
}));
vi.mock("three/webgpu", () => ({
  WebGPURenderer: class {
    constructor() {
      if (initialization.constructionFails) throw new Error("construction_failed");
    }
    async init() {
      throw new Error("initialization_failed");
    }
    async dispose() {
      if (initialization.disposalFails) throw new Error("cleanup_failed");
    }
  },
  WebGPUBackend: class {},
}));

describe("createMeshRenderer", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it.each([
    { name: "constructor refusal", constructionFails: true, disposalFails: false },
    { name: "initialization refusal", constructionFails: false, disposalFails: false },
    { name: "cleanup refusal", constructionFails: false, disposalFails: true },
  ])("releases the acquired device after $name", async (failure) => {
    initialization.constructionFails = failure.constructionFails;
    initialization.disposalFails = failure.disposalFails;
    let destroyed = false;
    const device = {
      destroy: () => {
        destroyed = true;
      },
    };
    vi.stubGlobal("isSecureContext", true);
    vi.stubGlobal("navigator", {
      gpu: { requestAdapter: async () => ({ requestDevice: async () => device }) },
    });

    await expect(
      createMeshRenderer(
        document.createElement("canvas"),
        "webgpu",
        new AbortController().signal,
        () => undefined,
      ),
    ).rejects.toThrow();

    expect(destroyed).toBe(true);
  });
});
