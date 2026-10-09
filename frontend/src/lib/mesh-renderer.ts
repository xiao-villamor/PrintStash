/** Mesh-only renderer lifecycle. The G-code viewer retains its WebGL Canvas. */
import { WebGLRenderer, WebGLRenderTarget, Vector2, type Camera, type Scene } from "three";
import type { WebGPURenderer } from "three/webgpu";
import type { MeshRendererPreference, ScreenshotScale } from "./preview-preferences";
import {
  screenshotDimensions,
  screenshotHasForeground,
  visibleCanvasBackground,
} from "./thumbnail-camera";

export type MeshBackend = "webgl" | "webgpu";
export type RendererFallback =
  | "insecure_context"
  | "webgpu_unavailable"
  | "initialization_failed"
  | "device_lost";
export interface CapturedMesh {
  width: number;
  height: number;
  pixels: Uint8ClampedArray<ArrayBuffer>;
}
export interface MeshRenderer {
  renderer: WebGLRenderer | WebGPURenderer;
  backend: MeshBackend;
  fallback: RendererFallback | null;
  maxTextureSize: number;
  capture(scene: Scene, camera: Camera, scale: ScreenshotScale): Promise<CapturedMesh>;
  dispose(): Promise<void>;
}

export interface BackendSelection {
  backend: MeshBackend;
  fallback: RendererFallback | null;
}

export function selectMeshBackend(
  preference: MeshRendererPreference,
  secure: boolean,
  available: boolean,
): BackendSelection {
  if (preference === "webgl") return { backend: "webgl", fallback: null };
  if (!secure) return { backend: "webgl", fallback: "insecure_context" };
  if (!available) return { backend: "webgl", fallback: "webgpu_unavailable" };
  return { backend: "webgpu", fallback: null };
}

/** WebGPU readback starts at the top; WebGL starts at the bottom. */
export function normalizeCaptureRows(
  pixels: Uint8Array,
  width: number,
  height: number,
  backend: MeshBackend,
): Uint8ClampedArray<ArrayBuffer> {
  const rowBytes = width * 4;
  const alignedStride = Math.ceil(rowBytes / 256) * 256;
  const stride =
    backend === "webgpu" && pixels.length !== rowBytes * height ? alignedStride : rowBytes;
  if (pixels.length !== (height - 1) * stride + rowBytes)
    throw new Error("screenshot_readback_incomplete");
  const output = new Uint8ClampedArray(rowBytes * height);
  for (let y = 0; y < height; y += 1) {
    const source = (backend === "webgl" ? height - y - 1 : y) * stride;
    output.set(pixels.subarray(source, source + rowBytes), y * rowBytes);
  }
  return output;
}

function rememberRenderTarget(renderer: WebGLRenderer | WebGPURenderer): () => void {
  if (renderer instanceof WebGLRenderer) {
    const target = renderer.getRenderTarget();
    return () => renderer.setRenderTarget(target);
  }
  const target = renderer.getRenderTarget();
  return () => renderer.setRenderTarget(target);
}

