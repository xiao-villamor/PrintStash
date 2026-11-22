/** The 3D view shades a surface exactly as the generated card thumbnail does. */
import { describe, expect, it } from "vitest";
import { studioShade } from "@/lib/studio-matcap";

// Reference values from printstash-core's `make_preview_shade` (the thumbnail worker).
const NORMALS = [
  [0, 0, 1],
  [0, 0.866, 0.5],
  [-0.7071, 0, 0.7071],
  [0.6, -0.48, 0.64],
] as const;
const THUMBNAIL = {
  slate: [
    [0.5175, 0.549725, 0.604047],
    [0.641781, 0.679893, 0.744138],
    [0.571221, 0.606456, 0.665854],
    [0.299634, 0.317642, 0.347996],
  ],
  sand: [
    [0.69652, 0.639017, 0.571367],
    [0.818101, 0.751102, 0.67228],
    [0.755438, 0.693149, 0.619867],
    [0.417932, 0.383584, 0.343174],
  ],
} as const;

describe("studioShade", () => {
  it.each(["slate", "sand"] as const)("matches the thumbnail worker for the %s look", (look) => {
    NORMALS.forEach((normal, index) => {
      const shade = studioShade(look, normal);
      shade.forEach((channel, component) =>
        expect(channel).toBeCloseTo(THUMBNAIL[look][index]![component]!, 5),
      );
    });
  });
});
