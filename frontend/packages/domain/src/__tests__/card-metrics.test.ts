import { describe, expect, it } from "vitest";

import {
  CARD_METRIC_STORAGE_KEY,
  DEFAULT_CARD_METRICS,
  readCardMetrics,
  writeCardMetrics,
  type CardMetrics,
} from "../card-metrics";

describe("card metric preferences", () => {
  it("defaults and round-trips a valid three-metric selection", () => {
    expect(readCardMetrics()).toEqual(DEFAULT_CARD_METRICS);
    const metrics: CardMetrics = ["material", "slicer", "file_count"];
    writeCardMetrics(metrics);
    expect(readCardMetrics()).toEqual(metrics);
  });

  it("rejects malformed, short, and unknown stored selections", () => {
    for (const raw of [
      "{not json",
      JSON.stringify(["material", "slicer"]),
      JSON.stringify(["material", "slicer", "not_a_metric"]),
    ]) {
      window.localStorage.setItem(CARD_METRIC_STORAGE_KEY, raw);
      expect(readCardMetrics()).toEqual(DEFAULT_CARD_METRICS);
    }
  });
});
