"use client";

import React, { Suspense, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Canvas, useLoader, useThree } from "@react-three/fiber";
import { OrbitControls, PerspectiveCamera } from "@react-three/drei";
import * as THREE from "three";
import { STLLoader } from "three-stdlib";
import { AlertTriangle, Loader2 } from "lucide-react";

import { authHeaders } from "@/lib/api/request";
import {
  previewPixelRatio,
  usePreviewPreferences,
  type ScreenshotScale,
} from "@/lib/preview-preferences";
import {
  fitCameraToBounds,
  heroCameraDirection,
  screenshotDimensions,
  screenshotHasForeground,
  visibleCanvasBackground,
} from "@/lib/thumbnail-camera";
import { useViewerReadiness } from "@/lib/use-viewer-readiness";

export type ViewerDisplayMode = "solid" | "xray" | "wireframe";

export interface STLViewerControls {
  zoomIn: () => void;
  zoomOut: () => void;
  resetView: () => void;
  fit: () => void;
  screenshot: () => Promise<void>;
}

export interface STLViewerProps {
  url: string;
  onControlsReady?: (api: STLViewerControls) => void;
  onReadyChange?: (ready: boolean) => void;
  displayMode?: ViewerDisplayMode;
  showGrid?: boolean;
  screenshotName?: string;
}

// The camera needs both shapes: R3F takes the tuple as a JSX prop, the orbit
// maths needs a Vector3. Derive the vector from the tuple so they cannot drift.
const DEFAULT_CAMERA_VECTOR = heroCameraDirection().multiplyScalar(18);
const DEFAULT_CAMERA_POSITION: THREE.Vector3Tuple = DEFAULT_CAMERA_VECTOR.toArray();
const ZOOM_FACTOR = 0.75;
// Mesh is normalized so its largest dimension equals this many world units.
const NORMALIZED_SIZE = 10;

const box = new THREE.Box3();
const sizeVec = new THREE.Vector3();
const centerVec = new THREE.Vector3();

function Mesh({
  url,
  displayMode,
  onSized,
}: {
  url: string;
  displayMode: ViewerDisplayMode;
  onSized: (size: THREE.Vector3) => void;
}) {
  const geometry = useLoader(STLLoader, url, (loader) => {
    loader.setRequestHeader(authHeaders());
  });
  const meshRef = useRef<THREE.Mesh>(null);

  useEffect(() => {
    if (!meshRef.current) return;
    const mesh = meshRef.current;
    mesh.scale.setScalar(1);
    mesh.position.set(0, 0, 0);
    // 3D-print meshes are authored Z-up; stand them upright in this Y-up scene
    // (matches the thumbnail renderer) so the model rests on the grid instead of
    // lying on its back and being sliced through the middle.
    mesh.rotation.set(-Math.PI / 2, 0, 0);
    mesh.updateMatrixWorld();

    box.setFromObject(mesh);
    box.getSize(sizeVec);

    const maxDim = Math.max(sizeVec.x, sizeVec.y, sizeVec.z);
    const scale = maxDim > 0 ? NORMALIZED_SIZE / maxDim : 1;

    mesh.scale.setScalar(scale);
    box.getCenter(centerVec);
    mesh.position.sub(centerVec.multiplyScalar(scale));

    onSized(sizeVec.clone().multiplyScalar(scale));
  }, [geometry, onSized]);

  return (
    <mesh ref={meshRef} geometry={geometry}>
      <meshStandardMaterial
        color="#8a93a6"
        roughness={0.45}
        metalness={0.1}
        wireframe={displayMode === "wireframe"}
        transparent={displayMode === "xray"}
        opacity={displayMode === "xray" ? 0.3 : 1}
        depthWrite={displayMode !== "xray"}
      />
    </mesh>
  );
}

