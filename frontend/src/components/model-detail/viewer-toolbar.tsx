"use client";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

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
  notifyScreenshotError = toast.error,
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
  notifyScreenshotError?: (message: string) => void;
}) {
  useUiLocale();
  const cluster =
    "flex bg-surface-container-lowest/90 backdrop-blur border border-outline-variant rounded overflow-hidden shadow-sm";
  const iconBtn =
    "flex h-11 w-11 items-center justify-center text-on-surface-variant transition-colors hover:bg-surface-container-high hover:text-primary sm:h-9 sm:w-9";

  return (
    <Localized>
      <div className="pointer-events-auto flex min-w-0 flex-wrap items-center gap-1.5">
        {/* 3D ↔ G-code toggle */}
        {hasGcode && (
          <div className={cluster}>
            <button
              onClick={() => setViewerMode("model")}
              className={`flex h-11 items-center gap-1.5 px-2.5 font-mono text-2xs uppercase tracking-wider transition-colors sm:h-9 ${
                viewerMode === "model"
                  ? "bg-accent text-accent-foreground"
                  : "text-on-surface-variant hover:bg-surface-container-high"
              }`}
              title={uiText("3D model view")}
            >
              <Code2 className="h-3.5 w-3.5" /> 3D
            </button>
            <button
              onClick={() => setViewerMode("gcode")}
              className={`flex h-11 items-center gap-1.5 px-2.5 font-mono text-2xs uppercase tracking-wider transition-colors sm:h-9 ${
                viewerMode === "gcode"
                  ? "bg-accent text-accent-foreground"
                  : "text-on-surface-variant hover:bg-surface-container-high"
              }`}
              title={uiText("G-code toolpath preview")}
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
                  className={`h-11 px-2.5 font-mono text-2xs uppercase tracking-wider transition-colors sm:h-9 ${
                    displayMode === m
                      ? "bg-accent text-accent-foreground"
                      : "text-on-surface-variant hover:bg-surface-container-high"
                  }`}
                >
                  {m === "wireframe"
                    ? uiText("Wire")
                    : m === "xray"
                      ? uiText("X-Ray")
                      : uiText("Solid")}
                </button>
              ))}
            </div>

            <div className={cluster}>
              <button
                onClick={() => controls.current?.fit()}
                className={`${iconBtn} border-r border-outline-variant`}
                title={uiText("Fit to view")}
                aria-label={uiText("Fit to view")}
              >
                <Maximize2 className="h-4 w-4" />
              </button>
              <button
                onClick={() => {
                  const screenshot = controls.current?.screenshot();
                  if (screenshot) {
                    void screenshot.catch(() =>
                      notifyScreenshotError("Screenshot could not be created"),
                    );
                  }
                }}
                disabled={!viewerReady}
                className={`${iconBtn} border-r border-outline-variant disabled:cursor-not-allowed disabled:opacity-50`}
                title={uiText("Screenshot")}
                aria-label={uiText("Screenshot")}
              >
                <Camera className="h-4 w-4" />
              </button>
              <button
                onClick={() => setShowGrid(!showGrid)}
                className={`${iconBtn} ${showGrid ? "text-primary bg-secondary-container" : ""}`}
                title={uiText("Build plate grid")}
                aria-label={uiText("Build plate grid")}
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
