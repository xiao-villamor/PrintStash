/*
 * Every curated printer model has artwork, and custom models fall back locally.
 *
 * The covers come from OrcaSlicer's catalog, so a model in our curated list with
 * no matching image renders an empty card in the fleet view — which reads as a
 * failed load rather than as a gap in a mapping. The local fallback is what keeps
 * a user's custom printer from looking broken next to the recognised ones.
 */

import { describe, expect, it } from "vitest";
import { printerArtwork } from "@/lib/orca-printer-images";
import { PRINTER_MODEL_OPTIONS } from "@/lib/printer-providers";

describe("printerArtwork", () => {
  it("has artwork for every curated model", () => {
    const withoutArtwork = PRINTER_MODEL_OPTIONS.filter(
      (model) => printerArtwork(model).source !== "orca",
    );
    expect(withoutArtwork).toEqual([]);
  });

  it("maps a known model to its OrcaSlicer cover", () => {
    const artwork = printerArtwork("Bambu Lab X1 Carbon");
    expect(artwork.source).toBe("orca");
    expect(artwork.imageUrl).toContain(
      "OrcaSlicer/main/resources/profiles/BBL/Bambu%20Lab%20X1%20Carbon_cover.png",
    );
  });

  it("uses local artwork for custom models", () => {
    expect(printerArtwork("Homebrew CoreXY")).toEqual({
      imageUrl: "/images/printers/generic-fdm.png",
      sourceUrl: "https://github.com/SoftFever/OrcaSlicer",
      source: "fallback",
    });
  });
});
