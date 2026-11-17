import { uiText } from "@/lib/locale";
import { formatNumber } from "@/lib/format";
import { ApiError } from "@/lib/errors";
import { createPortal } from "react-dom";
import { Input } from "@/components/ui/input";
import { ToolpathLayout, type ToolpathControlContainers } from "@/components/toolpath-layout";
import { Button } from "@/components/ui/button";

import React, { useEffect, useMemo, useRef, useState, type ComponentType } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import { OrbitControls, PerspectiveCamera } from "@react-three/drei";
import * as THREE from "three";
import {
  AlertTriangle,
  Check,
  ChevronLeft,
  ChevronRight,
  Layers,
  Loader2,
  Minus,
  Plus,
} from "lucide-react";
import "./gcode-viewer.css";
import { getDerivedText } from "@/lib/api/request";
import { subscribeEvents } from "@/lib/events";
import { useOptionalI18n, type MessageKey } from "@/lib/i18n";
import { previewPixelRatio, usePreviewPreferences } from "@/lib/preview-preferences";
import { fitCameraToBounds, viewerPresetDirection } from "@/lib/thumbnail-camera";
import type { ToolpathData } from "@/lib/gcode";
import { parseGcodeInWorker, ToolpathParseError } from "@/lib/gcode-worker-client";

import type { ViewerControls, ViewerCameraPreset } from "@/components/stl-viewer";

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
  onControlsReady: (controls: ViewerControls | null) => void;
  screenshotName: string;
}) {
  const { camera, gl, scene, size } = useThree();
  const orbitRef = useRef<any>(null);
  const presetRef = useRef<ViewerCameraPreset>("isometric");

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

  // R3F does not own <primitive> resources. Release the previous plate's GPU
  // buffers when switching plates, including when the next preview fails.
  useEffect(
    () => () => {
      extrudeGeo.dispose();
      travelGeo.dispose();
    },
    [extrudeGeo, travelGeo],
  );
  useEffect(
    () => () => {
      extrudeMat.dispose();
      travelMat.dispose();
    },
    [extrudeMat, travelMat],
  );
  useEffect(
    () => () => {
      bedGeo?.dispose();
      bedEdgesGeo?.dispose();
    },
    [bedGeo, bedEdgesGeo],
  );

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
    () =>
      viewerPresetDirection("isometric")
        .multiplyScalar(data.bounds.maxDim * 1.75)
        .toArray(),
    [data],
  );

  useEffect(() => {
    function setView(preset: ViewerCameraPreset) {
      presetRef.current = preset;
      // Match the mesh preview's upright top view as well as its isometric angle.
      camera.up.set(0, preset === "top" ? 0 : 1, preset === "top" ? -1 : 0);
      const direction = viewerPresetDirection(preset);
      const aspect = gl.domElement.clientWidth / Math.max(gl.domElement.clientHeight, 1);
      const fit = fitCameraToBounds(
        new THREE.Vector3(data.bounds.sizeX, data.bounds.sizeZ, data.bounds.sizeY),
        aspect,
        45,
        direction,
      );
      camera.position.copy(fit.position);
      orbitRef.current?.target.set(0, 0, 0);
      camera.lookAt(0, 0, 0);
      orbitRef.current?.update();
    }
    function zoom(factor: number) {
      const target = orbitRef.current?.target ?? new THREE.Vector3();
      camera.position.sub(target).multiplyScalar(factor).add(target);
      orbitRef.current?.update();
    }
    setView(presetRef.current);
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
  }, [data, camera, gl, scene, screenshotName, onControlsReady, size.width, size.height]);

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
export interface GcodeViewerProps {
  url: string;
  printerBedMm?: { x: number; y: number } | null;
  screenshotName?: string;
  showGrid?: boolean;
  showDimensions?: boolean;
  onShowGridChange?: (visible: boolean) => void;
  onShowDimensionsChange?: (visible: boolean) => void;
  /** The parent supplies camera and shared overlay controls. */
  sharedControls?: boolean;
  /** Render controls beside the parent-owned source picker without remounting it. */
  controlsContainers?: ToolpathControlContainers;
  onControlsReady?: (controls: ViewerControls | null) => void;
  canvasRenderer?: ComponentType<CanvasRendererProps>;
  toolpathParser?: typeof parseGcodeInWorker;
  toolpathFetcher?: typeof getDerivedText;
  privateEventsEnabled?: boolean;
}

