/** Observe real viewer resource retirement across repeated native lifecycles. */
import "../../src/globals.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import {
  Box3,
  MeshStandardMaterial,
  BoxGeometry,
  Mesh,
  Line,
  Points,
  Scene,
  type BufferGeometry,
  type Material,
} from "three";
import { STLExporter } from "three/addons/exporters/STLExporter.js";
import { STLViewer } from "../../src/components/stl-viewer";
import {
  DEFAULT_PREVIEW_PREFERENCES,
  writePreviewPreferences,
} from "../../src/lib/preview-preferences";

interface ResourceSnapshot {
  workers: number;
  devices: number;
  geometries: number;
  materials: number;
  objectUrls: number;
}
declare global {
  interface Window {
    meshLifecycleCheck: {
      runCycles(count: number): Promise<ResourceSnapshot[]>;
      alignmentBounds(): number[][];
      releaseFrame(): void;
      releaseViewer(): void;
    };
  }
}
const query = new URLSearchParams(location.search);
const backend = query.get("backend") === "webgpu" ? "webgpu" : "webgl";
writePreviewPreferences({ ...DEFAULT_PREVIEW_PREFERENCES, meshRenderer: backend });
let releaseFrame: (() => void) | null = null;
let releaseViewer: (() => void) | null = null;
if (query.has("pauseFrame")) {
  const { WebGPURenderer } = await import("three/webgpu");
  const render = WebGPURenderer.prototype.render;
  let paused = true;
  WebGPURenderer.prototype.render = function (...args) {
    if (paused) {
      document.body.dataset.drawRequested = "true";
      releaseFrame = () => {
        paused = false;
        render.apply(this, args);
      };
      return;
    }
    return render.apply(this, args);
  };
}
const workers = new Set<Worker>();
const devices = new Set<GPUDevice>();
const geometries = new Set<BufferGeometry>();
const materials = new Set<Material>();
const scenes = new Set<Scene>();
const objectUrls = new Set<string>();
const createObjectURL = URL.createObjectURL;
const revokeObjectURL = URL.revokeObjectURL;
URL.createObjectURL = (object) => {
  const url = createObjectURL.call(URL, object);
  objectUrls.add(url);
  return url;
};
URL.revokeObjectURL = (url) => {
  revokeObjectURL.call(URL, url);
  objectUrls.delete(url);
};
const NativeWorker = window.Worker;
window.Worker = class extends NativeWorker {
  constructor(url: string | URL, options?: WorkerOptions) {
    super(url, options);
    workers.add(this);
  }
  terminate() {
    super.terminate();
    workers.delete(this);
  }
};
if ("gpu" in navigator) {
  const request = GPUAdapter.prototype.requestDevice;
  GPUAdapter.prototype.requestDevice = async function (...args) {
    const device = await request.apply(this, args);
    devices.add(device);
    void device.lost.then(() => devices.delete(device));
    return device;
  };
}
const add = Scene.prototype.add;
Scene.prototype.add = function (...objects) {
  scenes.add(this);
  add.apply(this, objects);
  return this;
};
let bounds: number[][] = [];
function observeSceneResources() {
  bounds = [];
  for (const scene of scenes) {
    scene.traverse((object) => {
      if (!(object instanceof Mesh || object instanceof Line || object instanceof Points)) return;
      if (object instanceof Mesh && object.material instanceof MeshStandardMaterial) {
        const box = new Box3().setFromObject(object);
        bounds.push([...box.min.toArray(), ...box.max.toArray()]);
      }
      const geometry = object.geometry;
      if (!geometries.has(geometry)) {
        geometries.add(geometry);
        geometry.addEventListener("dispose", () => geometries.delete(geometry));
      }
      for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
        if (!materials.has(material)) {
          materials.add(material);
          material.addEventListener("dispose", () => materials.delete(material));
        }
      }
    });
  }
  scenes.clear();
}
const control = new BoxGeometry(4, 2, 6);
const bytes = new STLExporter().parse(new Mesh(control), { binary: true });
control.dispose();
const blob = new Blob([bytes]);
const halfControl = new BoxGeometry(2, 1, 3);
const halfBlob = new Blob([new STLExporter().parse(new Mesh(halfControl), { binary: true })]);
halfControl.dispose();
const host = document.getElementById("root");
if (host === null) throw new Error("missing lifecycle host");
const nextFrame = () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
const snapshot = (): ResourceSnapshot => ({
  workers: workers.size,
  devices: devices.size,
  geometries: geometries.size,
  materials: materials.size,
  objectUrls: objectUrls.size,
});
window.meshLifecycleCheck = {
  releaseFrame: () => releaseFrame?.(),
  releaseViewer: () => releaseViewer?.(),
  alignmentBounds: () => bounds,
  async runCycles(count) {
    const results: ResourceSnapshot[] = [];
    for (let cycle = 0; cycle < count; cycle += 1) {
      const root = createRoot(host);
      let ready: (() => void) | null = null;
      const loaded = new Promise<void>((resolve) => {
        ready = resolve;
      });
      root.render(
        <StrictMode>
          <STLViewer
            showGrid={false}
            displayMode={
              query.get("mode") === "xray"
                ? "xray"
                : query.get("mode") === "wireframe"
                  ? "wireframe"
                  : "solid"
            }
            url={"/lifecycle-" + cycle + ".stl"}
            previewFetcher={async (url) => ({
              ready: true as const,
              blob: url === "/overlay.stl" ? halfBlob : blob,
            })}
            comparison={query.has("overlay") ? { referenceSizeMm: 6 } : undefined}
            overlay={
              query.has("overlay")
                ? {
                    url: "/overlay.stl",
                    comparison: {
                      referenceSizeMm: 6,
                      compensateScale: true,
                      alignment: [
                        [2, 0, 0, 0],
                        [0, 2, 0, 0],
                        [0, 0, 2, 0],
                        [0, 0, 0, 1],
                      ],
                    },
                  }
                : undefined
            }
            onReadyChange={(value) => {
              document.body.dataset.viewerReady = String(value);
              if (value) ready?.();
            }}
          />
        </StrictMode>,
      );
      await loaded;
      await nextFrame();
      await nextFrame();
      if (host.querySelector("[data-mesh-backend]")?.getAttribute("data-mesh-backend") !== backend)
        throw new Error("requested_lifecycle_backend_unavailable");
      observeSceneResources();
      if (query.has("hold")) {
        await new Promise<void>((resolve) => {
          releaseViewer = resolve;
        });
      }
      root.unmount();
      const deadline = performance.now() + 3000;
      do {
        await nextFrame();
      } while (
        Object.values(snapshot()).some((value) => value > 0) &&
        performance.now() < deadline
      );
      results.push(snapshot());
    }
    return results;
  },
};
document.body.dataset.ready = "true";
