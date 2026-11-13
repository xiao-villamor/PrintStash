import { uiText } from "@/lib/locale";
import { formatNumber } from "@/lib/format";
import { ApiError } from "@/lib/errors";
import { Button } from "@/components/ui/button";

import React, { useEffect, useMemo, useRef, useState, type ComponentType } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import { OrbitControls, PerspectiveCamera } from "@react-three/drei";
import * as THREE from "three";
import { AlertTriangle, ChevronDown, ChevronUp, Layers, Loader2, Maximize2 } from "lucide-react";
import { cn } from "@/lib/utils";
import "./gcode-viewer.css";
import { getDerivedText } from "@/lib/api/request";
import { subscribeEvents } from "@/lib/events";
import { useOptionalI18n, type MessageKey } from "@/lib/i18n";
import { previewPixelRatio, usePreviewPreferences } from "@/lib/preview-preferences";
import type { ToolpathData } from "@/lib/gcode";
import { parseGcodeInWorker, ToolpathParseError } from "@/lib/gcode-worker-client";

import type { STLViewerControls, ViewerCameraPreset } from "@/components/stl-viewer";

// ---- Three.js Scene ----

function GcodeScene({
  data,
  currentLayer,
  onlyCurrentLayer,
  showTravel,
  showBed,
  printerBedMm,
  showGrid,
  onControlsReady,
  screenshotName,
}: {
  data: ToolpathData;
  currentLayer: number;
  onlyCurrentLayer: boolean;
  showTravel: boolean;
  showBed: boolean;
  printerBedMm: { x: number; y: number } | null;
  showGrid: boolean;
  onControlsReady: (controls: STLViewerControls | null) => void;
  screenshotName: string;
}) {
  const { camera, gl, scene } = useThree();
  const orbitRef = useRef<any>(null);

  const extrudeGeo = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(data.extrudePositions, 3));
    geo.setAttribute("color", new THREE.BufferAttribute(data.extrudeColors, 3));
    return geo;
  }, [data]);

  const travelGeo = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(data.travelPositions, 3));
    return geo;
  }, [data]);

  const extrudeMat = useMemo(() => new THREE.LineBasicMaterial({ vertexColors: true }), []);
  const travelMat = useMemo(
    () => new THREE.LineBasicMaterial({ color: "#94a3b8", transparent: true, opacity: 0.2 }),
    [],
  );

  // Stable LineSegments objects — must be memoized so <primitive> identity is stable across renders
  const extrudeLines = useMemo(
    () => new THREE.LineSegments(extrudeGeo, extrudeMat),
    [extrudeGeo, extrudeMat],
  );
  const travelLines = useMemo(
    () => new THREE.LineSegments(travelGeo, travelMat),
    [travelGeo, travelMat],
  );

  // Bed geometry (actual mm dimensions — gcode coords are real mm)
  const bedGeo = useMemo(() => {
    if (!printerBedMm) return null;
    return new THREE.PlaneGeometry(printerBedMm.x, printerBedMm.y);
  }, [printerBedMm]);
  const bedEdgesGeo = useMemo(() => (bedGeo ? new THREE.EdgesGeometry(bedGeo) : null), [bedGeo]);

  // Update drawRange directly on the stable geometry — no ref gymnastics needed
  useEffect(() => {
    const count = data.cumulativeVertices[currentLayer + 1] ?? data.extrudePositions.length / 3;
    const start = onlyCurrentLayer ? (data.cumulativeVertices[currentLayer] ?? 0) : 0;
    extrudeGeo.setDrawRange(start, Math.max(count - start, 0));
  }, [currentLayer, onlyCurrentLayer, data, extrudeGeo]);

  // One array per toolpath: R3F re-applies a changed `position` prop on every
  // render, so a fresh array would snap the camera back to its starting point
  // whenever a control or the layer slider re-rendered the scene.
  const initialCameraPosition = useMemo<[number, number, number]>(
    () => [data.bounds.maxDim * 0.8, data.bounds.maxDim * 0.9, data.bounds.maxDim * 1.2],
    [data],
  );

  useEffect(() => {
    const distance = Math.max(data.bounds.maxDim, 1);
    function setView(preset: ViewerCameraPreset) {
      // OrbitControls keeps the world up axis it started with; the top view
      // looks straight down by sitting a hair off the axis instead of rolling it.
      camera.up.set(0, 1, 0);
      if (preset === "front") camera.position.set(0, 0, distance * 1.8);
      else if (preset === "top") camera.position.set(0, distance * 1.8, distance * 0.001);
      else camera.position.set(distance * 0.8, distance * 0.9, distance * 1.2);
      orbitRef.current?.target.set(0, 0, 0);
      camera.lookAt(0, 0, 0);
      orbitRef.current?.update();
    }
    function zoom(factor: number) {
      const target = orbitRef.current?.target ?? new THREE.Vector3();
      camera.position.sub(target).multiplyScalar(factor).add(target);
      orbitRef.current?.update();
    }
    setView("isometric");
    onControlsReady({
      setView,
      zoomIn: () => zoom(0.8),
      zoomOut: () => zoom(1.25),
      fit: () => setView("isometric"),
      resetView: () => setView("isometric"),
      screenshot: async () => {
        gl.render(scene, camera);
        const blob = await new Promise<Blob>((resolve, reject) =>
          gl.domElement.toBlob(
            (value) => (value ? resolve(value) : reject(new Error("screenshot_encoding_failed"))),
            "image/png",
          ),
        );
        const href = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = href;
        link.download = `${screenshotName}.png`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(href);
      },
    });
    return () => onControlsReady(null);
  }, [data, camera, gl, scene, screenshotName, onControlsReady]);

  const gridHalfSize =
    showBed && printerBedMm
      ? Math.max(printerBedMm.x, printerBedMm.y) * 0.6
      : (Math.max(data.bounds.sizeX, data.bounds.sizeY) / 2) * 1.1 || 10;
  const floorY = -(data.bounds.sizeZ / 2);

  return (
    <>
      <PerspectiveCamera
        makeDefault
        fov={45}
        near={0.1}
        far={10000}
        position={initialCameraPosition}
      />
      <ambientLight intensity={0.8} />
      <primitive object={extrudeLines} />
      {showTravel && data.travelPositions.length > 0 && <primitive object={travelLines} />}

      {/* Bed platform (only in bed-fit mode) */}
      {showGrid && showBed && bedGeo && bedEdgesGeo && printerBedMm && (
        <group position={[0, floorY - 0.5, 0]} rotation={[-Math.PI / 2, 0, 0]}>
          <mesh geometry={bedGeo}>
            <meshStandardMaterial
              color="#1e3a5f"
              transparent
              opacity={0.15}
              side={THREE.DoubleSide}
            />
          </mesh>
          <lineSegments geometry={bedEdgesGeo}>
            <lineBasicMaterial color="#3b82f6" transparent opacity={0.8} />
          </lineSegments>
          <gridHelper
            args={[Math.max(printerBedMm.x, printerBedMm.y), 10, "#1e40af", "#1e3a5f"]}
            rotation={[Math.PI / 2, 0, 0]}
          />
        </group>
      )}

      {/* Default floor grid (when not in bed-fit mode) */}
      {showGrid && (!showBed || !printerBedMm) && (
        <gridHelper
          args={[gridHalfSize * 2, 20, "#475569", "#334155"]}
          position={[0, floorY - 0.5, 0]}
        />
      )}

      <OrbitControls ref={orbitRef} enablePan enableZoom enableRotate />
    </>
  );
}

