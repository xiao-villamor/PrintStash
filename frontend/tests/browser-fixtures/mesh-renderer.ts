/** Real native renderer checks, isolated from application data and service mocks. */
import {
  BoxGeometry,
  Mesh,
  MeshBasicMaterial,
  OrthographicCamera,
  Scene,
  WebGLRenderer,
} from "three";
import { createMeshRenderer, type CapturedMesh } from "../../src/lib/mesh-renderer";

interface CaptureCheck {
  width: number;
  height: number;
  top: number[];
  bottom: number[];
}
declare global {
  interface Window {
    meshCheck: {
      capture: (scale: 1 | 2 | 3) => Promise<CaptureCheck>;
      recoverCapture: () => Promise<boolean>;
      closeDuringCapture: () => Promise<boolean>;
      close: () => Promise<void>;
    };
  }
}

const canvas = document.querySelector("canvas");
if (canvas === null) throw new Error("missing conformance canvas");
const query = new URLSearchParams(location.search);
const preference = query.get("backend") === "webgpu" ? "webgpu" : "webgl";
const controller = new AbortController();
const adapter = await createMeshRenderer(canvas, preference, controller.signal, () => {
  document.body.dataset.lost = "true";
});
adapter.renderer.setSize(128, 128);
const scene = new Scene();
const geometry = new BoxGeometry(1, 0.6, 0.1);
const red = new MeshBasicMaterial({ color: 0xff0000 });
const blue = new MeshBasicMaterial({ color: 0x0000ff });
const top = new Mesh(geometry, red);
const bottom = new Mesh(geometry, blue);
top.position.y = 0.5;
bottom.position.y = -0.5;
scene.add(top, bottom);
const camera = new OrthographicCamera(-1, 1, 1, -1, 0.1, 10);
camera.position.z = 3;
camera.lookAt(0, 0, 0);
adapter.renderer.render(scene, camera);
const describe = (capture: CapturedMesh): CaptureCheck => {
  const x = Math.floor(capture.width / 2);
  const sample = (y: number) =>
    Array.from(
      capture.pixels.subarray((y * capture.width + x) * 4, (y * capture.width + x) * 4 + 4),
    );
  return {
    width: capture.width,
    height: capture.height,
    top: sample(Math.floor(capture.height / 4)),
    bottom: sample(Math.floor((capture.height * 3) / 4)),
  };
};
window.meshCheck = {
  capture: async (scale) => describe(await adapter.capture(scene, camera, scale)),
  closeDuringCapture: async () => {
    const outcome = adapter.capture(scene, camera, 1).then(
      () => false,
      (error: Error) => error.message === "renderer_disposed",
    );
    await Promise.all([adapter.dispose(), adapter.dispose()]);
    return outcome;
  },
  recoverCapture: async () => {
    const renderer = adapter.renderer;
    if (!(renderer instanceof WebGLRenderer)) throw new Error("WebGL failure boundary required");
    const read = renderer.readRenderTargetPixelsAsync;
    const target = renderer.getRenderTarget();
    const background = scene.background;
    renderer.readRenderTargetPixelsAsync = async () => {
      throw new Error("injected_readback_failure");
    };
    let refused = false;
    try {
      await adapter.capture(scene, camera, 1);
    } catch (error) {
      refused = error instanceof Error && error.message === "injected_readback_failure";
    } finally {
      renderer.readRenderTargetPixelsAsync = read;
    }
    const restored = renderer.getRenderTarget() === target && scene.background === background;
    const next = await adapter.capture(scene, camera, 1);
    return refused && restored && next.pixels.some((value) => value !== 0);
  },
  close: async () => {
    controller.abort();
    await adapter.dispose();
    geometry.dispose();
    red.dispose();
    blue.dispose();
  },
};
document.body.dataset.backend = adapter.backend;
document.body.dataset.ready = "true";
