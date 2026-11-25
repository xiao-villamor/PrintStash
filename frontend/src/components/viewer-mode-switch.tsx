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
  gcodeOnly = false,
  className,
}: {
  mode: ViewerMode;
  onChange: (mode: ViewerMode) => void;
  hasGcode: boolean;
  availability?: ToolpathAvailability;
  gcodeOnly?: boolean;
  className?: string;
}) {
  const gcodeReason = hasGcode
    ? undefined
    : availability === "checking"
      ? uiText("Checking for G-code…")
      : availability === "error"
        ? uiText("Couldn’t load sliced plates")
        : uiText("No G-code file");
  const modelReason = gcodeOnly ? uiText("G-code only — this file has no 3D mesh") : undefined;
  const options = [
    { mode: "model", label: uiText("3D model"), icon: Box, reason: modelReason },
    { mode: "gcode", label: uiText("G-code"), icon: Layers, reason: gcodeReason },
  ] as const;

  return (
    <div className={className} role="group" aria-label={uiText("Preview")}>
      {options.map(({ mode: option, label, icon: Icon, reason }) => (
        <Button
          key={option}
          size="sm"
          variant="ghost"
          type="button"
          data-viewer-mode={option}
          aria-pressed={mode === option}
          disabled={reason !== undefined}
          aria-description={reason}
          title={reason}
          className={
            reason ? "disabled:pointer-events-auto" : "hover:bg-muted hover:text-foreground"
          }
          onClick={() => onChange(option)}
        >
          <Icon className="h-4 w-4" aria-hidden />
          {label}
        </Button>
      ))}
    </div>
  );
}
