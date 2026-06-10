"use client";

import {
  Axis3d,
  Box,
  Camera,
  Crosshair,
  Grid3x3,
  Maximize2,
} from "lucide-react";

import type { STLViewerControls, ViewerDisplayMode } from "@/components/stl-viewer";

export function ViewerToolbar({
  displayMode,
  setDisplayMode,
  showGrid,
  setShowGrid,
  showAxes,
  setShowAxes,
  showBoundingBox,
  setShowBoundingBox,
  controls,
}: {
  displayMode: ViewerDisplayMode;
  setDisplayMode: (m: ViewerDisplayMode) => void;
  showGrid: boolean;
  setShowGrid: (v: boolean) => void;
  showAxes: boolean;
  setShowAxes: (v: boolean) => void;
  showBoundingBox: boolean;
  setShowBoundingBox: (v: boolean) => void;
  controls: React.RefObject<STLViewerControls | null>;
}) {
  const modes: { key: ViewerDisplayMode; label: string }[] = [
    { key: "solid", label: "Solid" },
    { key: "xray", label: "X-Ray" },
    { key: "wireframe", label: "Wire" },
  ];
  const cluster =
    "flex bg-[var(--surface-container-lowest)]/90 backdrop-blur border border-[var(--outline-variant)] rounded overflow-hidden shadow-sm";
  const iconBtn =
    "w-9 h-9 flex items-center justify-center text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-high)] hover:text-[var(--primary)] transition-colors";

  return (
    <div className="absolute top-4 left-4 z-10 flex flex-wrap items-center gap-1.5">
      <div className={cluster}>
        {modes.map((m) => (
          <button
            key={m.key}
            onClick={() => setDisplayMode(m.key)}
            className={`px-2.5 h-9 font-mono text-[11px] uppercase tracking-wider transition-colors ${
              displayMode === m.key
                ? "bg-[var(--primary)] text-[var(--primary-foreground)]"
                : "text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-high)]"
            }`}
            title={`${m.label} view`}
          >
            {m.label}
          </button>
        ))}
      </div>
      <div className={cluster}>
        <button onClick={() => controls.current?.fit()} className={`${iconBtn} border-r border-[var(--outline-variant)]`} title="Fit view">
          <Maximize2 className="h-4 w-4" />
        </button>
        <button onClick={() => controls.current?.center()} className={`${iconBtn} border-r border-[var(--outline-variant)]`} title="Center">
          <Crosshair className="h-4 w-4" />
        </button>
        <button onClick={() => controls.current?.screenshot()} className={iconBtn} title="Screenshot">
          <Camera className="h-4 w-4" />
        </button>
      </div>
      <div className={cluster}>
        <button
          onClick={() => setShowGrid(!showGrid)}
          className={`${iconBtn} border-r border-[var(--outline-variant)] ${showGrid ? "text-[var(--primary)] bg-[var(--secondary-container)]" : ""}`}
          title="Build plate grid"
        >
          <Grid3x3 className="h-4 w-4" />
        </button>
        <button
          onClick={() => setShowAxes(!showAxes)}
          className={`${iconBtn} border-r border-[var(--outline-variant)] ${showAxes ? "text-[var(--primary)] bg-[var(--secondary-container)]" : ""}`}
          title="XYZ axes"
        >
          <Axis3d className="h-4 w-4" />
        </button>
        <button
          onClick={() => setShowBoundingBox(!showBoundingBox)}
          className={`${iconBtn} ${showBoundingBox ? "text-[var(--primary)] bg-[var(--secondary-container)]" : ""}`}
          title="Bounding box"
        >
          <Box className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
