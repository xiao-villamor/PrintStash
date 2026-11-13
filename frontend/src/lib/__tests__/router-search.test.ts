/** Search serialization retains the Library wire contract instead of applying JSON coercion. */
import { describe, expect, it } from "vitest";
import { parseAppSearch, stringifyAppSearch } from "@/lib/router-search";

describe("parseAppSearch", () => {
  it("preserves repeated tags in request order", () => {
    expect(parseAppSearch("?tag=Tools&tag=Desk&c=parts")).toEqual({
      tag: ["Tools", "Desk"],
      c: "parts",
    });
  });
  it("keeps JSON-looking user values as strings", () => {
    expect(parseAppSearch("?q=123&favorites=true&tag=%5B%22x%22%5D&empty=")).toEqual({
      q: "123",
      favorites: "true",
      tag: '["x"]',
      empty: "",
    });
  });
  it("reads prototype-named keys without mutating the search prototype", () => {
    const search = parseAppSearch("?__proto__=tag&constructor=name");
    expect(Object.entries(search)).toEqual([
      ["__proto__", "tag"],
      ["constructor", "name"],
    ]);
    expect(Object.getPrototypeOf(search)).toBe(Object.prototype);
  });
});

describe("stringifyAppSearch", () => {
  it("round-trips structured feature filters as opaque strings", () => {
    const search = new URLSearchParams({
      filter: JSON.stringify({ file_type: "stl", tags: ["Desk"] }),
      return: "/?c=parts&tag=Desk",
      q: "Pieza ñ + gear",
    });
    search.append("tag", "Tools");
    search.append("tag", "Desk");
    const result = new URLSearchParams(stringifyAppSearch(parseAppSearch(search.toString())));
    expect([...result]).toEqual([...search]);
  });
  it("omits the query marker for an empty search", () => {
    expect(stringifyAppSearch({})).toBe("");
  });
});