export function GcodeViewer({
  url,
  printerBedMm = null,
  screenshotName = "toolpath",
  showGrid: externalGrid,
  showDimensions: externalDimensions,
  onShowGridChange,
  onShowDimensionsChange,
  sharedControls = false,
  controlsContainers,
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
  const [grid, setGrid] = useState(externalGrid ?? true);
  const [dimensions, setDimensions] = useState(externalDimensions ?? true);
  const showGrid = externalGrid ?? grid;
  const showDimensions = externalDimensions ?? dimensions;
  const [retry, setRetry] = useState(0);
  const [loaded, setLoaded] = useState<LoadedToolpath | null>(null);
  const [currentLayer, setCurrentLayer] = useState(0);
  const [layerEntry, setLayerEntry] = useState<string | null>(null);
  const [onlyCurrentLayer, setOnlyCurrentLayer] = useState(false);
  const [showTravel, setShowTravel] = useState(false);
  const [showBed, setShowBed] = useState(true);
  const localControls = useRef<ViewerControls | null>(null);
  const [cameraPreset, setCameraPreset] = useState<ViewerCameraPreset>("isometric");
  const connectControls = React.useCallback(
    (controls: ViewerControls | null) => {
      localControls.current = controls;
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
        setLayerEntry(null);
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

  const layout = (canvas: React.ReactNode, layers?: React.ReactNode, view?: React.ReactNode) =>
    controlsContainers === undefined ? (
      <ToolpathLayout layers={layers} view={view} viewAvailable={view !== undefined}>
        {canvas}
      </ToolpathLayout>
    ) : (
      <>
        {canvas}
        {controlsContainers.layers && createPortal(layers, controlsContainers.layers)}
        {controlsContainers.view && createPortal(view, controlsContainers.view)}
      </>
    );

  if (loading || current?.preparing) {
    return layout(
      <div role="status" className="toolpath-notice">
        <Loader2 className="h-8 w-8 animate-spin" />
        <span className="font-mono text-xs">
          {current?.preparing ? viewerCopy(i18n, "viewer.preparingToolpath") : loadingCopy}
        </span>
      </div>,
    );
  }

  if (errorKind === "disabled") {
    return layout(
      <div role="status" className="toolpath-notice">
        <span className="font-mono text-xs">{errorCopy}</span>
      </div>,
    );
  }

  if (errorKind || !data) {
    return layout(
      <div role="alert" className="toolpath-notice">
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
      </div>,
    );
  }

  if (data.totalLayers === 0) {
    return layout(
      <div className="toolpath-notice">
        <Layers className="h-8 w-8 opacity-40" />
        <span className="font-mono text-xs">{noToolpathCopy}</span>
      </div>,
    );
  }

  const selectLayer = (layer: number) => {
    setCurrentLayer(layer);
    setLayerEntry(null);
  };
  const commitLayer = () => {
    if (layerEntry === null) return;
    const number = Number(layerEntry);
    if (Number.isInteger(number) && number >= 1 && number <= data.totalLayers) {
      setCurrentLayer(number - 1);
    }
    setLayerEntry(null);
  };
  const toggle = (
    label: string,
    checked: boolean,
    change: (value: boolean) => void,
    description?: string,
  ) => (
    <Button
      variant="ghost"
      aria-label={label}
      aria-pressed={checked}
      onClick={() => change(!checked)}
      className="toolpath-toggle"
    >
      <span className="toolpath-toggle-copy">
        <span>{label}</span>
        {description && <span className="toolpath-toggle-description">{description}</span>}
      </span>
      <span className="toolpath-check" aria-hidden>
        {checked && <Check className="h-3.5 w-3.5" />}
      </span>
    </Button>
  );

  return layout(
    <div className="relative h-full w-full" title={uiText("Drag to orbit · Scroll to zoom")}>
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
    </div>,
    <section className="toolpath-section" aria-label={uiText("Layers")}>
      <div className="flex items-center justify-between gap-2">
        <h3 className="toolpath-section-title">{uiText("Layers")}</h3>
        <span className="font-mono text-xs text-muted-foreground">
          {uiText("{value1} mm", {
            value1: formatNumber(data.layerRanges[currentLayer]?.z ?? 0, {
              maximumFractionDigits: 2,
            }),
          })}
        </span>
      </div>
      <div className="toolpath-layer-entry">
        <Button
          variant="outline"
          size="icon-sm"
          aria-label={uiText("Previous layer")}
          disabled={currentLayer <= 0}
          onClick={() => selectLayer(currentLayer - 1)}
        >
          <ChevronLeft className="h-4 w-4" aria-hidden />
        </Button>
        <Input
          type="number"
          inputMode="numeric"
          min={1}
          max={data.totalLayers}
          step={1}
          aria-label={uiText("Layer number")}
          value={layerEntry ?? currentLayer + 1}
          onChange={(event) => setLayerEntry(event.target.value)}
          onBlur={commitLayer}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              commitLayer();
            }
            if (event.key === "Escape") {
              event.preventDefault();
              setLayerEntry(null);
            }
          }}
          className="min-w-0 font-mono text-center"
        />
        <span className="whitespace-nowrap font-mono text-xs text-muted-foreground">
          / {data.totalLayers}
        </span>
        <Button
          variant="outline"
          size="icon-sm"
          aria-label={uiText("Next layer")}
          disabled={currentLayer >= data.totalLayers - 1}
          onClick={() => selectLayer(currentLayer + 1)}
        >
          <ChevronRight className="h-4 w-4" aria-hidden />
        </Button>
      </div>
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
        onChange={(event) => selectLayer(Number(event.target.value))}
        className="toolpath-layer-slider"
      />
      {toggle(uiText("This layer"), onlyCurrentLayer, setOnlyCurrentLayer)}
    </section>,
    <>
      {!sharedControls && (
        <section className="toolpath-section" aria-label={uiText("Camera view")}>
          <label className="toolpath-field">
            <span className="toolpath-section-title">{uiText("Camera view")}</span>
            <select
              value={cameraPreset}
              onChange={(event) => {
                const preset = CAMERA_PRESETS.find((item) => item === event.target.value);
                if (!preset) return;
                localControls.current?.setView(preset);
                setCameraPreset(preset);
              }}
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
          <div className="toolpath-camera-actions">
            <Button
              variant="outline"
              size="icon-sm"
              aria-label={uiText("Zoom out")}
              onClick={() => localControls.current?.zoomOut()}
            >
              <Minus className="h-4 w-4" aria-hidden />
            </Button>
            <Button
              variant="outline"
              size="sm"
              aria-label={uiText("Fit to view")}
              onClick={() => {
                localControls.current?.fit();
                setCameraPreset("isometric");
              }}
            >
              {uiText("Fit")}
            </Button>
            <Button
              variant="outline"
              size="icon-sm"
              aria-label={uiText("Zoom in")}
              onClick={() => localControls.current?.zoomIn()}
            >
              <Plus className="h-4 w-4" aria-hidden />
            </Button>
          </div>
        </section>
      )}
      <section className="toolpath-section" aria-label={uiText("Display")}>
        <h3 className="toolpath-section-title">{uiText("Display")}</h3>
        <div className="toolpath-option-group">
          {toggle(
            viewerCopy(i18n, "viewer.travel"),
            showTravel,
            setShowTravel,
            uiText("Non-printing moves"),
          )}
          {!sharedControls &&
            (externalGrid === undefined || onShowGridChange) &&
            toggle(uiText("Build plate grid"), showGrid, onShowGridChange ?? setGrid)}
          {printerBedMm &&
            toggle(
              viewerCopy(i18n, "viewer.bed", {
                x: String(printerBedMm.x),
                y: String(printerBedMm.y),
              }),
              showBed,
              setShowBed,
              uiText("Bed position is illustrative"),
            )}
          {!sharedControls &&
            (externalDimensions === undefined || onShowDimensionsChange) &&
            toggle(uiText("Dimensions"), showDimensions, onShowDimensionsChange ?? setDimensions)}
        </div>
        {!printerBedMm && (
          <p className="toolpath-view-note">
            {uiText("Printer bed size unknown; grid is illustrative")}
          </p>
        )}
      </section>
      {showDimensions && (
        <section className="toolpath-section" aria-label={uiText("Toolpath bounds")}>
          <div className="toolpath-bounds-heading">
            <h3 className="toolpath-section-title">{uiText("Toolpath bounds")}</h3>
            <span className="toolpath-bounds-unit">mm</span>
          </div>
          <dl className="toolpath-bounds-grid">
            {(
              [
                ["X", data.bounds.sizeX],
                ["Y", data.bounds.sizeY],
                ["Z", data.bounds.sizeZ],
              ] as const
            ).map(([axis, value]) => (
              <div key={axis}>
                <dt>{axis}</dt>
                <dd>{formatNumber(value, { maximumFractionDigits: 1 })}</dd>
              </div>
            ))}
          </dl>
          <p className="toolpath-view-note">
            {uiText("Extrusion bounds (includes any purge; excludes travel)")}
          </p>
        </section>
      )}
    </>,
  );
}
