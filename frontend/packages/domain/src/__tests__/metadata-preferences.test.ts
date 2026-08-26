import { describe, expect, it } from "vitest";

import {
  DEFAULT_METADATA_PREFERENCES,
  METADATA_PREFERENCE_STORAGE_KEY,
  readMetadataPreferences,
  writeMetadataPreferences,
} from "../metadata-preferences";

describe("metadata preferences", () => {
  it("defaults every field to visible and round-trips false values", () => {
    expect(Object.values(readMetadataPreferences()).every(Boolean)).toBe(true);
    const preferences = { ...DEFAULT_METADATA_PREFERENCES, material: false };
    writeMetadataPreferences(preferences);
    expect(readMetadataPreferences().material).toBe(false);
  });

  it("merges partial data and treats only literal false as hidden", () => {
    window.localStorage.setItem(
      METADATA_PREFERENCE_STORAGE_KEY,
      JSON.stringify({ walls: false, supports: "yes" }),
    );
    const preferences = readMetadataPreferences();
    expect(preferences.walls).toBe(false);
    expect(preferences.supports).toBe(true);
    expect(preferences.material).toBe(true);
  });

  it("falls back to defaults for malformed JSON", () => {
    window.localStorage.setItem(METADATA_PREFERENCE_STORAGE_KEY, "broken");
    expect(readMetadataPreferences()).toEqual(DEFAULT_METADATA_PREFERENCES);
  });
});
