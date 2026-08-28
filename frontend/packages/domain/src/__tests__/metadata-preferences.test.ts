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

import { describe, expect, it } from "vitest";

import {
  DEFAULT_METADATA_PREFERENCES,
  METADATA_PREFERENCE_STORAGE_KEY,
  readMetadataPreferences,
  writeMetadataPreferences,
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
