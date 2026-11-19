import { Box, Layers } from "lucide-react";
import type { ToolpathAvailability } from "@/lib/toolpath-preview";
import { Button } from "@/components/ui/button";
import { uiText } from "@/lib/locale";

export type ViewerMode = "model" | "gcode";
export type InspectorViewerMode = ViewerMode | "images";

export function ViewerModeSwitch({
  mode,
  onChange,
  hasGcode,
  hasModel = true,
  onImages,
  availability,
  gcodeOnly = false,
  compact = false,
  className,
}: {
  mode: InspectorViewerMode;
  onChange: (mode: ViewerMode) => void;
  hasGcode: boolean;
  hasModel?: boolean;
  onImages?: () => void;
  availability?: ToolpathAvailability;
  gcodeOnly?: boolean;
  compact?: boolean;
  className?: string;
}) {
  const reason =
    availability === "checking"
      ? uiText("Checking for G-code…")
      : availability === "error"
        ? uiText("Couldn’t load sliced plates")
        : uiText("No G-code file");
  const modelReason = gcodeOnly
    ? uiText("G-code only — this file has no 3D mesh")
    : hasModel
      ? undefined
      : uiText("No 3D model");
  const options: { mode: InspectorViewerMode; reason?: string }[] = [
    { mode: "model", reason: modelReason },
    { mode: "gcode", reason: hasGcode ? undefined : reason },
  ];
  if (onImages) options.push({ mode: "images" });
  function select(option: InspectorViewerMode) {
    if (option === "images") onImages?.();
    else onChange(option);
  }
  return (
    <div
      className={className}
      role={compact ? "radiogroup" : "group"}
      aria-label={uiText("Preview")}
      onKeyDown={(event) => {
        if (compact && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
          const enabled = options.filter((option) => option.reason === undefined);
          if (enabled.length < 2) return;
          event.preventDefault();
          const index = enabled.findIndex((option) => option.mode === mode);
          const direction = event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 1;
          const next = enabled[(index + direction + enabled.length) % enabled.length]?.mode;
          if (!next) return;
          select(next);
          event.currentTarget
            .querySelector<HTMLButtonElement>(`[data-viewer-mode=${next}]`)
            ?.focus();
        }
      }}
    >
      {options.map(({ mode: option, reason: unavailable }) => (
        <Button
          key={option}
          size={compact ? "xs" : "sm"}
          variant="ghost"
          type="button"
          role={compact ? "radio" : undefined}
          data-viewer-mode={option}
          aria-checked={compact ? mode === option : undefined}
          aria-pressed={compact ? undefined : mode === option}
          tabIndex={compact && mode !== option ? -1 : undefined}
          disabled={unavailable !== undefined}
          aria-description={unavailable}
          title={unavailable}
          className={
            unavailable !== undefined
              ? "disabled:pointer-events-auto"
              : mode === option
                ? "bg-accent text-accent-foreground hover:bg-accent"
                : "hover:bg-muted hover:text-foreground"
          }
          onClick={() => select(option)}
        >
          {!compact &&
            (option === "model" ? (
              <Box className="h-4 w-4" aria-hidden />
            ) : (
              <Layers className="h-4 w-4" aria-hidden />
            ))}
          {uiText(
            option === "model"
              ? compact
                ? "3D"
                : "3D model"
              : option === "images"
                ? "Images"
                : "G-code",
          )}
        </Button>
      ))}
    </div>
  );
}
