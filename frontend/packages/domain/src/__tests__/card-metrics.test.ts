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

import { afterEach, describe, expect, it, vi } from "vitest";

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

afterEach(() => {
  vi.restoreAllMocks();

  writeCardMetrics(DEFAULT_CARD_METRICS);
});

describe("optional card metrics persistence", () => {
  it.each([{ failure: "property" }, { failure: "getItem" }])(
    "uses defaults when card metrics storage $failure is blocked",
    ({ failure }) => {
      const error = new DOMException("blocked storage", "SecurityError");
      const spy =
        failure === "property"
          ? vi.spyOn(window, "localStorage", "get").mockImplementation(() => {
              throw error;
            })
          : vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
              throw error;
            });
      try {
        expect(readCardMetrics()).toEqual(DEFAULT_CARD_METRICS);
      } finally {
        spy.mockRestore();
      }
    },
  );
  it.each([{ failure: "property" }, { failure: "getItem" }])(
    "retains read card metrics when storage $failure is blocked",
    ({ failure }) => {
      const choice: CardMetrics = ["material", "slicer", "file_count"];
      localStorage.setItem(CARD_METRIC_STORAGE_KEY, JSON.stringify(choice));
      expect(readCardMetrics()).toEqual(choice);
      const error = new DOMException("blocked storage", "SecurityError");
      const spy =
        failure === "property"
          ? vi.spyOn(window, "localStorage", "get").mockImplementation(() => {
              throw error;
            })
          : vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
              throw error;
            });
      try {
        expect(readCardMetrics()).toEqual(choice);
      } finally {
        spy.mockRestore();
      }
    },
  );
  it.each([{ failure: "property" }, { failure: "setItem" }])(
    "retains selected card metrics after storage $failure fails",
    ({ failure }) => {
      const choice: CardMetrics = ["material", "slicer", "file_count"];
      writeCardMetrics(DEFAULT_CARD_METRICS);
      const error = new DOMException("storage quota", "QuotaExceededError");
      const spy =
        failure === "property"
          ? vi.spyOn(window, "localStorage", "get").mockImplementation(() => {
              throw error;
            })
          : vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
              throw error;
            });
      try {
        expect(() => writeCardMetrics(choice)).not.toThrow();
      } finally {
        spy.mockRestore();
      }
      expect(readCardMetrics()).toEqual(choice);
      expect(readCardMetrics()).toEqual(choice);
      expect(JSON.parse(localStorage.getItem(CARD_METRIC_STORAGE_KEY)!)).toEqual(
        DEFAULT_CARD_METRICS,
      );
    },
  );
  it("snapshots unpersisted card metrics choices", () => {
    const choice: CardMetrics = ["material", "slicer", "file_count"];
    const expected = structuredClone(choice);
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage quota", "QuotaExceededError");
    });
    try {
      writeCardMetrics(choice);
    } finally {
      spy.mockRestore();
    }
    choice[0] = "print_time";
    const result = readCardMetrics();
    result[1] = "layer_height";
    expect(readCardMetrics()).toEqual(expected);
  });
  it("preserves card metrics serialization errors", () => {
    const error = new TypeError("invalid preference encoding");
    const choice: CardMetrics = ["material", "slicer", "file_count"];
    vi.spyOn(JSON, "stringify").mockImplementationOnce(() => {
      throw error;
    });
    expect(() => writeCardMetrics(choice)).toThrow(error);
    expect(readCardMetrics()).toEqual(DEFAULT_CARD_METRICS);
  });
  it("releases pending card metrics after persistence recovers", () => {
    const choice: CardMetrics = ["material", "slicer", "file_count"];
    const external: CardMetrics = ["file_count", "material", "slicer"];
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage quota", "QuotaExceededError");
    });
    try {
      writeCardMetrics(choice);
    } finally {
      spy.mockRestore();
    }
    writeCardMetrics(DEFAULT_CARD_METRICS);
    localStorage.setItem(CARD_METRIC_STORAGE_KEY, JSON.stringify(external));
    expect(readCardMetrics()).toEqual(external);
  });
});
