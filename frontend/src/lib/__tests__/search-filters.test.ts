/** Canonical URLs preserve normalized saved filters without model output or parse intent. */
import { describe, expect, it } from "vitest";
import { historyFilters, readSearchFilters, writeSearchFilters } from "@/lib/search-filters";

describe("Search filter URLs", () => {
  it("roundtrips actual history including a zero lower bound", () => {
    const filters = {
      direct: false,
      favorites: false,
      tag: ["fixtures"],
      printed: true,
      printed_after: "2026-08-01T00:00:00Z",
      printed_before: "2026-09-01T00:00:00Z",
      print_duration_min_s: 0,
      print_duration_max_s: 10800,
    };
    const params = writeSearchFilters(filters, "bracket", "printed-desc");
    expect(readSearchFilters(params)).toMatchObject(filters);
    expect(params.get("q")).toBe("bracket");
    expect(params.get("sort")).toBe("printed-desc");
    expect(params.has("parse")).toBe(false);
  });
  it("drops invalid numeric browser input", () => {
    expect(
      historyFilters(new URLSearchParams("print_duration_min_s=-1&print_duration_max_s=invalid")),
    ).toEqual({
      printed_after: undefined,
      printed_before: undefined,
      print_duration_min_s: undefined,
      print_duration_max_s: undefined,
    });
  });
});
