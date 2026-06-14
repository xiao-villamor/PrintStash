import { describe, expect, it } from "vitest";

import {
  DEFAULT_METADATA_PREFERENCES,
  METADATA_PREFERENCE_STORAGE_KEY,
  readMetadataPreferences,
  writeMetadataPreferences,
} from "@/lib/metadata-preferences";

describe("metadata preferences persistence", () => {
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
    window.localStorage.setItem(
      METADATA_PREFERENCE_STORAGE_KEY,
      JSON.stringify({ infill: false }),
    );
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
});
