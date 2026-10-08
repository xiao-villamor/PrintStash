"use client";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import { OrbitControls, PerspectiveCamera } from "@react-three/drei";
import * as THREE from "three";
import { type OrbitControls as OrbitControlsImpl } from "three-stdlib";
import {
  comparisonTransform,
  sharedScale,
  type ComparisonCamera,
  type MeshComparison,
} from "@/lib/comparison-camera";
import { AlertTriangle, Loader2 } from "lucide-react";

import { useParsedStl } from "@/lib/use-parsed-stl";
import { createMeshRenderer, type MeshRenderer, type RendererFallback } from "@/lib/mesh-renderer";
import type { CameraPose } from "@/lib/comparison-camera";
import type { MeshRendererPreference } from "@/lib/preview-preferences";
import {
  previewPixelRatio,
  usePreviewPreferences,
  type ScreenshotScale,
} from "@/lib/preview-preferences";
import { fitCameraToBounds, heroCameraDirection } from "@/lib/thumbnail-camera";
import { useViewerReadiness } from "@/lib/use-viewer-readiness";
import { useStlPreview, stlPreviewMessage } from "@/lib/use-stl-preview";

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
  modelId?: number;
  previewFetcher?: NonNullable<Parameters<typeof useStlPreview>[2]>;
  onControlsReady?: (api: STLViewerControls) => void;
  onReadyChange?: (ready: boolean) => void;
  /** Largest loaded dimension, before viewer normalization, in source units. */
  onGeometrySize?: (size: number) => void;
  displayMode?: ViewerDisplayMode;
  showGrid?: boolean;
  screenshotName?: string;
  comparison?: MeshComparison;
  comparisonCamera?: ComparisonCamera;
  overlay?: { url: string; comparison: MeshComparison };
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
  geometry,
  displayMode,
  onSized,
  onGeometrySize,
  comparison,
  overlay = false,
}: {
  geometry: THREE.BufferGeometry;
  displayMode: ViewerDisplayMode;
  onSized: (size: THREE.Vector3) => void;
  onGeometrySize?: (size: number) => void;
  comparison?: MeshComparison;
  overlay?: boolean;
}) {
  useUiLocale();
  const meshRef = useRef<THREE.Mesh>(null);
  const geometrySizeRef = useRef(onGeometrySize);
  useEffect(() => {
    geometrySizeRef.current = onGeometrySize;
  }, [onGeometrySize]);

  useEffect(() => {
    if (!meshRef.current) return;
    const mesh = meshRef.current;
    mesh.scale.setScalar(1);
    mesh.position.set(0, 0, 0);
    // 3D-print meshes are authored Z-up; stand them upright in this Y-up scene
    // (matches the thumbnail renderer) so the model rests on the grid instead of
    // lying on its back and being sliced through the middle.
    mesh.rotation.set(0, 0, 0);
    if (comparison) mesh.applyMatrix4(comparisonTransform(comparison));
    mesh.applyMatrix4(new THREE.Matrix4().makeRotationX(-Math.PI / 2));
    mesh.updateMatrixWorld();

    box.setFromObject(mesh);
    box.getSize(sizeVec);

    const maxDim = Math.max(sizeVec.x, sizeVec.y, sizeVec.z);
    if (Number.isFinite(maxDim) && maxDim > 0) geometrySizeRef.current?.(maxDim);
    const scale = comparison
      ? sharedScale(comparison.referenceSizeMm)
      : maxDim > 0
        ? NORMALIZED_SIZE / maxDim
        : 1;

    mesh.scale.multiplyScalar(scale);
    box.getCenter(centerVec);
    mesh.position.sub(centerVec.multiplyScalar(scale));

    onSized(
      comparison
        ? new THREE.Vector3(NORMALIZED_SIZE, NORMALIZED_SIZE, NORMALIZED_SIZE)
        : sizeVec.clone().multiplyScalar(scale),
    );
  }, [geometry, onSized, comparison]);

  return (
    <mesh ref={meshRef} geometry={geometry}>
      <meshStandardMaterial
        color={overlay ? "#497bbd" : "#8a93a6"}
        roughness={0.45}
        metalness={0.1}
        wireframe={displayMode === "wireframe"}
        transparent={overlay || displayMode === "xray"}
        opacity={overlay ? 0.42 : displayMode === "xray" ? 0.3 : 1}
        depthWrite={!overlay && displayMode !== "xray"}
      />
    </mesh>
  );
}