function Scene({
  url,
  onControlsReady,
  onLoadedChange,
  displayMode,
  showGrid,
  screenshotName,
  screenshotScale,
}: Required<Omit<STLViewerProps, "onControlsReady" | "onReadyChange">> & {
  onControlsReady?: (api: STLViewerControls) => void;
  onLoadedChange?: (loaded: boolean) => void;
  screenshotScale: ScreenshotScale;
}) {
  const orbitRef = useRef<any>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera>(null);
  const { gl, scene, camera, invalidate, size: canvasSize } = useThree();
  const [modelSize, setModelSize] = useState(
    () => new THREE.Vector3(NORMALIZED_SIZE, NORMALIZED_SIZE, NORMALIZED_SIZE),
  );
  const { loaded, setLoaded } = useViewerReadiness(url);
  const loadedChangeRef = useRef(onLoadedChange);
  useEffect(() => {
    loadedChangeRef.current = onLoadedChange;
  }, [onLoadedChange]);
  // Ref so fit() always reads the latest size without stale closure
  const sizeRef = useRef(modelSize);
  useEffect(() => {
    sizeRef.current = modelSize;
  }, [modelSize]);

  const handleSized = useCallback(
    (nextSize: THREE.Vector3) => {
      setModelSize((current) => (current.equals(nextSize) ? current : nextSize));
      setLoaded(true);
      loadedChangeRef.current?.(true);
      invalidate();
    },
    [invalidate, setLoaded],
  );

  const gridSize = Math.max(modelSize.x, modelSize.z) * 2.6 || NORMALIZED_SIZE * 2.6;
  const floorY = -modelSize.y / 2;

  const controlsApi: STLViewerControls = {
    zoomIn: () => {
      if (cameraRef.current) {
        cameraRef.current.position.multiplyScalar(ZOOM_FACTOR);
        orbitRef.current?.update();
        invalidate();
      }
    },
    zoomOut: () => {
      if (cameraRef.current) {
        cameraRef.current.position.multiplyScalar(1 / ZOOM_FACTOR);
        orbitRef.current?.update();
        invalidate();
      }
    },
    resetView: () => {
      controlsApi.fit();
    },
    fit: () => {
      const cam = cameraRef.current;
      if (!cam) return;
      const fit = fitCameraToBounds(
        sizeRef.current,
        canvasSize.width / Math.max(canvasSize.height, 1),
        cam.fov,
      );
      orbitRef.current?.target?.set(0, 0, 0);
      cam.position.copy(fit.position);
      cam.lookAt(0, 0, 0);
      orbitRef.current?.update();
      invalidate();
    },
    screenshot: async () => {
      if (!loaded) throw new Error("preview_not_ready");
      const renderSize = gl.getSize(new THREE.Vector2());
      const dimensions = screenshotDimensions(
        renderSize.x,
        renderSize.y,
        screenshotScale,
        gl.capabilities.maxTextureSize,
      );
      const target = new THREE.WebGLRenderTarget(dimensions.width, dimensions.height, {
        depthBuffer: true,
        stencilBuffer: false,
      });
      target.texture.colorSpace = gl.outputColorSpace;
      const previousTarget = gl.getRenderTarget();
      const previousBackground = scene.background;
      const visibleBackground = visibleCanvasBackground(gl.domElement);
      const pixels = new Uint8Array(dimensions.width * dimensions.height * 4);
      try {
        if (visibleBackground) scene.background = visibleBackground;
        gl.setRenderTarget(target);
        gl.render(scene, camera);
        gl.readRenderTargetPixels(target, 0, 0, dimensions.width, dimensions.height, pixels);
      } finally {
        scene.background = previousBackground;
        gl.setRenderTarget(previousTarget);
        target.dispose();
        invalidate();
      }
      if (!screenshotHasForeground(pixels)) {
        throw new Error("screenshot_empty");
      }

      const flipped = new Uint8ClampedArray(pixels.length);
      const rowBytes = dimensions.width * 4;
      for (let y = 0; y < dimensions.height; y += 1) {
        const sourceStart = (dimensions.height - y - 1) * rowBytes;
        flipped.set(pixels.subarray(sourceStart, sourceStart + rowBytes), y * rowBytes);
      }
      const output = document.createElement("canvas");
      output.width = dimensions.width;
      output.height = dimensions.height;
      const context = output.getContext("2d");
      if (!context) throw new Error("screenshot_canvas_unavailable");
      context.putImageData(new ImageData(flipped, dimensions.width, dimensions.height), 0, 0);
      const blob = await new Promise<Blob>((resolve, reject) => {
        output.toBlob((value) => {
          if (value && value.size > 0) resolve(value);
          else reject(new Error("screenshot_encoding_failed"));
        }, "image/png");
      });
      const dataUrl = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = dataUrl;
      link.download = `${screenshotName || "model"}.png`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(dataUrl);
    },
  };

  useEffect(() => {
    onControlsReady?.(controlsApi);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onControlsReady, modelSize, loaded, canvasSize.width, canvasSize.height]);

  useLayoutEffect(() => {
    loadedChangeRef.current?.(false);
  }, [url]);

  // Re-fit when the source or viewport changes so tall/narrow layouts retain
  // the same safe framing as the generated thumbnail.
  useEffect(() => {
    if (loaded) {
      controlsApi.fit();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded, modelSize, canvasSize.width, canvasSize.height, url]);

  return (
    <>
      <PerspectiveCamera ref={cameraRef} makeDefault position={DEFAULT_CAMERA_POSITION} />
      <ambientLight intensity={0.5} />
      <hemisphereLight args={["#d4e8ff", "#1a1a2e", 0.4]} />
      <directionalLight position={[8, 12, 6]} intensity={1.2} castShadow />
      <directionalLight position={[-6, -4, -8]} intensity={0.25} />
      <directionalLight position={[0, -8, 0]} intensity={0.15} color="#8899bb" />
      <Suspense fallback={null}>
        <Mesh key={url} url={url} displayMode={displayMode} onSized={handleSized} />
      </Suspense>
      {showGrid && (
        <gridHelper args={[gridSize, 26, "#94a3b8", "#475569"]} position={[0, floorY, 0]} />
      )}
      <OrbitControls
        ref={orbitRef}
        enablePan
        enableZoom
        enableRotate
        minPolarAngle={0.02}
        maxPolarAngle={Math.PI / 2 - 0.02}
        onChange={() => invalidate()}
      />
    </>
  );
}

interface MeshErrorBoundaryProps {
  children: React.ReactNode;
  fallback?: React.ReactNode;
}

interface MeshErrorBoundaryState {
  hasError: boolean;
}

class MeshErrorBoundary extends React.Component<MeshErrorBoundaryProps, MeshErrorBoundaryState> {
  constructor(props: MeshErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(): MeshErrorBoundaryState {
    return { hasError: true };
  }

  render() {
    if (this.state.hasError) {
      return (
        this.props.fallback ?? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-on-surface-variant">
            <AlertTriangle className="h-8 w-8" />
            <span className="font-mono text-xs">Failed to load 3D preview</span>
          </div>
        )
      );
    }
    return this.props.children;
  }
}

export function STLViewer({
  url,
  onControlsReady,
  onReadyChange,
  displayMode = "solid",
  showGrid = true,
  screenshotName = "model",
}: STLViewerProps) {
  // Tracking *which* url has loaded, rather than a bare boolean, makes the url
  // swap reset the overlay during render instead of through a reset effect.
  const { loaded: meshLoaded, setLoaded: setMeshLoaded } = useViewerReadiness(url);
  const previewPreferences = usePreviewPreferences();

  useEffect(() => {
    onReadyChange?.(meshLoaded);
  }, [meshLoaded, onReadyChange]);

  return (
    <div className="relative h-full w-full touch-none overscroll-contain">
      <MeshErrorBoundary key={url}>
        <Canvas
          aria-label="3D model preview"
          className="h-full w-full touch-none overscroll-contain"
          dpr={previewPixelRatio(previewPreferences.previewQuality)}
          frameloop="demand"
        >
          <Scene
            url={url}
            onControlsReady={onControlsReady}
            onLoadedChange={setMeshLoaded}
            displayMode={displayMode}
            showGrid={showGrid}
            screenshotName={screenshotName}
            screenshotScale={previewPreferences.screenshotScale}
          />
        </Canvas>
        {/* Overlay while the mesh downloads/parses — the canvas mounts
            immediately, so without this the viewer is a blank void. */}
        {!meshLoaded && (
          <div
            role="status"
            aria-label="Loading 3D preview"
            className="pointer-events-none absolute inset-0 flex items-center justify-center"
          >
            <Loader2 className="h-8 w-8 animate-spin text-on-surface-variant" />
          </div>
        )}
      </MeshErrorBoundary>
    </div>
  );
}
