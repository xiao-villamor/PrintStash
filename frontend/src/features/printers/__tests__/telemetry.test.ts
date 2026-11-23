/** Heater history records only observed temperatures and breaks at reporting gaps. */
import { describe, expect, it } from "vitest";

import {
  activePrintState,
  emptyHeaterHistory,
  heaterSegments,
  HEATER_GAP_MS,
  HEATER_SAMPLE_LIMIT,
  HEATER_WINDOW_MS,
  observeHeaters,
} from "../telemetry";

describe("printer telemetry", () => {
  it.each(["printing", "paused", "running"])("recognizes active state %s", (state) => {
    expect(activePrintState(state)).toBe(state === "running" ? "printing" : state);
  });

  it.each(["standby", "complete", "cancelled", "error", undefined])(
    "rejects non-active state %s",
    (state) => {
      expect(activePrintState(state)).toBeNull();
    },
  );

  it("plots only the heater that reported a measurement in each frame", () => {
    const initial = observeHeaters(emptyHeaterHistory, { extruder: { temperature: 0 } }, 10, 0);
    const next = observeHeaters(initial, { heater_bed: { temperature: 25 } }, 20, 0);
    expect(next).toEqual({
      extruder: [{ at: 10, degrees: 0, connection: 0 }],
      heater_bed: [{ at: 20, degrees: 25, connection: 0 }],
    });
  });

  it("ignores frames without valid observed temperatures", () => {
    const initial = observeHeaters(emptyHeaterHistory, { extruder: { temperature: 22 } }, 10, 0);
    expect(
      observeHeaters(
        initial,
        { extruder: { target: 60 }, heater_bed: { temperature: NaN } },
        20,
        0,
      ),
    ).toBe(initial);
  });

  it("caps dense reports per heater", () => {
    let history = emptyHeaterHistory;
    for (let at = 0; at <= HEATER_SAMPLE_LIMIT; at++) {
      history = observeHeaters(history, { extruder: { temperature: 20 } }, at, 0);
    }
    expect(history.extruder).toHaveLength(HEATER_SAMPLE_LIMIT);
    expect(history.extruder[0].at).toBe(1);
  });

  it("drops observations older than the chart window", () => {
    const initial = observeHeaters(emptyHeaterHistory, { extruder: { temperature: 22 } }, 0, 0);
    const next = observeHeaters(
      initial,
      { extruder: { temperature: 24 } },
      HEATER_WINDOW_MS + 1,
      0,
    );
    expect(next.extruder).toHaveLength(1);
  });

  it("uses one sample per timestamp", () => {
    const initial = observeHeaters(emptyHeaterHistory, { extruder: { temperature: 22 } }, 10, 0);
    const next = observeHeaters(initial, { extruder: { temperature: 24 } }, 10, 0);
    expect(next.extruder).toEqual([{ at: 10, degrees: 24, connection: 0 }]);
  });

  it("breaks lines at reconnects or after a gap in observations", () => {
    const last = HEATER_GAP_MS + 30;
    const sample = (at: number, connection: number) => ({ at, connection, degrees: 21 });
    expect(heaterSegments([sample(0, 0), sample(10, 0), sample(20, 1), sample(last, 1)])).toEqual([
      [sample(0, 0), sample(10, 0)],
      [sample(20, 1)],
      [sample(last, 1)],
    ]);
  });
});
