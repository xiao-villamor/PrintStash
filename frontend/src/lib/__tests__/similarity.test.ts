/** Review copy distinguishes physical equivalence, approximate evidence and stale inputs. */
import { afterEach, describe, expect, it } from "vitest";
import { setLocale } from "@/lib/locale";
import { evidenceDescription, evidenceLabel } from "@/lib/similarity";
import { aSimilarityCandidate } from "@/test-support/similarity";

afterEach(() => setLocale("en"));

describe("Similarity review copy", () => {
  it.each([
    { locale: "en", identical: "Identical geometry", approximate: "Similar shape" },
    { locale: "es", identical: "Geometría idéntica", approximate: "Forma similar" },
  ] as const)("localizes evidence classes in $locale", ({ locale, identical, approximate }) => {
    setLocale(locale);
    expect(evidenceLabel("identical_geometry")).toBe(identical);
    expect(evidenceLabel("similar_shape")).toBe(approximate);
  });
  it("prioritizes stale-source guidance over exact evidence", () => {
    expect(evidenceDescription(aSimilarityCandidate({ freshness: "stale" }))).toMatch(
      /changed|unavailable/,
    );
  });
  it("explains ambiguous mirror history", () => {
    expect(
      evidenceDescription(aSimilarityCandidate({ summary: { mirror_ambiguous: true } })),
    ).toMatch(/symmetric|reflection/i);
  });
  it.each([25.4, 1 / 25.4])("identifies inch conversion at %s scale", (scale) => {
    expect(
      evidenceDescription(
        aSimilarityCandidate({ evidence_class: "rescaled", summary: { scale_factor: scale } }),
      ),
    ).toMatch(/inch/i);
  });
  it("shows the physical scale factor for other variants", () => {
    expect(
      evidenceDescription(
        aSimilarityCandidate({ evidence_class: "rescaled", summary: { scale_factor: 2 } }),
      ),
    ).toContain("2.000");
  });
  it("does not describe sampled evidence as exact", () => {
    expect(
      evidenceDescription(
        aSimilarityCandidate({ evidence_class: "remeshed", exact_equivalence: false }),
      ),
    ).toMatch(/sample/i);
  });
});
