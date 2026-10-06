/*
 * Which metadata fields a user chose to see, read back from their browser.
 *
 * Everything defaults to *visible*, and the merge is one-directional: a stored
 * preference file written before a field existed is missing that key, and the
 * missing key has to stay visible rather than becoming hidden. The opposite
 * default is what makes a release appear to lose data — the fields are still
 * there, and every existing user has them switched off.
 *
 * Only an explicit `false` hides a field. Any other value (a string, a null from
 * hand-edited JSON) leaves it visible, for the same reason.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  DEFAULT_METADATA_PREFERENCES,
  METADATA_PREFERENCE_STORAGE_KEY,
  readMetadataPreferences,
  writeMetadataPreferences,
  type MetadataPreferences,
} from "../metadata-preferences";

describe("readMetadataPreferences", () => {
  it("defaults every field to visible", () => {
    const prefs = readMetadataPreferences();
    expect(prefs).toEqual(DEFAULT_METADATA_PREFERENCES);
    expect(Object.values(prefs).every(Boolean)).toBe(true);
  });

  it("round-trips an explicit selection", () => {
    const prefs = { ...DEFAULT_METADATA_PREFERENCES, material: false };
    writeMetadataPreferences(prefs);
    expect(readMetadataPreferences().material).toBe(false);
  });

  it("merges stored partial prefs over defaults (missing keys stay visible)", () => {
    window.localStorage.setItem(METADATA_PREFERENCE_STORAGE_KEY, JSON.stringify({ infill: false }));
    const prefs = readMetadataPreferences();
    expect(prefs.infill).toBe(false);
    // A field not present in storage keeps the default (true).
    expect(prefs.material).toBe(true);
  });

  it("only false hides a field; any other value stays visible", () => {
    window.localStorage.setItem(
      METADATA_PREFERENCE_STORAGE_KEY,
      // `walls` is explicitly false; `supports` is a non-boolean truthy.
      JSON.stringify({ walls: false, supports: "yes" }),
    );
    const prefs = readMetadataPreferences();
    expect(prefs.walls).toBe(false);
    expect(prefs.supports).toBe(true);
  });

  it("falls back to defaults on malformed JSON", () => {
    window.localStorage.setItem(METADATA_PREFERENCE_STORAGE_KEY, "broken");
    expect(readMetadataPreferences()).toEqual(DEFAULT_METADATA_PREFERENCES);
  });

  it("falls back to defaults for valid JSON that is not an object", () => {
    // Hand-edited storage, or a value from an older schema. It parses, so the
    // malformed-JSON guard never sees it.
    window.localStorage.setItem(METADATA_PREFERENCE_STORAGE_KEY, "5");

    expect(readMetadataPreferences()).toEqual(DEFAULT_METADATA_PREFERENCES);
  });
});

afterEach(() => {
  vi.restoreAllMocks();

  writeMetadataPreferences(DEFAULT_METADATA_PREFERENCES);
});

describe("optional metadata preferences persistence", () => {
  it.each([{ failure: "property" }, { failure: "getItem" }])(
    "uses defaults when metadata preferences storage $failure is blocked",
    ({ failure }) => {
      const error = new DOMException("blocked storage", "SecurityError");
      const spy =
        failure === "property"
          ? vi.spyOn(globalThis, "localStorage", "get").mockImplementation(() => {
              throw error;
            })
          : vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
              throw error;
            });
      try {
        expect(readMetadataPreferences()).toEqual(DEFAULT_METADATA_PREFERENCES);
      } finally {
        spy.mockRestore();
      }
    },
  );
  it.each([{ failure: "property" }, { failure: "getItem" }])(
    "retains read metadata preferences when storage $failure is blocked",
    ({ failure }) => {
      const choice: MetadataPreferences = { ...DEFAULT_METADATA_PREFERENCES, material: false };
      localStorage.setItem(METADATA_PREFERENCE_STORAGE_KEY, JSON.stringify(choice));
      expect(readMetadataPreferences()).toEqual(choice);
      const error = new DOMException("blocked storage", "SecurityError");
      const spy =
        failure === "property"
          ? vi.spyOn(globalThis, "localStorage", "get").mockImplementation(() => {
              throw error;
            })
          : vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
              throw error;
            });
      try {
        expect(readMetadataPreferences()).toEqual(choice);
      } finally {
        spy.mockRestore();
      }
    },
  );
  it.each([{ failure: "property" }, { failure: "setItem" }])(
    "retains selected metadata preferences after storage $failure fails",
    ({ failure }) => {
      const choice: MetadataPreferences = { ...DEFAULT_METADATA_PREFERENCES, material: false };
      writeMetadataPreferences(DEFAULT_METADATA_PREFERENCES);
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
        expect(() => writeMetadataPreferences(choice)).not.toThrow();
      } finally {
        spy.mockRestore();
      }
      expect(readMetadataPreferences()).toEqual(choice);
      expect(readMetadataPreferences()).toEqual(choice);
      expect(JSON.parse(localStorage.getItem(METADATA_PREFERENCE_STORAGE_KEY)!)).toEqual(
        DEFAULT_METADATA_PREFERENCES,
      );
    },
  );
  it("snapshots unpersisted metadata preferences choices", () => {
    const choice: MetadataPreferences = { ...DEFAULT_METADATA_PREFERENCES, material: false };
    const expected = structuredClone(choice);
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage quota", "QuotaExceededError");
    });
    try {
      writeMetadataPreferences(choice);
    } finally {
      spy.mockRestore();
    }
    choice.material = true;
    const result = readMetadataPreferences();
    result.infill = false;
    expect(readMetadataPreferences()).toEqual(expected);
  });
  it("preserves metadata preferences serialization errors", () => {
    const error = new TypeError("invalid preference encoding");
    const choice: MetadataPreferences = { ...DEFAULT_METADATA_PREFERENCES, material: false };
    vi.spyOn(JSON, "stringify").mockImplementationOnce(() => {
      throw error;
    });
    expect(() => writeMetadataPreferences(choice)).toThrow(error);
    expect(readMetadataPreferences()).toEqual(DEFAULT_METADATA_PREFERENCES);
  });
  it("releases pending metadata preferences after persistence recovers", () => {
    const choice: MetadataPreferences = { ...DEFAULT_METADATA_PREFERENCES, material: false };
    const external: MetadataPreferences = { ...DEFAULT_METADATA_PREFERENCES, infill: false };
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage quota", "QuotaExceededError");
    });
    try {
      writeMetadataPreferences(choice);
    } finally {
      spy.mockRestore();
    }
    writeMetadataPreferences(DEFAULT_METADATA_PREFERENCES);
    localStorage.setItem(METADATA_PREFERENCE_STORAGE_KEY, JSON.stringify(external));
    expect(readMetadataPreferences()).toEqual(external);
  });
});
