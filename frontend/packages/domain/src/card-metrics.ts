export const CARD_METRIC_STORAGE_KEY = "printstash.card.metrics";

export const CARD_METRIC_OPTIONS = [
  { id: "layer_height", label: "Layer height", abbr: "LYR" },
  { id: "print_time", label: "Print time", abbr: "TIME" },
  { id: "filament_weight", label: "Filament weight", abbr: "WGT" },
  { id: "material", label: "Material", abbr: "MAT" },
  { id: "slicer", label: "Slicer", abbr: "SLR" },
  { id: "file_count", label: "File count", abbr: "FILES" },
] as const;

export type CardMetricId = (typeof CARD_METRIC_OPTIONS)[number]["id"];
export type CardMetrics = [CardMetricId, CardMetricId, CardMetricId];

export const DEFAULT_CARD_METRICS: CardMetrics = ["layer_height", "print_time", "filament_weight"];

/**
 * Match one decoded JSON entry against the known metric options. The stored
 * value is user-editable browser storage, so an entry only becomes a
 * `CardMetricId` by equalling one of the ids we ship.
 */
function toCardMetricId(entries: readonly unknown[], index: number): CardMetricId | null {
  const entry = entries[index];
  return CARD_METRIC_OPTIONS.find((option) => option.id === entry)?.id ?? null;
}

/** Decode a stored selection; returns null for anything that isn't three known ids. */
function parseCardMetrics(raw: string): CardMetrics | null {
  let decoded: unknown;
  try {
    decoded = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!Array.isArray(decoded) || decoded.length !== 3) return null;
  const entries: readonly unknown[] = decoded;
  const first = toCardMetricId(entries, 0);
  const second = toCardMetricId(entries, 1);
  const third = toCardMetricId(entries, 2);
  if (first === null || second === null || third === null) return null;
  return [first, second, third];
}

export function readCardMetrics(): CardMetrics {
  if (!("window" in globalThis)) return DEFAULT_CARD_METRICS;
  const raw = window.localStorage.getItem(CARD_METRIC_STORAGE_KEY);
  if (!raw) return DEFAULT_CARD_METRICS;
  return parseCardMetrics(raw) ?? DEFAULT_CARD_METRICS;
}

export function writeCardMetrics(metrics: CardMetrics): void {
  window.localStorage.setItem(CARD_METRIC_STORAGE_KEY, JSON.stringify(metrics));
}
