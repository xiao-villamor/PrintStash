/** Mount the public viewer with real STL bytes, native workers and renderers. */
import "../../src/globals.css";
import { StrictMode } from "react";
import type { ViewerDisplayMode } from "../../src/components/stl-viewer";
import { createRoot } from "react-dom/client";
import { BoxGeometry, Mesh } from "three";
import { STLExporter } from "three/addons/exporters/STLExporter.js";
import { STLViewer, type STLViewerControls } from "../../src/components/stl-viewer";
import { createComparisonCamera, type CameraPose } from "../../src/lib/comparison-camera";
import {
  DEFAULT_PREVIEW_PREFERENCES,
  writePreviewPreferences,
} from "../../src/lib/preview-preferences";

declare global {
  interface Window {
    meshViewerCheck: {
      zoom: () => void;
      fit: () => void;
      pose: () => CameraPose | null;
      loseDevice: () => void;
      screenshot: () => Promise<void>;
      close: () => void;
      releaseDevice: () => void;
      selectRenderer: (meshRenderer: "webgl" | "webgpu") => void;
    };
  }
}
const query = new URLSearchParams(location.search);
writePreviewPreferences({
  ...DEFAULT_PREVIEW_PREFERENCES,
  meshRenderer: query.get("backend") === "webgpu" ? "webgpu" : "webgl",
  screenshotScale: query.get("scale") === "1" ? 1 : query.get("scale") === "3" ? 3 : 2,
});
let device: GPUDevice | null = null;
let releaseDevice: (() => void) | null = null;
if ("gpu" in navigator) {
  const requestDevice = GPUAdapter.prototype.requestDevice;
  GPUAdapter.prototype.requestDevice = async function (...args) {
    device = await requestDevice.apply(this, args);
    void device.lost.then((info) => {
      document.body.dataset.deviceReleased = info.reason;
    });
    if (query.has("delayDevice")) {
      document.body.dataset.deviceRequested = "true";
      await new Promise<void>((resolve) => {
        releaseDevice = resolve;
      });
    }
    return device;
  };
}
const geometry = new BoxGeometry(4, 2, 6);
const exporter = new STLExporter();
const mesh = new Mesh(geometry);
const bytes = query.has("ascii") ? exporter.parse(mesh) : exporter.parse(mesh, { binary: true });
geometry.dispose();
const previewFetcher = async () => ({ ready: true as const, blob: new Blob([bytes]) });
const host = document.getElementById("root");
if (host === null) throw new Error("missing viewer host");
const root = createRoot(host);
const mode = query.get("mode");
const displayMode: ViewerDisplayMode = mode === "xray" || mode === "wireframe" ? mode : "solid";
let controls: STLViewerControls | null = null;
let pose: CameraPose | null = null;
const camera = createComparisonCamera();
camera.subscribe((_, value) => {
  pose = value;
});
const compare = query.has("compare");
host.style.width = compare ? "1280px" : "640px";
const readiness = [false, !compare];
const ready = (index: number, value: boolean) => {
  readiness[index] = value;
  document.body.dataset.ready = String(readiness.every(Boolean));
};
root.render(
  <StrictMode>
    <div style={{ display: "flex", height: "100%" }}>
      <div style={{ width: 640, height: 480 }}>
        <STLViewer
          displayMode={displayMode}
          url="/control.stl"
          previewFetcher={previewFetcher}
          comparisonCamera={camera}
          onControlsReady={(value) => {
            controls = value;
          }}
          onGeometrySize={(value) => {
            document.body.dataset.geometrySize = String(value);
          }}
          onReadyChange={(value) => ready(0, value)}
        />
      </div>
      {compare && (
        <div style={{ width: 640, height: 480 }}>
          <STLViewer
            url="/comparison.stl"
            previewFetcher={previewFetcher}
            comparisonCamera={camera}
            onReadyChange={(value) => ready(1, value)}
          />
        </div>
      )}
    </div>
  </StrictMode>,
);
window.meshViewerCheck = {
  zoom: () => controls?.zoomIn(),
  fit: () => (query.has("reset") ? controls?.resetView() : controls?.fit()),
  pose: () => pose,
  loseDevice: () => {
    if (device === null) throw new Error("no device to lose");
    device.destroy();
  },
  screenshot: async () => {
    if (controls === null) throw new Error("controls unavailable");
    await controls.screenshot();
  },
  close: () => root.unmount(),
  releaseDevice: () => releaseDevice?.(),
  selectRenderer: (meshRenderer) =>
    writePreviewPreferences({
      ...DEFAULT_PREVIEW_PREFERENCES,
      meshRenderer,
    }),
};
