/*
 * Exactly one G-code revision is marked recommended, even when the data says
 * otherwise.
 *
 * Older releases could leave two revisions flagged, and the invariant is now
 * enforced on write — but the rows are already in people's databases. The UI has
 * to pick the newest rather than render both, because two badges reading
 * "recommended" on one model gives a user no way to know which file to print.
 */

import { afterEach, describe, expect, it } from "vitest";
import { setLocale } from "@/lib/locale";

import { aMetadata } from "@/test-support/factories";
import { DEFAULT_METADATA_PREFERENCES } from "@/lib/metadata-preferences";
import { buildPrintSettingRows, normalizeRecommendedGcodeFiles } from "../presentation";

describe("normalizeRecommendedGcodeFiles", () => {
  it("shows only newest G-code revision as recommended when legacy data contains duplicates", () => {
    const files = normalizeRecommendedGcodeFiles([
      { id: 1, file_type: "gcode" as const, version: 1, is_recommended: true },
      { id: 2, file_type: "gcode" as const, version: 2, is_recommended: true },
      { id: 3, file_type: "gcode" as const, version: 3, is_recommended: false },
      { id: 4, file_type: "stl" as const, version: 1, is_recommended: true },
    ]);

    expect(files.map((file) => file.is_recommended)).toEqual([false, true, false, true]);
  });
});

describe("resin print settings", () => {
  afterEach(() => setLocale("en"));
  it("formats resin measurements in the selected language", () => {
    setLocale("es");
    const rows = buildPrintSettingRows(
      aMetadata({
        exposure_time_s: 2.5,
        resin_volume_ml: 12.75,
      }),
      DEFAULT_METADATA_PREFERENCES,
    );
    expect(rows).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ label: "EXPOSICIÓN", value: "2,5 s" }),
        expect.objectContaining({ label: "VOLUMEN DE RESINA", value: "12,75 ml" }),
      ]),
    );
  });

  it("shows resin measurements with units", () => {
    const rows = buildPrintSettingRows(
      aMetadata({
        layer_count: 120,
        layer_height_mm: 0.05,
        exposure_time_s: 2.5,
        resin_volume_ml: 2.5,
        estimated_time_s: 600,
      }),
      DEFAULT_METADATA_PREFERENCES,
    );
    expect(rows).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ label: "LAYERS", value: "120" }),
        expect.objectContaining({ label: "EXPOSURE", value: "2.5 s" }),
        expect.objectContaining({ label: "RESIN VOLUME", value: "2.5 ml" }),
      ]),
    );
    expect(rows.map((row) => row.label)).not.toEqual(
      expect.arrayContaining(["NOZZLE", "FILAMENT"]),
    );
  });

  it("omits absent resin measurements for FDM files", () => {
    const rows = buildPrintSettingRows(aMetadata(), DEFAULT_METADATA_PREFERENCES);
    expect(rows.map((row) => row.label)).not.toEqual(
      expect.arrayContaining(["LAYERS", "EXPOSURE", "RESIN VOLUME"]),
    );
  });
});
