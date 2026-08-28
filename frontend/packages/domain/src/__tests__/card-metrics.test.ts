/*
 * Which three numbers a user chose to see on their model cards, remembered.
 *
 * This reads back a value the user's browser has been holding for months, so
 * every stored shape is untrusted input: hand-edited JSON, a selection saved
 * before a metric was renamed, an array of the wrong length from an older
 * release. Each has to fall back to the defaults rather than throw, because this
 * runs while the vault page is rendering and an exception here is a blank
 * library.
 *
 * The validation is deliberately strict about *ids* rather than lenient: a
 * metric id that no longer exists would render an empty slot on every card,
 * which looks like missing data rather than a stale preference.
 */

import { describe, expect, it } from "vitest";

import {
  CARD_METRIC_STORAGE_KEY,
  DEFAULT_CARD_METRICS,
  readCardMetrics,
  writeCardMetrics,
  type CardMetrics,
} from "../card-metrics";

describe("readCardMetrics", () => {
  it("returns defaults when nothing is stored", () => {
    expect(readCardMetrics()).toEqual(DEFAULT_CARD_METRICS);
  });

  it("round-trips a valid selection through localStorage", () => {
    const choice: CardMetrics = ["material", "slicer", "file_count"];
    writeCardMetrics(choice);
    expect(readCardMetrics()).toEqual(choice);
  });

  it("ignores malformed JSON and returns defaults", () => {
    window.localStorage.setItem(CARD_METRIC_STORAGE_KEY, "{not json");
    expect(readCardMetrics()).toEqual(DEFAULT_CARD_METRICS);
  });

  it("rejects an array of the wrong length", () => {
    window.localStorage.setItem(CARD_METRIC_STORAGE_KEY, JSON.stringify(["material", "slicer"]));
    expect(readCardMetrics()).toEqual(DEFAULT_CARD_METRICS);
  });

  it("rejects unknown metric ids", () => {
    window.localStorage.setItem(
      CARD_METRIC_STORAGE_KEY,
      JSON.stringify(["material", "slicer", "not_a_metric"]),
    );
    expect(readCardMetrics()).toEqual(DEFAULT_CARD_METRICS);
  });
});
