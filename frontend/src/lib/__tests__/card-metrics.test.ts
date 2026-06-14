import { describe, expect, it } from "vitest";

import {
  CARD_METRIC_STORAGE_KEY,
  DEFAULT_CARD_METRICS,
  readCardMetrics,
  writeCardMetrics,
  type CardMetrics,
} from "@/lib/card-metrics";

describe("card metrics persistence", () => {
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
    window.localStorage.setItem(
      CARD_METRIC_STORAGE_KEY,
      JSON.stringify(["material", "slicer"]),
    );
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
