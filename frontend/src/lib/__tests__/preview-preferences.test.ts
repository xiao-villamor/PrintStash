/*
 * Preview quality and screenshot scale, read back from the user's browser.
 *
 * The defaults are deliberate — balanced previews and 2x screenshots — because
 * both extremes have a cost a user would notice: the highest quality makes the
 * vault sluggish on a laptop, and the lowest makes the thumbnails useless.
 *
 * An unsupported value is replaced rather than passed through. These feed a
 * renderer, and an out-of-range quality is not a slightly-wrong preview; it is a
 * blank one.
 */

import { describe, expect, it } from "vitest";

import {
  DEFAULT_PREVIEW_PREFERENCES,
  PREVIEW_PREFERENCES_STORAGE_KEY,
  previewPixelRatio,
  readPreviewPreferences,
  writePreviewPreferences,
} from "@/lib/preview-preferences";

describe("readPreviewPreferences", () => {
  it("uses balanced previews and 2x screenshots by default", () => {
    expect(readPreviewPreferences()).toEqual(DEFAULT_PREVIEW_PREFERENCES);
    expect(previewPixelRatio("balanced")).toBe(1.5);
  });

  it("round-trips supported quality settings", () => {
    writePreviewPreferences({ previewQuality: "detail", screenshotScale: 3 });
    expect(readPreviewPreferences()).toEqual({
      previewQuality: "detail",
      screenshotScale: 3,
    });
  });

  it("replaces malformed or unsupported values with defaults", () => {
    localStorage.setItem(
      PREVIEW_PREFERENCES_STORAGE_KEY,
      JSON.stringify({ previewQuality: "ultra", screenshotScale: 8 }),
    );
    expect(readPreviewPreferences()).toEqual(DEFAULT_PREVIEW_PREFERENCES);

    localStorage.setItem(PREVIEW_PREFERENCES_STORAGE_KEY, "broken");
    expect(readPreviewPreferences()).toEqual(DEFAULT_PREVIEW_PREFERENCES);
  });
});
