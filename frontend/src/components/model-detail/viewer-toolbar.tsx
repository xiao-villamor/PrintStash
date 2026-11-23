import { useEffect, useState } from "react";
import { Camera, Grid2X2, Minus, Plus, Ruler } from "lucide-react";
import { Button } from "@/components/ui/button";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { toast } from "sonner";
import type {
  ViewerControls,
  ViewerDisplayMode,
  ViewerCameraPreset,
} from "@/components/stl-viewer";

import { ViewerModeSwitch, type ViewerMode } from "@/components/viewer-mode-switch";
import type { ToolpathAvailability } from "@/lib/toolpath-preview";

export type { ViewerMode } from "@/components/viewer-mode-switch";
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
  availability,
  gcodeOnly = false,
  viewerReady,
  filename,
  expanded = false,
  onExpand,
  showDimensions = true,
  onDimensions,
  notifyScreenshotError = toast.error,
  camera,
  setCamera,
}: {
  displayMode: ViewerDisplayMode;
  setDisplayMode: (mode: ViewerDisplayMode) => void;
  showGrid: boolean;
  setShowGrid: (visible: boolean) => void;
  controls: React.RefObject<ViewerControls | null>;
  viewerMode: ViewerMode;
  setViewerMode: (mode: ViewerMode) => void;
  hasGcode: boolean;
  availability?: ToolpathAvailability;
  gcodeOnly?: boolean;
  viewerReady: boolean;
  filename?: string;
  expanded?: boolean;
  onExpand?: () => void;
  showDimensions?: boolean;
  onDimensions?: (visible: boolean) => void;
  notifyScreenshotError?: (message: string) => void;
  camera?: ViewerCameraPreset | null;
  setCamera?: (preset: ViewerCameraPreset) => void;
}) {
  useUiLocale();
  const [localCamera, setLocalCamera] = useState<ViewerCameraPreset>("isometric");
  const activeCamera = camera === undefined ? localCamera : camera;
  const chooseCamera = setCamera ?? setLocalCamera;
  useEffect(() => {
    if (viewerReady && activeCamera !== null) controls.current?.setView(activeCamera);
  }, [viewerReady, viewerMode, activeCamera, controls]);
  const buttonClass = (active: boolean) =>
    active
      ? "bg-accent text-accent-foreground hover:bg-accent"
      : "text-foreground hover:bg-muted hover:text-foreground";
  return (
    <div className="detail-viewer-controls" data-viewer-mode={viewerMode}>
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
            disabled={!viewerReady || gcodeOnly}
            onClick={() => {
              const result = controls.current?.screenshot();
              if (result)
                void result.catch(() =>
                  notifyScreenshotError(uiText("Screenshot could not be created")),
                );
            }}
          >
            <Camera className="h-3.5 w-3.5" />
            {uiText("Screenshot")}
          </Button>
          {onExpand && (
            <Button
              data-preview-expand
              variant="outline"
              size="sm"
              aria-pressed={expanded}
              onClick={onExpand}
            >
              {expanded ? uiText("Collapse preview") : uiText("Expand")}
            </Button>
          )}
        </div>
      </div>
      <div className="detail-viewer-commandbar">
        <ViewerModeSwitch
          className="detail-viewer-control-group detail-mode-switch"
          mode={viewerMode}
          onChange={setViewerMode}
          hasGcode={hasGcode}
          availability={availability}
          gcodeOnly={gcodeOnly}
        />
        <div className="detail-viewer-control-group" role="group" aria-label={uiText("Overlays")}>
          <Button
            size="sm"
            variant="ghost"
            disabled={!viewerReady}
            aria-label={uiText("Build plate grid")}
            aria-pressed={showGrid}
            className={buttonClass(showGrid)}
            onClick={() => setShowGrid(!showGrid)}
          >
            <Grid2X2 className="h-4 w-4" aria-hidden />
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
              <Ruler className="h-4 w-4" aria-hidden />
              {uiText("Dimensions")}
            </Button>
          )}
        </div>
        {viewerMode === "model" && (
          <select
            aria-label={uiText("Display")}
            className="detail-preview-style"
            disabled={!viewerReady}
            value={displayMode}
            onChange={(event) => {
              const selected = DISPLAY_MODES.find((mode) => mode === event.target.value);
              if (selected) setDisplayMode(selected);
            }}
          >
            {DISPLAY_MODES.map((mode) => (
              <option key={mode} value={mode}>
                {mode === "wireframe"
                  ? uiText("Wireframe")
                  : mode === "xray"
                    ? uiText("X-Ray")
                    : uiText("Solid")}
              </option>
            ))}
          </select>
        )}
      </div>
      <div className="detail-viewer-dock">
        <div className="flex">
          {CAMERA_PRESETS.map((preset) => (
            <Button
              key={preset}
              size="sm"
              variant="ghost"
              disabled={!viewerReady}
              aria-pressed={activeCamera === preset}
              className={buttonClass(activeCamera === preset)}
              onClick={() => {
                controls.current?.setView(preset);
                chooseCamera(preset);
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
              chooseCamera("isometric");
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
    </div>
  );
}
