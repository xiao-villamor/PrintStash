import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/lib/utils";

/** Paired with group/selection-card on the Model or folder card. */
export function LibraryCardSelection({
  selecting,
  checked,
  label,
  onChange,
  className,
}: {
  selecting: boolean;
  checked: boolean;
  label: string;
  onChange: () => void;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "absolute left-1.5 top-1.5 z-10 transition-opacity duration-fast",
        !selecting &&
          !checked &&
          "[@media(hover:hover)_and_(pointer:fine)]:opacity-0 [@media(hover:hover)_and_(pointer:fine)]:pointer-events-none group-hover/selection-card:opacity-100 group-hover/selection-card:pointer-events-auto group-focus-within/selection-card:opacity-100 group-focus-within/selection-card:pointer-events-auto",
        className,
      )}
    >
      {/* The enlarged target belongs to the control, so the card's drag cursor cannot show around it. */}
      <Checkbox
        checked={checked}
        onChange={onChange}
        ariaLabel={label}
        className="relative before:absolute before:-inset-1.5"
      />
    </div>
  );
}
