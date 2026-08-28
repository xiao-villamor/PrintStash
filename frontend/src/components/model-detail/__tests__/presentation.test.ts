/*
 * Exactly one G-code revision is marked recommended, even when the data says
 * otherwise.
 *
 * Older releases could leave two revisions flagged, and the invariant is now
 * enforced on write — but the rows are already in people's databases. The UI has
 * to pick the newest rather than render both, because two badges reading
 * "recommended" on one model gives a user no way to know which file to print.
 */

import { describe, expect, it } from "vitest";

import { normalizeRecommendedGcodeFiles } from "../presentation";

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
