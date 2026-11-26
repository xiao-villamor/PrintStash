/** Part readiness: what a multipart part still needs, read from the Models it links. */
import { describe, expect, it } from "vitest";

import { partReadiness, printablePartCount } from "@/lib/multipart-model-presentation";
import type { MultipartModelReference, MultipartPartRead } from "@/types";

function aReference(over: Partial<MultipartModelReference> = {}): MultipartModelReference {
  return {
    id: 1,
    choice_id: 1,
    name: "Base",
    slug: "base",
    thumbnail_url: null,
    source_file_count: 1,
    gcode_revision_count: 1,
    available: true,
    ...over,
  };
}

function aPart(models: MultipartModelReference[]): MultipartPartRead {
  return { id: 1, name: "Part", sort_order: 0, quantity: 1, models };
}

describe("partReadiness", () => {
  it("is ready for one available Model with a G-code revision", () => {
    expect(partReadiness(aPart([aReference()]))).toBe("ready");
  });

  it("needs G-code when no linked Model has a revision", () => {
    expect(partReadiness(aPart([aReference({ gcode_revision_count: 0 })]))).toBe("no-gcode");
  });

  it("asks to choose a variant when several Models are linked, one printable", () => {
    expect(
      partReadiness(aPart([aReference({ id: 1 }), aReference({ id: 2, gcode_revision_count: 0 })])),
    ).toBe("choose-variant");
  });

  it("is unavailable when every linked Model is gone", () => {
    expect(partReadiness(aPart([aReference({ available: false })]))).toBe("unavailable");
  });

  it("ignores an unavailable Model's G-code revisions", () => {
    expect(
      partReadiness(
        aPart([
          aReference({ available: false, gcode_revision_count: 4 }),
          aReference({ id: 2, gcode_revision_count: 0 }),
        ]),
      ),
    ).toBe("no-gcode");
  });
});

describe("printablePartCount", () => {
  it("counts parts with a printable Model", () => {
    expect(
      printablePartCount([
        aPart([aReference()]),
        aPart([aReference({ gcode_revision_count: 0 })]),
        aPart([aReference({ available: false })]),
        aPart([aReference({ id: 2, gcode_revision_count: 0 }), aReference({ id: 3 })]),
      ]),
    ).toBe(2);
  });

  it("is zero without parts", () => {
    expect(printablePartCount([])).toBe(0);
  });
});
