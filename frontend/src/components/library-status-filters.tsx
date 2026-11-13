import { Button } from "@/components/ui/button";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import type { FacetValueRead } from "@/types";

type Status = "known_good" | "needs_test" | "failed";
export function LibraryStatusFilters({
  value,
  counts,
  total,
  onChange,
  onIntent,
}: {
  value: string[];
  counts: FacetValueRead[] | undefined;
  total: number;
  onChange: (value: Status[]) => void;
  onIntent: () => void;
}) {
  useUiLocale();
  const options: { key: Status | "all"; label: string; count: number | undefined; dot?: string }[] =
    [
      { key: "all", label: uiText("All"), count: total },
      {
        key: "known_good",
        label: uiText("Ready"),
        count: counts?.find((item) => item.value === "known_good")?.count,
        dot: "bg-muted-foreground",
      },
      {
        key: "needs_test",
        label: uiText("Needs test"),
        count: counts?.find((item) => item.value === "needs_test")?.count,
        dot: "library-status-test",
      },
      {
        key: "failed",
        label: uiText("Failed"),
        count: counts?.find((item) => item.value === "failed")?.count,
        dot: "bg-destructive",
      },
    ];
  const shown = options.filter(
    (item) =>
      item.key === "all" ||
      value.includes(item.key) ||
      (item.key === "failed" ? (item.count ?? 0) > 0 : item.count !== 0),
  );
  const active = value.length === 0 ? "all" : value.length === 1 ? value[0] : null;
  return (
    <div
      className="library-status-filters order-1 flex w-full min-w-0 items-center overflow-x-auto @3xl/library:w-auto"
      role="radiogroup"
      aria-label={uiText("Filter models by status")}
      onFocusCapture={onIntent}
      onPointerDownCapture={onIntent}
      onKeyDown={(event) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        const buttons = Array.from(
          event.currentTarget.querySelectorAll<HTMLButtonElement>("button:not(:disabled)"),
        );
        const current = buttons.findIndex((button) => button === document.activeElement);
        const next =
          event.key === "Home"
            ? 0
            : event.key === "End"
              ? buttons.length - 1
              : (current + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) % buttons.length;
        event.preventDefault();
        buttons[next]?.focus();
        buttons[next]?.click();
      }}
    >
      {shown.map((item, index) => (
        <Button
          key={item.key}
          type="button"
          variant="ghost"
          size="xs"
          role="radio"
          aria-checked={active === item.key}
          tabIndex={active === item.key || (active === null && index === 0) ? 0 : -1}
          onClick={() => onChange(item.key === "all" ? [] : [item.key])}
        >
          {item.dot && <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${item.dot}`} />}
          {item.label}{" "}
          <span className="font-mono text-3xs text-muted-foreground">{item.count}</span>
        </Button>
      ))}
    </div>
  );
}
