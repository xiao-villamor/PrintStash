"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import { OrbitControls, PerspectiveCamera } from "@react-three/drei";
import * as THREE from "three";
import { AlertTriangle, Layers, Loader2 } from "lucide-react";
import { authHeaders, getUrl } from "@/lib/api/request";

// ---- Types ----

interface LayerRange {
  z: number;
  vertexStart: number; // index into extrudePositions (in floats / 3)
  vertexCount: number;
}

export interface ToolpathData {
  extrudePositions: Float32Array;
  extrudeColors: Float32Array;
  travelPositions: Float32Array;
  layerRanges: LayerRange[];
  cumulativeVertices: Uint32Array; // cumulative vertex count per layer (length = layerRanges.length + 1)
  totalLayers: number;
  bounds: {
    sizeX: number; sizeY: number; sizeZ: number;
    maxDim: number;
  };
}

// ---- G-code Parser ----

function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  const a = s * Math.min(l, 1 - l);
  const f = (n: number) => {
    const k = (n + h / 30) % 12;
    return l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1));
  };
  return [f(0), f(8), f(4)];
}

export function parseGcode(text: string): ToolpathData {
  let cx = 0, cy = 0, cz = 0, ce = 0;
  let relXYZ = false, relE = false;

  const extrudeSegs: number[] = [];
  const travelSegs: number[] = [];

  const layerRanges: LayerRange[] = [];
  let currentZ = -1;
  let layerVertStart = 0; // in vertex units (floats/3)

  let minX = Infinity, maxX = -Infinity;
  let minY = Infinity, maxY = -Infinity;
  let minZ = Infinity, maxZ = -Infinity;

  const lines = text.split("\n");

  for (const rawLine of lines) {
    let line = rawLine;
    const semi = line.indexOf(";");
    if (semi >= 0) line = line.slice(0, semi);
    line = line.trim();
    if (!line) continue;

    const tokens = line.split(/\s+/);
    const op = tokens[0].toUpperCase();

    if (op === "G90") { relXYZ = false; continue; }
    if (op === "G91") { relXYZ = true; continue; }
    if (op === "M82") { relE = false; continue; }
    if (op === "M83") { relE = true; continue; }
    if (op !== "G0" && op !== "G1" && op !== "G00" && op !== "G01") continue;

    let nx = cx, ny = cy, nz = cz, ne = ce;
    let hasE = false;

    for (let i = 1; i < tokens.length; i++) {
      const t = tokens[i].toUpperCase();
      if (t.length < 2) continue;
      const k = t[0];
      const v = parseFloat(t.slice(1));
      if (isNaN(v)) continue;
      if (k === "X") nx = relXYZ ? cx + v : v;
      else if (k === "Y") ny = relXYZ ? cy + v : v;
      else if (k === "Z") nz = relXYZ ? cz + v : v;
      else if (k === "E") { ne = relE ? ce + v : v; hasE = true; }
    }

    // Layer change: Z increases
    if (nz > cz && nz > 0.01) {
      if (currentZ >= 0) {
        const vCount = extrudeSegs.length / 3 - layerVertStart;
        layerRanges.push({ z: currentZ, vertexStart: layerVertStart, vertexCount: vCount });
      }
      currentZ = nz;
      layerVertStart = extrudeSegs.length / 3;
    } else if (currentZ < 0 && nz >= 0) {
      currentZ = nz;
    }

    const isExtrusion = hasE && (relE ? ne > 0 : ne > ce + 0.0001);
    if (hasE) ce = ne;

    // Track bounds only from extrusion moves so start-gcode travel to X0 Y0 doesn't skew center
    if (isExtrusion) {
      if (nx < minX) minX = nx; if (nx > maxX) maxX = nx;
      if (ny < minY) minY = ny; if (ny > maxY) maxY = ny;
      if (nz < minZ) minZ = nz; if (nz > maxZ) maxZ = nz;
    }

    const dx = nx - cx, dy = ny - cy, dz = nz - cz;
    if (dx !== 0 || dy !== 0 || dz !== 0) {
      // Map: three.x = gcodeX, three.y = gcodeZ (height), three.z = -gcodeY
      if (isExtrusion) {
        extrudeSegs.push(cx, cz, -cy, nx, nz, -ny);
      } else {
        travelSegs.push(cx, cz, -cy, nx, nz, -ny);
      }
    }

    cx = nx; cy = ny; cz = nz;
  }

  // Push final layer
  if (currentZ >= 0) {
    const vCount = extrudeSegs.length / 3 - layerVertStart;
    layerRanges.push({ z: currentZ, vertexStart: layerVertStart, vertexCount: vCount });
  }

  if (minX === Infinity) { minX = 0; maxX = 200; minY = 0; maxY = 200; minZ = 0; maxZ = 20; }

  // Center coordinates
  const centerX = (minX + maxX) / 2;
  const centerY = (minY + maxY) / 2; // gcode Y
  const centerZ = (minZ + maxZ) / 2; // gcode Z (height)

  const extArr = new Float32Array(extrudeSegs);
  const travArr = new Float32Array(travelSegs);

  for (let i = 0; i < extArr.length; i += 3) {
    extArr[i] -= centerX;
    extArr[i + 1] -= centerZ;
    extArr[i + 2] += centerY; // three.z was -gcodeY, center is -(centerY), shift = +centerY
  }
  for (let i = 0; i < travArr.length; i += 3) {
    travArr[i] -= centerX;
    travArr[i + 1] -= centerZ;
    travArr[i + 2] += centerY;
  }

  // Per-vertex colors based on Y (height) in three.js space
  const totalVerts = extArr.length / 3;
  const colArr = new Float32Array(totalVerts * 3);
  const heightRange = maxZ - minZ || 1;

  for (let vi = 0; vi < totalVerts; vi++) {
    const threeY = extArr[vi * 3 + 1]; // centered, three.y = gcodeZ - centerZ
    const gcodeZ = threeY + centerZ;
    const t = Math.max(0, Math.min(1, (gcodeZ - minZ) / heightRange));
    // Blue (240°) at bottom → red (0°) at top
    const hue = (1 - t) * 240;
    const [r, g, b] = hslToRgb(hue, 0.9, 0.55);
    colArr[vi * 3] = r;
    colArr[vi * 3 + 1] = g;
    colArr[vi * 3 + 2] = b;
  }

  // Cumulative vertex counts for layer slider
  const cumulative = new Uint32Array(layerRanges.length + 1);
  cumulative[0] = 0;
  for (let i = 0; i < layerRanges.length; i++) {
    cumulative[i + 1] = cumulative[i] + layerRanges[i].vertexCount;
  }

  const sizeX = maxX - minX;
  const sizeY = maxY - minY;
  const sizeZ = maxZ - minZ;

  return {
    extrudePositions: extArr,
    extrudeColors: colArr,
    travelPositions: travArr,
    layerRanges,
    cumulativeVertices: cumulative,
    totalLayers: layerRanges.length,
    bounds: { sizeX, sizeY, sizeZ, maxDim: Math.max(sizeX, sizeY, sizeZ) || 1 },
  };
}

