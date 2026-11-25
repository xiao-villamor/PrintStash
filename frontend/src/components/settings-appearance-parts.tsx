import { Box, Monitor, Moon, Sun } from "lucide-react";
import { cn } from "@/lib/utils";
import { uiText } from "@/lib/locale";
import type { CardMetricId } from "@/lib/card-metrics";

type ThemeChoice = "light" | "dark" | "system";

/** Theme as three small pictures of the interface, so the choice shows what it does. */
export function ThemeTiles({
  value,
  onChange,
}: {
  value: ThemeChoice;
  onChange: (value: ThemeChoice) => void;
}) {
  const tiles = [
    { value: "light", label: uiText("Light"), Icon: Sun },
    { value: "dark", label: uiText("Dark"), Icon: Moon },
    { value: "system", label: uiText("System"), Icon: Monitor },
  ] as const;
  return (
    <div role="radiogroup" aria-label={uiText("Theme")} className="grid grid-cols-3 gap-3">
      {tiles.map(({ value: tile, label, Icon }) => {
        const selected = tile === value;
        return (
          <button
            key={tile}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => onChange(tile)}
            className="group text-left focus-visible:outline-none"
          >
            <span
              className={cn(
                "settings-theme-swatch block h-21 overflow-hidden rounded-lg border",
                selected ? "border-2 border-primary" : "border-border",
                "group-focus-visible:ring-2 group-focus-visible:ring-ring",
              )}
              data-theme={tile}
            >
              <span className="settings-theme-bar" />
              <span className="settings-theme-cells">
                <span />
                <span />
                <span />
              </span>
            </span>
            <span className="mt-2 flex items-center gap-2 text-sm font-medium">
              <span
                aria-hidden
                className={cn(
                  "flex size-4 items-center justify-center rounded-full border",
                  selected ? "border-primary" : "border-border",
                )}
              >
                {selected && <span className="size-2 rounded-full bg-primary" />}
              </span>
              <Icon className="size-3.5 text-muted-foreground" aria-hidden />
              {label}
            </span>
          </button>
        );
      })}
    </div>
  );
}

const SAMPLE_METRIC_VALUES = {
  layer_height: "0.2 mm",
  print_time: "1h 24m",
  filament_weight: "32 g",
  material: "PLA",
  slicer: "OrcaSlicer",
  file_count: "3 files",
} satisfies Record<CardMetricId, string>;

/** The model card as the three chosen stats will read on it, with sample values. */
export function CardMetricsPreview({ metrics }: { metrics: readonly CardMetricId[] }) {
  return (
    <figure className="w-60 shrink-0">
      <figcaption className="mb-2 text-sm text-muted-foreground">
        {uiText("Preview with sample values")}
      </figcaption>
      <div className="overflow-hidden rounded-lg border border-border bg-background">
        <div className="flex h-28 items-center justify-center bg-muted text-muted-foreground">
          <Box className="size-8" aria-hidden />
        </div>
        <div className="p-3">
          <p className="text-sm font-semibold">{uiText("Example bracket")}</p>
          <ul className="mt-2 flex flex-wrap gap-1.5">
            {metrics.map((metric) => (
              <li
                key={metric}
                className="rounded-md bg-foreground/10 px-1.5 py-0.5 font-mono text-2xs"
              >
                {SAMPLE_METRIC_VALUES[metric]}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </figure>
  );
}
