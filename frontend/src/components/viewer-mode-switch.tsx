import { Button } from "@/components/ui/button";
import { uiText } from "@/lib/locale";

export type ViewerMode = "model" | "gcode";

export function ViewerModeSwitch({
  mode,
  onChange,
  hasGcode,
  compact = false,
}: {
  mode: ViewerMode;
  onChange: (mode: ViewerMode) => void;
  hasGcode: boolean;
  compact?: boolean;
}) {
  return (
    <div
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
          aria-description={option === "gcode" && !hasGcode ? uiText("No G-code file") : undefined}
          title={option === "gcode" && !hasGcode ? uiText("No G-code file") : undefined}
          className={
            option === "gcode" && !hasGcode
              ? "disabled:pointer-events-auto"
              : mode === option
                ? "bg-accent text-accent-foreground"
                : undefined
          }
          onClick={() => onChange(option)}
        >
          {option === "model"
            ? uiText(compact ? "3D" : "3D model")
            : uiText(compact ? "G-code" : "Layers")}
        </Button>
      ))}
    </div>
  );
}