// ---- Error Boundary ----

interface EBState {
  hasError: boolean;
}
class GcodeErrorBoundary extends React.Component<
  { children: React.ReactNode; renderFailed: string },
  EBState
> {
  constructor(props: { children: React.ReactNode; renderFailed: string }) {
    super(props);
    this.state = { hasError: false };
  }
  static getDerivedStateFromError(): EBState {
    return { hasError: true };
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-muted-foreground">
          <AlertTriangle className="h-8 w-8" />
          <span className="font-mono text-xs">{this.props.renderFailed}</span>
        </div>
      );
    }
    return this.props.children;
  }
}

function viewerCopy(
  i18n: ReturnType<typeof useOptionalI18n>,
  key: MessageKey,
  values?: Record<string, string>,
): string {
  return i18n?.t(key, values) ?? uiText(key, values);
}

// ---- Public Component ----

interface CanvasRendererProps {
  children: React.ReactNode;
  className: string;
  dpr: number;
  gl: { preserveDrawingBuffer: boolean };
}

function DefaultCanvasRenderer({ children, className, dpr, gl }: CanvasRendererProps) {
  return (
    <Canvas className={className} dpr={dpr} gl={gl}>
      {children}
    </Canvas>
  );
}

/** The outcome of one completed toolpath fetch, tagged with the url it was for. */
interface LoadedToolpath {
  url: string;
  fetcher: typeof getDerivedText;
  data: ToolpathData | null;
  errorKind: "limit" | "resource" | "busy" | "invalid" | "load" | "disabled" | null;
  /** The toolpath is a derivative still being produced; check again shortly. */
  preparing?: boolean;
}