function Scene({
  url,
  geometry,
  overlayGeometry,
  renderer,
  restoredPose,
  rememberPose,
  onControlsReady,
  onLoadedChange,
  onGeometrySize,
  displayMode,
  showGrid,
  screenshotName,
  screenshotScale,
  comparison,
  comparisonCamera,
  overlay,
}: Required<
  Omit<
    STLViewerProps,
    | "modelId"
    | "previewFetcher"
    | "onControlsReady"
    | "onReadyChange"
    | "onGeometrySize"
    | "comparison"
    | "comparisonCamera"
    | "overlay"
  >
> &
  Pick<STLViewerProps, "comparison" | "comparisonCamera" | "overlay"> & {
    onControlsReady?: (api: STLViewerControls) => void;
    onLoadedChange?: (loaded: boolean) => void;
    onGeometrySize?: (size: number) => void;
    screenshotScale: ScreenshotScale;
    geometry: THREE.BufferGeometry;
    overlayGeometry: THREE.BufferGeometry | null;
    renderer: MeshRenderer;
    restoredPose: React.RefObject<CameraPose | null>;
    rememberPose: (pose: CameraPose) => void;
  }) {
  useUiLocale();
  const orbitRef = useRef<OrbitControlsImpl>(null);
  const syncing = useRef(false);
  const poseApplied = useRef(false);
  const sender = useRef(Symbol("comparison-camera"));
  const cameraRef = useRef<THREE.PerspectiveCamera>(null);
  const { scene, camera, invalidate, size: canvasSize } = useThree();
  const [modelSize, setModelSize] = useState(
    () => new THREE.Vector3(NORMALIZED_SIZE, NORMALIZED_SIZE, NORMALIZED_SIZE),
  );
  const { loaded, setLoaded } = useViewerReadiness(url);
  useEffect(
    () =>
      comparisonCamera?.subscribe((origin, pose) => {
        if (origin === sender.current || !cameraRef.current || !orbitRef.current) return;
        syncing.current = true;
        cameraRef.current.position.fromArray(pose.position);
        orbitRef.current.target.fromArray(pose.target);
        orbitRef.current.update();
        invalidate();
        syncing.current = false;
      }),
    [comparisonCamera, invalidate],
  );

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
      let captured;
      try {
        captured = await renderer.capture(scene, camera, screenshotScale);
      } finally {
        invalidate();
      }
      const { width, height, pixels } = captured;
      const output = document.createElement("canvas");
      output.width = width;
      output.height = height;
      const context = output.getContext("2d");
      if (!context) throw new Error("screenshot_canvas_unavailable");
      context.putImageData(new ImageData(pixels, width, height), 0, 0);
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
  }, [
    onControlsReady,
    modelSize,
    loaded,
    canvasSize.width,
    canvasSize.height,
    renderer,
    screenshotScale,
  ]);

  useLayoutEffect(() => {
    loadedChangeRef.current?.(false);
  }, [url]);

  // Re-fit when the source or viewport changes so tall/narrow layouts retain
  // the same safe framing as the generated thumbnail.
  useEffect(() => {
    if (loaded) {
      const pose = restoredPose.current;
      if (!poseApplied.current && pose !== null && cameraRef.current && orbitRef.current) {
        poseApplied.current = true;
        cameraRef.current.position.fromArray(pose.position);
        orbitRef.current.target.fromArray(pose.target);
        orbitRef.current.update();
        invalidate();
      } else controlsApi.fit();
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
      <>
        <Mesh
          key={url}
          geometry={geometry}
          displayMode={displayMode}
          onSized={handleSized}
          onGeometrySize={onGeometrySize}
          comparison={comparison}
        />
        {overlay && overlayGeometry && (
          <Mesh
            geometry={overlayGeometry}
            displayMode="solid"
            onSized={() => {}}
            comparison={overlay.comparison}
            overlay
          />
        )}
      </>
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
        onChange={() => {
          invalidate();
          if (cameraRef.current && orbitRef.current) {
            const pose = {
              position: cameraRef.current.position.toArray(),
              target: orbitRef.current.target.toArray(),
            };
            if (loaded) rememberPose(pose);
            if (!syncing.current) comparisonCamera?.publish(sender.current, pose);
          }
        }}
      />
    </>
  );
}

interface MeshErrorBoundaryProps {
  children: React.ReactNode;
  fallback?: React.ReactNode;
  onFailure?: () => void;
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

  componentDidCatch() {
    this.props.onFailure?.();
  }

  render() {
    if (this.state.hasError) {
      return (
        this.props.fallback ?? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-on-surface-variant">
            <AlertTriangle className="h-8 w-8" />
            <span className="font-mono text-xs">{uiText("Failed to load 3D preview")}</span>
          </div>
        )
      );
    }
    return this.props.children;
  }
}

