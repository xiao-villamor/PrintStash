export const CARD_METRIC_STORAGE_KEY = "printstash.card.metrics";

export const CARD_METRIC_OPTIONS = [
  { id: "layer_height",      label: "Layer height",     abbr: "LYR"   },
  { id: "print_time",        label: "Print time",       abbr: "TIME"  },
  { id: "filament_weight",   label: "Filament weight",  abbr: "WGT"   },
  { id: "material",          label: "Material",         abbr: "MAT"   },
  { id: "slicer",            label: "Slicer",           abbr: "SLR"   },
  { id: "file_count",        label: "File count",       abbr: "FILES" },
] as const;

export type CardMetricId = (typeof CARD_METRIC_OPTIONS)[number]["id"];

export type CardMetrics = [CardMetricId, CardMetricId, CardMetricId];

export const DEFAULT_CARD_METRICS: CardMetrics = ["layer_height", "print_time", "filament_weight"];

export function readCardMetrics(): CardMetrics {
  if (typeof window === "undefined") return DEFAULT_CARD_METRICS;
  const raw = window.localStorage.getItem(CARD_METRIC_STORAGE_KEY);
  if (!raw) return DEFAULT_CARD_METRICS;
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (
      Array.isArray(parsed) &&
      parsed.length === 3 &&
      parsed.every((id: unknown) => CARD_METRIC_OPTIONS.some((o) => o.id === id))
    ) {
      return parsed as CardMetrics;
    }
  } catch {}
  return DEFAULT_CARD_METRICS;
}

export function writeCardMetrics(metrics: CardMetrics): void {
  window.localStorage.setItem(CARD_METRIC_STORAGE_KEY, JSON.stringify(metrics));
}
