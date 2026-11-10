import { useState } from "react";
import { Camera, Minus, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { toast } from "sonner";
import type {
  STLViewerControls,
  ViewerDisplayMode,
  ViewerCameraPreset,
} from "@/components/stl-viewer";

export type ViewerMode = "model" | "gcode";
const DISPLAY_MODES: readonly ViewerDisplayMode[] = ["solid", "xray", "wireframe"];
const CAMERA_PRESETS: readonly ViewerCameraPreset[] = ["isometric", "front", "top"];

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
  filename,
  expanded = false,
  onExpand,
  showDimensions = true,
  onDimensions,
  notifyScreenshotError = toast.error,
}: {
  displayMode: ViewerDisplayMode;
  setDisplayMode: (mode: ViewerDisplayMode) => void;
  showGrid: boolean;
  setShowGrid: (visible: boolean) => void;
  controls: React.RefObject<STLViewerControls | null>;
  viewerMode: ViewerMode;
  setViewerMode: (mode: ViewerMode) => void;
  hasGcode: boolean;
  viewerReady: boolean;
  filename?: string;
  expanded?: boolean;
  onExpand?: () => void;
  showDimensions?: boolean;
  onDimensions?: (visible: boolean) => void;
  notifyScreenshotError?: (message: string) => void;
}) {
  useUiLocale();
  const [camera, setCamera] = useState<ViewerCameraPreset>("isometric");
  const buttonClass = (active: boolean) =>
    active ? "bg-accent text-accent-foreground" : "text-foreground";
  return (
    <div className="detail-viewer-controls">
      <div className="detail-preview-heading">
        <div className="min-w-0 flex flex-wrap items-baseline gap-3">
          <h2>{uiText("Model preview")}</h2>
          {filename && <span className="break-all text-xs text-muted-foreground">{filename}</span>}
        </div>
        <div className="flex shrink-0 gap-2">
          <Button
            variant="outline"
            size="sm"
            aria-label={uiText("Screenshot")}
            disabled={!viewerReady || viewerMode !== "model"}
            onClick={() => {
              const result = controls.current?.screenshot();
              if (result)
                void result.catch(() =>
                  notifyScreenshotError(uiText("Screenshot could not be created")),
                );
            }}
          >
            <Camera className="h-3.5 w-3.5" />
            {uiText("Capture")}
          </Button>
          {onExpand && (
            <Button variant="outline" size="sm" aria-pressed={expanded} onClick={onExpand}>
              {expanded ? uiText("Collapse preview") : uiText("Expand")}
            </Button>
          )}
        </div>
      </div>
      <div className="detail-viewer-commandbar">
        {hasGcode && (
          <div className="detail-viewer-control-group">
            <span>{uiText("Preview")}</span>
            <div>
              <Button
                size="sm"
                variant="ghost"
                aria-pressed={viewerMode === "model"}
                className={buttonClass(viewerMode === "model")}
                onClick={() => setViewerMode("model")}
              >
                {uiText("3D model")}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                aria-pressed={viewerMode === "gcode"}
                className={buttonClass(viewerMode === "gcode")}
                onClick={() => setViewerMode("gcode")}
              >
                {uiText("Layers")}
              </Button>
            </div>
          </div>
        )}
        {viewerMode === "model" && (
          <>
            <div className="detail-viewer-control-group">
              <span>{uiText("Display")}</span>
              <div>
                {DISPLAY_MODES.map((mode) => (
                  <Button
                    key={mode}
                    size="sm"
                    variant="ghost"
                    disabled={!viewerReady}
                    aria-pressed={displayMode === mode}
                    className={buttonClass(displayMode === mode)}
                    onClick={() => setDisplayMode(mode)}
                  >
                    {mode === "wireframe"
                      ? uiText("Wireframe")
                      : mode === "xray"
                        ? uiText("X-Ray")
                        : uiText("Solid")}
                  </Button>
                ))}
              </div>
            </div>
            <div className="detail-viewer-control-group">
              <span>{uiText("Overlays")}</span>
              <div>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={!viewerReady}
                  aria-label={uiText("Build plate grid")}
                  aria-pressed={showGrid}
                  className={buttonClass(showGrid)}
                  onClick={() => setShowGrid(!showGrid)}
                >
                  {uiText("Grid")}
                </Button>
                {onDimensions && (
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={!viewerReady}
                    aria-pressed={showDimensions}
                    className={buttonClass(showDimensions)}
                    onClick={() => onDimensions(!showDimensions)}
                  >
                    {uiText("Dimensions")}
                  </Button>
                )}
              </div>
            </div>
          </>
        )}
      </div>
      {viewerMode === "model" && (
        <div className="detail-viewer-dock">
          <div className="flex">
            {CAMERA_PRESETS.map((preset) => (
              <Button
                key={preset}
                size="sm"
                variant="ghost"
                disabled={!viewerReady}
                aria-pressed={camera === preset}
                className={buttonClass(camera === preset)}
                onClick={() => {
                  controls.current?.setView(preset);
                  setCamera(preset);
                }}
              >
                {preset === "isometric"
                  ? uiText("Isometric")
                  : preset === "front"
                    ? uiText("Front")
                    : uiText("Top")}
              </Button>
            ))}
          </div>
          <p>{uiText("Drag to orbit · Scroll to zoom")}</p>
          <div className="flex">
            <Button
              variant="ghost"
              size="icon-sm"
              disabled={!viewerReady}
              aria-label={uiText("Zoom out")}
              onClick={() => controls.current?.zoomOut()}
            >
              <Minus className="h-3 w-3" />
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={!viewerReady}
              aria-label={uiText("Fit to view")}
              onClick={() => {
                controls.current?.fit();
                setCamera("isometric");
              }}
            >
              {uiText("Fit")}
            </Button>
            <Button
              variant="ghost"
              size="icon-sm"
              disabled={!viewerReady}
              aria-label={uiText("Zoom in")}
              onClick={() => controls.current?.zoomIn()}
            >
              <Plus className="h-3 w-3" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
