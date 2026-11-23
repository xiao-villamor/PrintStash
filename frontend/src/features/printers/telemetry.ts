import type { PrinterSnapshot } from "@/types";

export const HEATER_WINDOW_MS = 5 * 60_000;
export const HEATER_SAMPLE_LIMIT = 1200;
export const HEATER_GAP_MS = 15_000;

export interface HeaterSample {
  at: number;
  degrees: number;
  connection: number;
}

type HeaterObservation = HeaterSample | (Omit<HeaterSample, "degrees"> & { degrees: null });

export interface HeaterHistory {
  extruder: readonly HeaterObservation[];
  heater_bed: readonly HeaterObservation[];
}

export const emptyHeaterHistory: HeaterHistory = { extruder: [], heater_bed: [] };

export function finiteReading(value: number | null | undefined): number | null {
  return value != null && Number.isFinite(value) ? value : null;
}

export function activePrintState(state: string | undefined): "printing" | "paused" | null {
  return state === "running"
    ? "printing"
    : state === "printing" || state === "paused"
      ? state
      : null;
}

export function observeHeaters(
  history: HeaterHistory,
  patch: PrinterSnapshot,
  at: number,
  connection: number,
  fullSnapshot = false,
): HeaterHistory {
  function observe(samples: readonly HeaterObservation[], value: number | null | undefined) {
    if (value === undefined && !fullSnapshot) return samples;
    const degrees = finiteReading(value);
    if (degrees === null && (samples.length === 0 || samples.at(-1)?.degrees === null)) {
      return samples;
    }
    return [
      ...samples.filter(
        (sample) =>
          sample.at >= at - HEATER_WINDOW_MS && (sample.at !== at || sample.degrees === null),
      ),
      { at, degrees, connection },
    ].slice(-HEATER_SAMPLE_LIMIT);
  }
  const extruder = observe(history.extruder, patch.extruder?.temperature);
  const heater_bed = observe(history.heater_bed, patch.heater_bed?.temperature);
  if (extruder === history.extruder && heater_bed === history.heater_bed) return history;
  return { extruder, heater_bed };
}

export function heaterSegments(samples: readonly HeaterObservation[]): HeaterSample[][] {
  const segments: HeaterSample[][] = [];
  let previous: HeaterSample | undefined;
  for (const sample of samples) {
    if (sample.degrees === null) {
      previous = undefined;
      continue;
    }
    const segment = segments.at(-1);
    if (
      !segment ||
      !previous ||
      sample.connection !== previous.connection ||
      sample.at - previous.at > HEATER_GAP_MS
    ) {
      segments.push([sample]);
    } else {
      segment.push(sample);
    }
    previous = sample;
  }
  return segments;
}