// ---- Three.js Scene ----

function GcodeScene({
  data,
  currentLayer,
  showTravel,
  showBed,
  printerBedMm,
}: {
  data: ToolpathData;
  currentLayer: number;
  showTravel: boolean;
  showBed: boolean;
  printerBedMm: { x: number; y: number } | null;
}) {
  const { camera } = useThree();
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
  const extrudeLines = useMemo(() => new THREE.LineSegments(extrudeGeo, extrudeMat), [extrudeGeo, extrudeMat]);
  const travelLines  = useMemo(() => new THREE.LineSegments(travelGeo,  travelMat),  [travelGeo,  travelMat]);

  // Bed geometry (actual mm dimensions — gcode coords are real mm)
  const bedGeo = useMemo(() => {
    if (!printerBedMm) return null;
    return new THREE.PlaneGeometry(printerBedMm.x, printerBedMm.y);
  }, [printerBedMm]);
  const bedEdgesGeo = useMemo(() => bedGeo ? new THREE.EdgesGeometry(bedGeo) : null, [bedGeo]);

  // Update drawRange directly on the stable geometry — no ref gymnastics needed
  useEffect(() => {
    const count = data.cumulativeVertices[currentLayer + 1] ?? data.extrudePositions.length / 3;
    extrudeGeo.setDrawRange(0, count);
  }, [currentLayer, data, extrudeGeo]);

  // Reset camera and OrbitControls when data changes
  useEffect(() => {
    const d = data.bounds.maxDim;
    camera.position.set(d * 0.8, d * 0.9, d * 1.2);
    if (orbitRef.current) {
      orbitRef.current.target.set(0, 0, 0);
      orbitRef.current.update();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  const gridHalfSize = showBed && printerBedMm
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
        position={[data.bounds.maxDim * 0.8, data.bounds.maxDim * 0.9, data.bounds.maxDim * 1.2]}
      />
      <ambientLight intensity={0.8} />
      <primitive object={extrudeLines} />
      {showTravel && data.travelPositions.length > 0 && (
        <primitive object={travelLines} />
      )}

      {/* Bed platform (only in bed-fit mode) */}
      {showBed && bedGeo && bedEdgesGeo && printerBedMm && (
        <group position={[0, floorY - 0.5, 0]} rotation={[-Math.PI / 2, 0, 0]}>
          <mesh geometry={bedGeo}>
            <meshStandardMaterial color="#1e3a5f" transparent opacity={0.15} side={THREE.DoubleSide} />
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
      {!showBed && (
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

interface EBState { hasError: boolean }
class GcodeErrorBoundary extends React.Component<
  { children: React.ReactNode },
  EBState
> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { hasError: false };
  }
  static getDerivedStateFromError(): EBState { return { hasError: true }; }
  render() {
    if (this.state.hasError) {
      return (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-muted-foreground">
          <AlertTriangle className="h-8 w-8" />
          <span className="font-mono text-xs">G-code render failed</span>
        </div>
      );
    }
    return this.props.children;
  }
}

// ---- Public Component ----

export interface GcodeViewerProps {
  url: string;
  printerBedMm?: { x: number; y: number } | null;
  screenshotName?: string;
}

export function GcodeViewer({ url, printerBedMm = null }: GcodeViewerProps) {
  const [data, setData] = useState<ToolpathData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [currentLayer, setCurrentLayer] = useState(0);
  const [showTravel, setShowTravel] = useState(false);
  const [showBed, setShowBed] = useState(true);

  useEffect(() => {
    setLoading(true);
    setError(null);
    setData(null);
    setCurrentLayer(0);

    fetch(getUrl(url), { headers: authHeaders() })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.text();
      })
      .then((text) => {
        const parsed = parseGcode(text);
        setData(parsed);
        setCurrentLayer(parsed.totalLayers - 1);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Failed to load G-code"))
      .finally(() => setLoading(false));
  }, [url]);

  if (loading) {
    return (
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-muted-foreground">
        <Loader2 className="h-8 w-8 animate-spin" />
        <span className="font-mono text-xs">Parsing G-code…</span>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-muted-foreground">
        <AlertTriangle className="h-8 w-8" />
        <span className="font-mono text-xs">{error ?? "No toolpath data"}</span>
      </div>
    );
  }

  if (data.totalLayers === 0) {
    return (
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-muted-foreground">
        <Layers className="h-8 w-8 opacity-40" />
        <span className="font-mono text-xs">No toolpath found in file</span>
      </div>
    );
  }

  return (
    <div className="relative h-full w-full">
      <GcodeErrorBoundary>
        <Canvas className="h-full w-full" gl={{ preserveDrawingBuffer: true }}>
          <GcodeScene
            data={data}
            currentLayer={currentLayer}
            showTravel={showTravel}
            showBed={showBed}
            printerBedMm={printerBedMm ?? null}
          />
        </Canvas>
      </GcodeErrorBoundary>

      {/* Layer controls overlay */}
      <div className="absolute bottom-4 left-1/2 -translate-x-1/2 z-10 w-[min(90%,440px)]">
        <div className="bg-[var(--surface-container-lowest)]/90 backdrop-blur border border-[var(--outline-variant)] rounded px-3 py-2 flex flex-col gap-1.5">
          <div className="flex items-center justify-between gap-2">
            <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              Layer {currentLayer + 1} / {data.totalLayers}
              {data.layerRanges[currentLayer] && (
                <> · Z {data.layerRanges[currentLayer].z.toFixed(2)} mm</>
              )}
            </span>
            <div className="flex items-center gap-1">
              <button
                onClick={() => setShowTravel((v) => !v)}
                className={`font-mono text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded border transition-colors ${
                  showTravel
                    ? "border-[var(--primary)] text-[var(--primary)] bg-[var(--secondary-container)]"
                    : "border-[var(--outline-variant)] text-muted-foreground hover:text-foreground"
                }`}
              >
                Travel
              </button>
              {printerBedMm && (
                <button
                  onClick={() => setShowBed((v) => !v)}
                  className={`font-mono text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded border transition-colors ${
                    showBed
                      ? "border-blue-500 dark:border-blue-400 text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-950/30"
                      : "border-[var(--outline-variant)] text-muted-foreground hover:text-foreground"
                  }`}
                >
                  Bed {printerBedMm.x}×{printerBedMm.y}
                </button>
              )}
            </div>
          </div>
          <input
            type="range"
            min={0}
            max={data.totalLayers - 1}
            value={currentLayer}
            onChange={(e) => setCurrentLayer(Number(e.target.value))}
            className="w-full h-1.5 accent-[var(--primary)] cursor-pointer"
          />
        </div>
      </div>
    </div>
  );
}
