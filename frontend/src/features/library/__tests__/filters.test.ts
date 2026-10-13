/*
 * Library links must execute the same filters they display and save. These
 * examples exercise URL normalization and capability projection directly so
 * malformed bookmarks cannot invent constraints or leak printer-only state.
 */
import { describe, expect, it } from "vitest";
import { readLibraryFilters, writeLibraryFilters, sameLibraryFilters } from "../filters";
import { readLibraryLocation } from "../url";
import type { SavedViewFilters } from "@/types";

function read(query: string, canViewPrinters = true) {
  const params = new URLSearchParams(query);
  return readLibraryFilters(params, readLibraryLocation(params), canViewPrinters);
}

function view(overrides: Partial<SavedViewFilters> = {}): SavedViewFilters {
  return { library_view: "all", direct: true, tag: [], favorites: false, ...overrides };
}

describe("Library filter projection", () => {
  it("preserves accepted repeated filters", () => {
    const state = read(
      "tag=b&tag=a&tag=b&file_type=obj&file_type=stl&file_type=obj&material_type=PETG&material_type=PLA&slicer_name=Orca&printer_model=MK4",
    );
    expect(state.filters).toMatchObject({
      tag: ["b", "a", "b"],
      file_type: ["obj", "stl", "obj"],
      material_type: ["PETG", "PLA"],
      slicer_name: ["Orca"],
      printer_model: ["MK4"],
    });
    expect(state.baseFilters.tag).toEqual(["b", "a", "b"]);
    expect(state.structured.file_type).toEqual(["obj", "stl", "obj"]);
  });

  it("omits unknown enum values", () => {
    const state = read(
      "file_type=bad&file_type=3mf&revision_status=bad&revision_status=known_good&print_outcome=completed&print_outcome=bad&storage=bad&storage=external",
    );
    expect(state.baseFilters).toMatchObject({
      file_type: ["3mf"],
      revision_status: ["known_good"],
      print_outcome: ["completed"],
      storage: ["external"],
    });
    for (const key of ["file_type", "revision_status", "print_outcome", "storage"] as const) {
      expect(state.params.getAll(key)).toEqual(state.structured[key]);
      expect(state.filters[key]).toEqual(state.structured[key]);
      expect(state.structured[key]).not.toContain("bad");
    }
  });

  it.each(["yes", "no"])("decodes optional boolean filters: %s", (value) => {
    const state = read(`printed=${value}&has_similar_candidates=${value}`);
    expect(state.filters).toMatchObject({
      printed: value === "yes",
      has_similar_candidates: value === "yes",
    });
    expect(state.baseFilters).toMatchObject({
      printed: value === "yes",
      has_similar_candidates: value === "yes",
    });
    expect(state.structured.printed).toEqual([value]);
  });

  it("omits malformed optional booleans", () => {
    const state = read("printed=invalid&has_similar_candidates=false");
    expect(state.baseFilters.printed).toBeUndefined();
    expect(state.baseFilters.has_similar_candidates).toBeUndefined();
    expect(state.filters.printed).toBeNull();
    expect(state.filters.has_similar_candidates).toBeNull();
    expect(state.params.has("printed")).toBe(false);
    expect(state.params.has("has_similar_candidates")).toBe(false);
    expect(state.structured.printed).toEqual([]);
  });

  it.each(["1", "99", "9007199254740991"])("accepts positive safe printer identities: %s", (id) => {
    const state = read(`printer_id=${id}`);
    expect(state.baseFilters.printer_id).toBe(Number(id));
    expect(state.filters.printer_id).toBe(Number(id));
    expect(state.params.get("printer_id")).toBe(id);
  });

  it.each(["", "0", "-1", "1.5", "1e2", "NaN", "Infinity", "9007199254740992", " 1"])(
    "removes invalid printer identities: %s",
    (id) => {
      const state = read(new URLSearchParams({ printer_id: id }).toString());
      expect(state.baseFilters.printer_id).toBeUndefined();
      expect(state.filters.printer_id).toBeNull();
      expect(state.params.has("printer_id")).toBe(false);
    },
  );

  it("gives an explicit printer precedence", () => {
    const state = read("printer_id=7&printer_presence=none");
    expect(state.filters).toMatchObject({ printer_id: 7, printer_presence: null });
    expect(state.baseFilters.printer_presence).toBeUndefined();
    expect(state.params.has("printer_presence")).toBe(false);
  });

  it("retains presence after an invalid printer identity", () => {
    const state = read("printer_id=oops&printer_presence=any");
    expect(state.filters).toMatchObject({ printer_id: null, printer_presence: "any" });
    expect(state.baseFilters.printer_presence).toBe("any");
  });

  it("removes inaccessible printer constraints", () => {
    const state = read("printer_id=7&printer_presence=none&tag=functional", false);
    expect(state.filters).toMatchObject({
      printer_id: null,
      printer_presence: null,
      tag: ["functional"],
    });
    expect(state.baseFilters.printer_id).toBeUndefined();
    expect(state.baseFilters.printer_presence).toBeUndefined();
    expect(state.params.has("printer_id")).toBe(false);
    expect(state.params.has("printer_presence")).toBe(false);
  });

  it("scopes folder requests without a search", () => {
    expect(read("c=parts&q=++").filters).toMatchObject({
      collection: "parts",
      direct: true,
      q: null,
    });
  });

  it("searches recursively with a trimmed query", () => {
    expect(read("c=parts&q=+gear+").filters).toMatchObject({
      collection: "parts",
      direct: false,
      q: "gear",
    });
  });

  it("preserves history filter parsing", () => {
    const state = read(
      "printed_after=2026-01-01&printed_before=2026-02-01&print_duration_min_s=0&print_duration_max_s=2147483647&uploaded_after=2025-01-01",
    );
    expect(state.filters).toMatchObject({
      printed_after: "2026-01-01",
      printed_before: "2026-02-01",
      print_duration_min_s: 0,
      print_duration_max_s: 2147483647,
      uploaded_after: "2025-01-01",
    });
    expect(
      read("print_duration_min_s=-1&print_duration_max_s=2147483648").baseFilters,
    ).toMatchObject({ print_duration_min_s: undefined, print_duration_max_s: undefined });
  });

  it("round trips complete Library filters", () => {
    const source = view({
      library_view: "multipart",
      sort: "cost-asc",
      collection: "parts",
      direct: false,
      q: "gear",
      tag: ["functional"],
      favorites: true,
      printer_presence: "any",
      file_type: ["gcode"],
      revision_status: ["failed"],
      storage: ["external"],
      print_outcome: ["printing"],
      printed: false,
      has_similar_candidates: true,
      material_type: ["PETG"],
      slicer_name: ["Orca"],
      printer_model: ["MK4"],
      uploaded_after: "2026-01-01",
      uploaded_before: "2026-02-01",
      printed_after: "2026-01-02",
      print_duration_min_s: 0,
    });
    const params = writeLibraryFilters(source, true);
    expect(read(params.toString()).filters).toMatchObject(source);
    expect(params.get("type")).toBe("multipart");
    expect(params.has("library_view")).toBe(false);
    expect(writeLibraryFilters(view(), true).get("sort")).toBe("date-desc");
  });

  it("compares equivalent saved filters", () => {
    expect(
      sameLibraryFilters(
        view({ tag: ["b", "a"] }),
        view({ tag: ["a", "b"], sort: "date-desc", q: null, printer_id: null }),
        true,
      ),
    ).toBe(true);
    expect(sameLibraryFilters(view({ printer_id: 7 }), view(), false)).toBe(true);
  });

  it("detects an effective filter change", () => {
    expect(sameLibraryFilters(view(), view({ printed: false }), true)).toBe(false);
    expect(sameLibraryFilters(view(), view({ printer_id: 7 }), true)).toBe(false);
    expect(sameLibraryFilters(view(), view({ sort: "name-asc" }), true)).toBe(false);
  });

  it("preserves unrelated URL parameters during repair", () => {
    const state = read("v=docs&upload=1&unknown=keep&printer_id=-1&sort=name-asc&type=multipart");
    expect(state.params.toString()).toBe(
      "v=docs&upload=1&unknown=keep&sort=name-asc&type=multipart",
    );
    expect(state.filters).toMatchObject({ library_view: "multipart", sort: "name-asc" });
  });
});