type SceneProps = React.ComponentProps<typeof Scene>;

function RendererCanvas({
  preference,
  onFailure,
  fallback,
  onLoadedChange,
  ...props
}: Omit<SceneProps, "renderer"> & {
  preference: MeshRendererPreference;
  onFailure: (reason: RendererFallback) => void;
  fallback: RendererFallback | null;
}) {
  const [controller] = useState(() => new AbortController());
  const adapter = useRef<MeshRenderer | null>(null);
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const [renderer, setRenderer] = useState<MeshRenderer | null>(null);
  useEffect(
    () => () => {
      // StrictMode replays effects while the same canvas remains mounted.
      if (canvas.current === null || canvas.current.isConnected) return;
      controller.abort();
      void adapter.current?.dispose().catch((error: Error) => {
        console.warn("Mesh renderer cleanup failed", error);
      });
    },
    [controller],
  );
  const factory = useCallback(
    async (defaults: { canvas: EventTarget }) => {
      if (!(defaults.canvas instanceof HTMLCanvasElement))
        throw new Error("mesh_canvas_unavailable");
      canvas.current = defaults.canvas;
      const created = await createMeshRenderer(defaults.canvas, preference, controller.signal, () =>
        onFailure("device_lost"),
      );
      adapter.current = created;
      setRenderer(created);
      return created.renderer;
    },
    [preference, controller, onFailure],
  );
  return (
    <MeshErrorBoundary onFailure={() => onFailure("initialization_failed")}>
      <Canvas
        aria-label={uiText("3D model preview")}
        className="h-full w-full touch-none overscroll-contain"
        dpr={previewPixelRatio(usePreviewPreferences().previewQuality)}
        frameloop="demand"
        data-mesh-backend={renderer?.backend}
        data-renderer-fallback={renderer?.fallback ?? fallback ?? undefined}
        gl={factory}
      >
        {renderer && <Scene {...props} renderer={renderer} onLoadedChange={onLoadedChange} />}
      </Canvas>
    </MeshErrorBoundary>
  );
}

function MeshCanvas(
  props: Omit<SceneProps, "renderer" | "restoredPose" | "rememberPose"> & {
    preference: MeshRendererPreference;
  },
) {
  const [recovered, setRecovered] = useState<RendererFallback | null>(null);
  const restoredPose = useRef<CameraPose | null>(null);
  const { preference, onLoadedChange } = props;
  const recover = useCallback(
    (reason: RendererFallback) => {
      if (!recovered && preference !== "webgl") {
        onLoadedChange?.(false);
        setRecovered(reason);
      }
    },
    [recovered, preference, onLoadedChange],
  );
  const rememberPose = useCallback((pose: CameraPose) => {
    restoredPose.current = pose;
  }, []);
  return (
    <RendererCanvas
      key={recovered ? "recovered" : "initial"}
      {...props}
      preference={recovered ? "webgl" : props.preference}
      onFailure={recover}
      fallback={recovered}
      restoredPose={restoredPose}
      rememberPose={rememberPose}
    />
  );
}