/** How often a toolpath still being derived is checked again. */
const PREPARING_POLL_MS = 3_000;
const CAMERA_PRESETS = [
  "isometric",
  "front",
  "top",
] as const satisfies readonly ViewerCameraPreset[];
const TOGGLE =
  "h-7 px-1.5 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-40";
const ICON_BUTTON =
  "inline-flex h-7 w-7 items-center justify-center text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-40";

export interface GcodeViewerProps {
  url: string;
  printerBedMm?: { x: number; y: number } | null;
  screenshotName?: string;
  showGrid?: boolean;
  showDimensions?: boolean;
  onControlsReady?: (controls: STLViewerControls | null) => void;
  canvasRenderer?: ComponentType<CanvasRendererProps>;
  toolpathParser?: typeof parseGcodeInWorker;
  toolpathFetcher?: typeof getDerivedText;
  privateEventsEnabled?: boolean;
}

export function GcodeViewer({
  url,
  printerBedMm = null,
  screenshotName = "toolpath",
  showGrid = true,
  showDimensions = true,
  onControlsReady,
  canvasRenderer,
  toolpathParser = parseGcodeInWorker,
  toolpathFetcher = getDerivedText,
  privateEventsEnabled = true,
}: GcodeViewerProps) {
  const i18n = useOptionalI18n();
  const previewPreferences = usePreviewPreferences();
  const CanvasRenderer = canvasRenderer ?? DefaultCanvasRenderer;
  // One state for the fetch that has actually completed, tagged with its url,
  // so switching files derives "loading" during render instead of clearing the
  // previous file's toolpath from an effect.
  const [retry, setRetry] = useState(0);
  const [loaded, setLoaded] = useState<LoadedToolpath | null>(null);
  const [currentLayer, setCurrentLayer] = useState(0);
  const [onlyCurrentLayer, setOnlyCurrentLayer] = useState(false);
  const [showTravel, setShowTravel] = useState(false);
  const [showBed, setShowBed] = useState(true);
  const localControls = useRef<STLViewerControls | null>(null);
  const [cameraPreset, setCameraPreset] = useState<ViewerCameraPreset>("isometric");
  const connectControls = React.useCallback(
    (controls: STLViewerControls | null) => {
      localControls.current = controls;
      if (controls) setCameraPreset("isometric");
      onControlsReady?.(controls);
    },
    [onControlsReady],
  );

  useEffect(() => {
    if (!privateEventsEnabled) return;
    return subscribeEvents((notice) => {
      if (notice.type === "derivative_policy" || notice.type === "resync") {
        setLoaded(null);
        setRetry((value) => value + 1);
      }
    });
  }, [privateEventsEnabled]);

  const current = loaded?.url === url && loaded.fetcher === toolpathFetcher ? loaded : null;
  const loading = current === null;
  const data = current?.data ?? null;
  const errorKind = current?.errorKind ?? null;

  useEffect(() => {
    // A response for a file the viewer has already left must not be shown as
    // this url's toolpath.
    let live = true;

    const controller = new AbortController();
    let recheck: ReturnType<typeof setTimeout> | null = null;
    toolpathFetcher(url, controller.signal)
      .then(async (derived) => {
        if (!derived.ready) {
          if (!live) return;
          // A binary toolpath is converted in the background; the old response
          // was the conversion itself, now it is a derivative that may not
          // exist yet.
          setLoaded({
            url,
            fetcher: toolpathFetcher,
            data: null,
            errorKind: null,
            preparing: true,
          });
          recheck = setTimeout(() => setRetry((value) => value + 1), PREPARING_POLL_MS);
          return;
        }
        const parsed = await toolpathParser(derived.text, controller.signal);
        if (!live) return;
        setLoaded({ url, fetcher: toolpathFetcher, data: parsed, errorKind: null });
        setCurrentLayer(parsed.totalLayers - 1);
      })
      .catch((cause: unknown) => {
        if (!live) return;
        setLoaded({
          url,
          fetcher: toolpathFetcher,
          data: null,
          errorKind:
            cause instanceof ApiError &&
            cause.status === 409 &&
            cause.code === "derivative_group_disabled"
              ? "disabled"
              : cause instanceof ToolpathParseError
                ? cause.code === "limit"
                  ? "limit"
                  : "invalid"
                : cause instanceof ApiError && [413, 504].includes(cause.status)
                  ? "resource"
                  : cause instanceof ApiError && cause.status === 429
                    ? "busy"
                    : cause instanceof ApiError && cause.status === 422
                      ? "invalid"
                      : "load",
        });
      });

    return () => {
      live = false;
      if (recheck !== null) clearTimeout(recheck);
      controller.abort();
    };
  }, [url, toolpathParser, toolpathFetcher, retry]);

  const loadingCopy = viewerCopy(i18n, "viewer.loadingToolpath");
  const renderFailedCopy = viewerCopy(i18n, "viewer.renderFailed");
  const errors = {
    limit: "viewer.segmentLimit",
    resource: "viewer.resourceLimit",
    busy: "viewer.converterBusy",
    invalid: "viewer.invalidToolpath",
    load: "viewer.loadFailed",
    disabled: "viewer.processingDisabled",
  } satisfies Record<NonNullable<LoadedToolpath["errorKind"]>, MessageKey>;
  const errorCopy = viewerCopy(i18n, errors[errorKind ?? "load"]);
  const noDataCopy = viewerCopy(i18n, "viewer.noToolpathData");
  const noToolpathCopy = viewerCopy(i18n, "viewer.noToolpathFound");

  if (loading || current?.preparing) {
    return (
      <div
        role="status"
        className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-muted-foreground"
      >
        <Loader2 className="h-8 w-8 animate-spin" />
        <span className="font-mono text-xs">
          {current?.preparing ? viewerCopy(i18n, "viewer.preparingToolpath") : loadingCopy}
        </span>
      </div>
    );
  }

  if (errorKind === "disabled") {
    return (
      <div
        role="status"
        className="absolute inset-0 flex items-center justify-center text-muted-foreground"
      >
        <span className="font-mono text-xs">{errorCopy}</span>
      </div>
    );
  }

  if (errorKind || !data) {
    return (
      <div
        role="alert"
        className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-muted-foreground"
      >
        <AlertTriangle className="h-8 w-8" />
        <span className="font-mono text-xs">{errorKind ? errorCopy : noDataCopy}</span>
        <Button
          variant="outline"
          onClick={() => {
            setLoaded(null);
            setRetry((value) => value + 1);
          }}
        >
          {viewerCopy(i18n, "viewer.retry")}
        </Button>
      </div>
    );
  }

  if (data.totalLayers === 0) {
    return (
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-muted-foreground">
        <Layers className="h-8 w-8 opacity-40" />
        <span className="font-mono text-xs">{noToolpathCopy}</span>
      </div>
    );
  }

  return (
    <div className="relative h-full w-full">
      <GcodeErrorBoundary renderFailed={renderFailedCopy}>
        <CanvasRenderer
          className="h-full w-full"
          dpr={previewPixelRatio(previewPreferences.previewQuality)}
          gl={{ preserveDrawingBuffer: true }}
        >
          <GcodeScene
            data={data}
            currentLayer={currentLayer}
            onlyCurrentLayer={onlyCurrentLayer}
            showTravel={showTravel}
            showBed={showBed}
            printerBedMm={printerBedMm ?? null}
            showGrid={showGrid}
            onControlsReady={connectControls}
            screenshotName={screenshotName}
          />
        </CanvasRenderer>
      </GcodeErrorBoundary>

      {showDimensions && (
        <div className="absolute bottom-12 left-2 bg-card px-2 py-1 font-mono text-2xs text-muted-foreground">
          {formatNumber(data.bounds.sizeX, { maximumFractionDigits: 1 })} ×{" "}
          {formatNumber(data.bounds.sizeY, { maximumFractionDigits: 1 })} ×{" "}
          {uiText("{value1} mm", {
            value1: formatNumber(data.bounds.sizeZ, { maximumFractionDigits: 1 }),
          })}
        </div>
      )}
      <div
        role="group"
        aria-label={viewerCopy(i18n, "viewer.currentLayer")}
        className="absolute bottom-2 right-2 top-2 z-10 flex w-14 flex-col items-center gap-1 border border-border bg-card py-1"
      >
        <span className="font-mono text-2xs tabular-nums text-foreground">
          {currentLayer + 1}
          <span className="text-muted-foreground">/{data.totalLayers}</span>
        </span>
        <button
          type="button"
          aria-label={uiText("Next layer")}
          disabled={currentLayer >= data.totalLayers - 1}
          onClick={() => setCurrentLayer((layer) => Math.min(layer + 1, data.totalLayers - 1))}
          className={ICON_BUTTON}
        >
          <ChevronUp className="h-3.5 w-3.5" aria-hidden />
        </button>
        <input
          aria-label={viewerCopy(i18n, "viewer.currentLayer")}
          aria-valuetext={viewerCopy(i18n, "viewer.layer", {
            current: String(currentLayer + 1),
            total: String(data.totalLayers),
          })}
          type="range"
          min={0}
          max={data.totalLayers - 1}
          value={currentLayer}
          onChange={(event) => setCurrentLayer(Number(event.target.value))}
          className="min-h-0 w-4 flex-1 cursor-pointer accent-primary [direction:rtl] [writing-mode:vertical-lr]"
        />
        <button
          type="button"
          aria-label={uiText("Previous layer")}
          disabled={currentLayer <= 0}
          onClick={() => setCurrentLayer((layer) => Math.max(layer - 1, 0))}
          className={ICON_BUTTON}
        >
          <ChevronDown className="h-3.5 w-3.5" aria-hidden />
        </button>
        <span className="font-mono text-2xs tabular-nums text-muted-foreground">
          {data.layerRanges[currentLayer]
            ? uiText("{value1} mm", {
                value1: formatNumber(data.layerRanges[currentLayer].z, {
                  maximumFractionDigits: 2,
                }),
              })
            : "–"}
        </span>
      </div>
      <div
        className="absolute bottom-2 left-2 right-18 z-10 flex justify-start"
        title={uiText("Drag to orbit · Scroll to zoom")}
      >
        <div className="flex max-w-full flex-wrap items-center gap-0.5 border border-border bg-card p-0.5">
          <label>
            <span className="sr-only">{uiText("Camera view")}</span>
            <select
              value={cameraPreset}
              onChange={(event) => {
                const preset = CAMERA_PRESETS.find((item) => item === event.target.value);
                if (!preset) return;
                localControls.current?.setView(preset);
                setCameraPreset(preset);
              }}
              className="toolpath-view-select h-7 border-0 bg-transparent px-1.5 text-xs text-foreground hover:bg-accent focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
            >
              {CAMERA_PRESETS.map((preset) => (
                <option key={preset} value={preset}>
                  {uiText(
                    preset === "isometric" ? "Isometric" : preset === "front" ? "Front" : "Top",
                  )}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            aria-label={uiText("Fit to view")}
            title={uiText("Fit to view")}
            onClick={() => {
              localControls.current?.fit();
              setCameraPreset("isometric");
            }}
            className={ICON_BUTTON}
          >
            <Maximize2 className="h-3.5 w-3.5" aria-hidden />
          </button>
          <span className="mx-0.5 h-4 w-px bg-border" aria-hidden />
          <button
            type="button"
            aria-pressed={onlyCurrentLayer}
            onClick={() => setOnlyCurrentLayer((value) => !value)}
            className={cn(TOGGLE, onlyCurrentLayer && "bg-accent text-foreground")}
          >
            {uiText("This layer")}
          </button>
          <button
            type="button"
            aria-pressed={showTravel}
            onClick={() => setShowTravel((value) => !value)}
            className={cn(TOGGLE, showTravel && "bg-accent text-foreground")}
          >
            {viewerCopy(i18n, "viewer.travel")}
          </button>
          {printerBedMm && (
            <button
              type="button"
              aria-pressed={showBed}
              onClick={() => setShowBed((value) => !value)}
              className={cn(TOGGLE, showBed && "bg-accent text-foreground")}
            >
              {viewerCopy(i18n, "viewer.bed", {
                x: String(printerBedMm.x),
                y: String(printerBedMm.y),
              })}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
