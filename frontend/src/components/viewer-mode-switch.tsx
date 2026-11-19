import { Box, Layers } from "lucide-react";
import type { ToolpathAvailability } from "@/lib/toolpath-preview";
import { Button } from "@/components/ui/button";
import { uiText } from "@/lib/locale";

export type ViewerMode = "model" | "gcode";

export function ViewerModeSwitch({
  mode,
  onChange,
  hasGcode,
  availability,
  compact = false,
  className,
}: {
  mode: ViewerMode;
  onChange: (mode: ViewerMode) => void;
  hasGcode: boolean;
  availability?: ToolpathAvailability;
  compact?: boolean;
  className?: string;
}) {
  const reason =
    availability === "checking"
      ? uiText("Checking for G-code…")
      : availability === "error"
        ? uiText("Couldn’t load sliced plates")
        : uiText("No G-code file");
  return (
    <div
      className={className}
      role={compact ? "radiogroup" : "group"}
      aria-label={uiText("Preview")}
      onKeyDown={(event) => {
        if (
          compact &&
          hasGcode &&
          ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)
        ) {
          event.preventDefault();
          const next = mode === "model" ? "gcode" : "model";
          onChange(next);
          event.currentTarget
            .querySelector<HTMLButtonElement>(`[data-viewer-mode=${next}]`)
            ?.focus();
        }
      }}
    >
      {(["model", "gcode"] as const).map((option) => (
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
          disabled={option === "gcode" && !hasGcode}
          aria-description={option === "gcode" && !hasGcode ? reason : undefined}
          title={option === "gcode" && !hasGcode ? reason : undefined}
          className={
            option === "gcode" && !hasGcode
              ? "disabled:pointer-events-auto"
              : mode === option
                ? "bg-accent text-accent-foreground hover:bg-accent"
                : "hover:bg-muted hover:text-foreground"
          }
          onClick={() => onChange(option)}
        >
          {!compact &&
            (option === "model" ? (
              <Box className="h-4 w-4" aria-hidden />
            ) : (
              <Layers className="h-4 w-4" aria-hidden />
            ))}
          {option === "model" ? uiText(compact ? "3D" : "3D model") : uiText("G-code")}
        </Button>
      ))}
    </div>
  );
}
