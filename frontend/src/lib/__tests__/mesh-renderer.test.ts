/** Backend eligibility and readback layout preserve the browser compatibility path. */
import { describe, expect, it } from "vitest";
import { initializeGpuRenderer, normalizeCaptureRows, selectMeshBackend } from "../mesh-renderer";

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

describe("initializeGpuRenderer", () => {
  it.each([
    {
      name: "constructor refusal",
      constructionFails: true,
      disposalFails: false,
      error: "construction_failed",
    },
    {
      name: "initialization refusal",
      constructionFails: false,
      disposalFails: false,
      error: "initialization_failed",
    },
    {
      name: "cleanup refusal",
      constructionFails: false,
      disposalFails: true,
      error: "cleanup_failed",
    },
  ])("releases the acquired device after $name", async (failure) => {
    let destroyed = false;
    const device = {
      destroy: () => {
        destroyed = true;
      },
    };
    const create = () => {
      if (failure.constructionFails) throw new Error("construction_failed");
      return {
        init: async () => {
          throw new Error("initialization_failed");
        },
        dispose: async () => {
          if (failure.disposalFails) throw new Error("cleanup_failed");
        },
      };
    };

    await expect(initializeGpuRenderer(device, create)).rejects.toThrow(failure.error);

    expect(destroyed).toBe(true);
  });

  it("retains the device after successful initialization", async () => {
    let destroyed = false;
    const device = {
      destroy: () => {
        destroyed = true;
      },
    };
    const renderer = { init: async () => undefined, dispose: () => undefined };

    const initialized = await initializeGpuRenderer(device, () => renderer);

    expect(initialized).toBe(renderer);
    expect(destroyed).toBe(false);
  });
});