export async function createMeshRenderer(
  canvas: HTMLCanvasElement,
  preference: MeshRendererPreference,
  signal: AbortSignal,
  onDeviceLost: () => void,
): Promise<MeshRenderer> {
  signal.throwIfAborted();
  let selection = selectMeshBackend(preference, window.isSecureContext, "gpu" in navigator);
  let renderer: WebGLRenderer | WebGPURenderer;
  let device: GPUDevice | null = null;
  let maxTextureSize: number;
  if (selection.backend === "webgpu") {
    const { WebGPURenderer, WebGPUBackend } = await import("three/webgpu");
    signal.throwIfAborted();
    const adapter = await navigator.gpu.requestAdapter({ powerPreference: "high-performance" });
    if (adapter === null) throw new Error("webgpu_adapter_unavailable");
    device = await adapter.requestDevice();
    if (signal.aborted) {
      device.destroy();
      throw new DOMException("Renderer initialization cancelled", "AbortError");
    }
    try {
      renderer = new WebGPURenderer({ canvas, device, antialias: true, alpha: true });
    } catch (error) {
      device.destroy();
      throw error;
    }
    try {
      await renderer.init();
    } catch (error) {
      try {
        await renderer.dispose();
      } finally {
        device.destroy();
      }
      throw error;
    }
    if (renderer.backend instanceof WebGPUBackend) {
      maxTextureSize = device.limits.maxTextureDimension2D;
    } else {
      selection = { backend: "webgl", fallback: "initialization_failed" };
      const context = canvas.getContext("webgl2");
      if (!context) {
        try {
          await renderer.dispose();
        } finally {
          device.destroy();
        }
        throw new Error("webgl_fallback_unavailable");
      }
      maxTextureSize = Number(context.getParameter(context.MAX_TEXTURE_SIZE));
      device.destroy();
      device = null;
    }
  } else {
    renderer = new WebGLRenderer({
      canvas,
      antialias: true,
      alpha: true,
      powerPreference: "high-performance",
    });
    maxTextureSize = renderer.capabilities.maxTextureSize;
  }
  const activeRenderer = renderer;
  let closed = false;
  let capture: Promise<CapturedMesh> | null = null;
  const nativeDispose = activeRenderer.dispose.bind(activeRenderer);
  let disposal: Promise<void> | null = null;
  const dispose = (): Promise<void> => {
    if (disposal !== null) return disposal;
    closed = true;
    disposal = (async () => {
      // Captures own render targets through readback, including rejection.
      if (capture !== null) await capture.catch(() => undefined);
      try {
        await nativeDispose();
      } finally {
        device?.destroy();
      }
    })();
    return disposal;
  };
  // Fiber also owns factory-created renderers. Both callers share one cleanup,
  // which keeps readback targets alive until their pending native work ends.
  activeRenderer.dispose = dispose;
  if (device !== null) {
    void device.lost.then(() => {
      if (!closed && !signal.aborted) onDeviceLost();
    });
  }
  if (signal.aborted) {
    await dispose();
    throw new DOMException("Renderer initialization cancelled", "AbortError");
  }
  return {
    renderer: activeRenderer,
    ...selection,
    maxTextureSize,
    dispose,
    async capture(scene, camera, scale) {
      if (closed || signal.aborted) throw new Error("renderer_disposed");
      if (capture !== null) throw new Error("screenshot_in_progress");
      const size = activeRenderer.getSize(new Vector2());
      const dimensions = screenshotDimensions(size.x, size.y, scale, maxTextureSize);
      const target = new WebGLRenderTarget(dimensions.width, dimensions.height, {
        depthBuffer: true,
        stencilBuffer: false,
      });
      target.texture.colorSpace = activeRenderer.outputColorSpace;
      const restoreTarget = rememberRenderTarget(activeRenderer);
      const previousBackground = scene.background;
      const background = visibleCanvasBackground(canvas);
      try {
        if (background) scene.background = background;
        activeRenderer.setRenderTarget(target);
        activeRenderer.render(scene, camera);
      } catch (error) {
        target.dispose();
        throw error;
      } finally {
        scene.background = previousBackground;
        restoreTarget();
      }
      capture = (async () => {
        try {
          let pixels: Uint8Array;
          if (activeRenderer instanceof WebGLRenderer) {
            pixels = new Uint8Array(dimensions.width * dimensions.height * 4);
            await activeRenderer.readRenderTargetPixelsAsync(
              target,
              0,
              0,
              dimensions.width,
              dimensions.height,
              pixels,
            );
          } else {
            const values = await activeRenderer.readRenderTargetPixelsAsync(
              target,
              0,
              0,
              dimensions.width,
              dimensions.height,
            );
            if (!(values instanceof Uint8Array)) throw new Error("screenshot_pixel_format_invalid");
            pixels = values;
          }
          if (!screenshotHasForeground(pixels)) throw new Error("screenshot_empty");
          if (closed || signal.aborted) throw new Error("renderer_disposed");
          return {
            ...dimensions,
            pixels: normalizeCaptureRows(
              pixels,
              dimensions.width,
              dimensions.height,
              selection.backend,
            ),
          };
        } finally {
          target.dispose();
        }
      })();
      try {
        return await capture;
      } finally {
        capture = null;
      }
    },
  };
}
