import { describe, expect, it } from "vitest";
import { printerArtwork } from "@/lib/orca-printer-images";
import { PRINTER_MODEL_OPTIONS } from "@/lib/printer-providers";

describe("printerArtwork", () => {
  it("has artwork for every curated model", () => {
    for (const model of PRINTER_MODEL_OPTIONS) {
      expect(printerArtwork(model).source, model).toBe("orca");
    }
  });

  it("maps a known model to its OrcaSlicer cover", () => {
    const artwork = printerArtwork("Bambu Lab X1 Carbon");
    expect(artwork.source).toBe("orca");
    expect(artwork.imageUrl).toContain("OrcaSlicer/main/resources/profiles/BBL/Bambu%20Lab%20X1%20Carbon_cover.png");
  });

  it("uses local artwork for custom models", () => {
    expect(printerArtwork("Homebrew CoreXY")).toEqual({
      imageUrl: "/images/printers/generic-fdm.png",
      sourceUrl: "https://github.com/SoftFever/OrcaSlicer",
      source: "fallback",
    });
  });
});