export function STLViewer({
  url,
  modelId,
  previewFetcher,
  onControlsReady,
  onReadyChange,
  onGeometrySize,
  displayMode = "solid",
  showGrid = true,
  screenshotName = "model",
  comparison,
  comparisonCamera,
  overlay,
}: STLViewerProps) {
  useUiLocale();
  // Tracking *which* url has loaded, rather than a bare boolean, makes the url
  // swap reset the overlay during render instead of through a reset effect.
  const previewPreferences = usePreviewPreferences();
  const preview = useStlPreview(url, modelId, previewFetcher);
  const { loaded: meshLoaded, setLoaded: setMeshLoaded } = useViewerReadiness(
    (preview.state === "ready" ? preview.url : url) + ":" + previewPreferences.meshRenderer,
  );
  const overlayPreview = useStlPreview(overlay?.url ?? null, undefined, previewFetcher);
  const parsed = useParsedStl(preview.state === "ready" ? preview.url : null);
  const parsedOverlay = useParsedStl(overlayPreview.state === "ready" ? overlayPreview.url : null);

  useEffect(() => {
    onReadyChange?.(meshLoaded && preview.state === "ready");
  }, [meshLoaded, onReadyChange, preview.state]);

  const failure =
    preview.state === "failed"
      ? preview
      : overlay !== undefined && overlayPreview.state === "failed"
        ? overlayPreview
        : null;
  if (failure !== null)
    return (
      <div
        role="status"
        className="flex h-full flex-col items-center justify-center gap-2 text-on-surface-variant"
      >
        <AlertTriangle className="h-8 w-8" aria-hidden />
        <span className="text-xs">{stlPreviewMessage(failure.reason)}</span>
      </div>
    );
  if (parsed.state === "failed" || (overlay !== undefined && parsedOverlay.state === "failed"))
    return (
      <div
        role="status"
        className="flex h-full items-center justify-center text-on-surface-variant"
      >
        {uiText("Failed to load 3D preview")}
      </div>
    );
  if (
    preview.state !== "ready" ||
    parsed.state !== "ready" ||
    (overlay !== undefined && (overlayPreview.state !== "ready" || parsedOverlay.state !== "ready"))
  )
    return (
      <div
        role="status"
        aria-label={uiText("Preparing 3D preview…")}
        className="flex h-full items-center justify-center gap-2 text-on-surface-variant"
      >
        <Loader2 className="h-8 w-8 animate-spin" aria-hidden />
        <span className="text-xs">{uiText("Preparing 3D preview…")}</span>
      </div>
    );

  return (
    <div className="relative h-full w-full touch-none overscroll-contain">
      <MeshCanvas
        key={`${preview.url}:${previewPreferences.meshRenderer}`}
        url={preview.url}
        geometry={parsed.geometry}
        overlayGeometry={parsedOverlay.state === "ready" ? parsedOverlay.geometry : null}
        preference={previewPreferences.meshRenderer}
        onControlsReady={onControlsReady}
        onLoadedChange={setMeshLoaded}
        onGeometrySize={onGeometrySize}
        displayMode={displayMode}
        showGrid={showGrid}
        screenshotName={screenshotName}
        screenshotScale={previewPreferences.screenshotScale}
        comparison={comparison}
        comparisonCamera={comparisonCamera}
        overlay={
          overlay !== undefined && overlayPreview.state === "ready"
            ? { ...overlay, url: overlayPreview.url }
            : undefined
        }
      />
      {/* Keep a loading indicator until the mounted scene has fitted the mesh. */}
      {!meshLoaded && (
        <div
          role="status"
          aria-label={uiText("Loading 3D preview")}
          className="pointer-events-none absolute inset-0 flex items-center justify-center"
        >
          <Loader2 className="h-8 w-8 animate-spin text-on-surface-variant" />
        </div>
      )}
    </div>
  );
}
