import { Eye, EyeOff } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * The show/hide button inside a password field. Place it in a `relative`
 * wrapper beside an `Input` with `pr-10`, so every password field reveals its
 * value with the same control.
 */
export function PasswordToggle({
  visible,
  onToggle,
  label,
  controls,
}: {
  visible: boolean;
  onToggle: () => void;
  /** Accessible name, e.g. "Show password". */
  label: string;
  /** Id(s) of the field(s) this button reveals. */
  controls: string;
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      className="absolute inset-y-0 right-0 h-full w-10 text-muted-foreground"
      aria-label={label}
      aria-controls={controls}
      aria-pressed={visible}
      onClick={onToggle}
    >
      {visible ? (
        <EyeOff className="h-4 w-4" aria-hidden />
      ) : (
        <Eye className="h-4 w-4" aria-hidden />
      )}
    </Button>
  );
}
