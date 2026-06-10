"use client";

import { Camera, Code2, Grid3x3, Layers, Maximize2 } from "lucide-react";
import type { STLViewerControls, ViewerDisplayMode } from "@/components/stl-viewer";

export type ViewerMode = "model" | "gcode";

export function ViewerToolbar({
  displayMode,
  setDisplayMode,
  showGrid,
  setShowGrid,
  controls,
  viewerMode,
  setViewerMode,
  hasGcode,
}: {
  displayMode: ViewerDisplayMode;
  setDisplayMode: (m: ViewerDisplayMode) => void;
  showGrid: boolean;
  setShowGrid: (v: boolean) => void;
  controls: React.RefObject<STLViewerControls | null>;
  viewerMode: ViewerMode;
  setViewerMode: (m: ViewerMode) => void;
  hasGcode: boolean;
}) {
  const cluster =
    "flex bg-[var(--surface-container-lowest)]/90 backdrop-blur border border-[var(--outline-variant)] rounded overflow-hidden shadow-sm";
  const iconBtn =
    "w-9 h-9 flex items-center justify-center text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-high)] hover:text-[var(--primary)] transition-colors";

  return (
    <div className="absolute top-4 left-4 z-10 flex flex-wrap items-center gap-1.5">
      {/* 3D ↔ G-code toggle */}
      {hasGcode && (
        <div className={cluster}>
          <button
            onClick={() => setViewerMode("model")}
            className={`px-2.5 h-9 font-mono text-[11px] uppercase tracking-wider transition-colors flex items-center gap-1.5 ${
              viewerMode === "model"
                ? "bg-[var(--primary)] text-[var(--primary-foreground)]"
                : "text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-high)]"
            }`}
            title="3D model view"
          >
            <Code2 className="h-3.5 w-3.5" /> 3D
          </button>
          <button
            onClick={() => setViewerMode("gcode")}
            className={`px-2.5 h-9 font-mono text-[11px] uppercase tracking-wider transition-colors flex items-center gap-1.5 ${
              viewerMode === "gcode"
                ? "bg-[var(--primary)] text-[var(--primary-foreground)]"
                : "text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-high)]"
            }`}
            title="G-code toolpath preview"
          >
            <Layers className="h-3.5 w-3.5" /> GCode
          </button>
        </div>
      )}

      {/* 3D model controls */}
      {viewerMode === "model" && (
        <>
          <div className={cluster}>
            {(["solid", "xray", "wireframe"] as ViewerDisplayMode[]).map((m) => (
              <button
                key={m}
                onClick={() => setDisplayMode(m)}
                className={`px-2.5 h-9 font-mono text-[11px] uppercase tracking-wider transition-colors ${
                  displayMode === m
                    ? "bg-[var(--primary)] text-[var(--primary-foreground)]"
                    : "text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-high)]"
                }`}
              >
                {m === "wireframe" ? "Wire" : m === "xray" ? "X-Ray" : "Solid"}
              </button>
            ))}
          </div>

          <div className={cluster}>
            <button
              onClick={() => controls.current?.fit()}
              className={`${iconBtn} border-r border-[var(--outline-variant)]`}
              title="Fit to view"
            >
              <Maximize2 className="h-4 w-4" />
            </button>
            <button
              onClick={() => controls.current?.screenshot()}
              className={`${iconBtn} border-r border-[var(--outline-variant)]`}
              title="Screenshot"
            >
              <Camera className="h-4 w-4" />
            </button>
            <button
              onClick={() => setShowGrid(!showGrid)}
              className={`${iconBtn} ${showGrid ? "text-[var(--primary)] bg-[var(--secondary-container)]" : ""}`}
              title="Build plate grid"
            >
              <Grid3x3 className="h-4 w-4" />
            </button>
          </div>
        </>
      )}
    </div>
  );
}
