"use client";

import { Camera, Code2, Grid3x3, Layers, Maximize2 } from "lucide-react";
import { toast } from "sonner";
import type { STLViewerControls, ViewerDisplayMode } from "@/components/stl-viewer";
import { Localized } from "@/components/ui/localized";

export type ViewerMode = "model" | "gcode";

/** Display modes in the order the toolbar offers them. */
const DISPLAY_MODES: readonly ViewerDisplayMode[] = ["solid", "xray", "wireframe"];

export function ViewerToolbar({
  displayMode,
  setDisplayMode,
  showGrid,
  setShowGrid,
  controls,
  viewerMode,
  setViewerMode,
  hasGcode,
  viewerReady,
}: {
  displayMode: ViewerDisplayMode;
  setDisplayMode: (m: ViewerDisplayMode) => void;
  showGrid: boolean;
  setShowGrid: (v: boolean) => void;
  controls: React.RefObject<STLViewerControls | null>;
  viewerMode: ViewerMode;
  setViewerMode: (m: ViewerMode) => void;
  hasGcode: boolean;
  viewerReady: boolean;
}) {
  const cluster =
    "flex bg-surface-container-lowest/90 backdrop-blur border border-outline-variant rounded overflow-hidden shadow-sm";
  const iconBtn =
    "w-9 h-9 flex items-center justify-center text-on-surface-variant hover:bg-surface-container-high hover:text-primary transition-colors";

  return (
    <Localized>
      <div className="absolute top-4 left-4 z-10 flex flex-wrap items-center gap-1.5">
        {/* 3D ↔ G-code toggle */}
        {hasGcode && (
          <div className={cluster}>
            <button
              onClick={() => setViewerMode("model")}
              className={`px-2.5 h-9 font-mono text-2xs uppercase tracking-wider transition-colors flex items-center gap-1.5 ${
                viewerMode === "model"
                  ? "bg-accent text-accent-foreground"
                  : "text-on-surface-variant hover:bg-surface-container-high"
              }`}
              title="3D model view"
            >
              <Code2 className="h-3.5 w-3.5" /> 3D
            </button>
            <button
              onClick={() => setViewerMode("gcode")}
              className={`px-2.5 h-9 font-mono text-2xs uppercase tracking-wider transition-colors flex items-center gap-1.5 ${
                viewerMode === "gcode"
                  ? "bg-accent text-accent-foreground"
                  : "text-on-surface-variant hover:bg-surface-container-high"
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
              {DISPLAY_MODES.map((m) => (
                <button
                  key={m}
                  onClick={() => setDisplayMode(m)}
                  className={`px-2.5 h-9 font-mono text-2xs uppercase tracking-wider transition-colors ${
                    displayMode === m
                      ? "bg-accent text-accent-foreground"
                      : "text-on-surface-variant hover:bg-surface-container-high"
                  }`}
                >
                  {m === "wireframe" ? "Wire" : m === "xray" ? "X-Ray" : "Solid"}
                </button>
              ))}
            </div>

            <div className={cluster}>
              <button
                onClick={() => controls.current?.fit()}
                className={`${iconBtn} border-r border-outline-variant`}
                title="Fit to view"
                aria-label="Fit to view"
              >
                <Maximize2 className="h-4 w-4" />
              </button>
              <button
                onClick={() => {
                  const screenshot = controls.current?.screenshot();
                  if (screenshot) {
                    void screenshot.catch(() => toast.error("Screenshot could not be created"));
                  }
                }}
                disabled={!viewerReady}
                className={`${iconBtn} border-r border-outline-variant disabled:cursor-not-allowed disabled:opacity-50`}
                title="Screenshot"
                aria-label="Screenshot"
              >
                <Camera className="h-4 w-4" />
              </button>
              <button
                onClick={() => setShowGrid(!showGrid)}
                className={`${iconBtn} ${showGrid ? "text-primary bg-secondary-container" : ""}`}
                title="Build plate grid"
                aria-label="Build plate grid"
              >
                <Grid3x3 className="h-4 w-4" />
              </button>
            </div>
          </>
        )}
      </div>
    </Localized>
  );
}
