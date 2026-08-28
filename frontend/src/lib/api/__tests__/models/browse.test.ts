/**
 * The read side of `api/models`: the grid, the outliner tree, the facet counts.
 *
 * The filter query is the whole contract, and its one dangerous translation is
 * invisible in TypeScript. Every multi-value facet — tags, file types, materials,
 * slicers, printer models, revision statuses, print outcomes, storage — travels as
 * **repeated keys**, because the backend reads them as lists. Comma-joining them
 * type-checks perfectly and filters for one tag literally named `a,b`, which
 * returns an empty grid the user reads as "I have no models like this".
 *
 * An empty filter set has to produce a bare path with no trailing `?`, or every
 * cache key in the app doubles: the same query arrives under two spellings and
 * each one refetches.
 *
 * Facets are pinned on the same filters as the listing for a related reason — the
 * counts have to be computed over exactly what the grid is showing, or the numbers
 * beside each filter do not match the list beneath them.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  getModelFacets,
  getVaultStats,
  listModelPage,
  listModels,
  listOutlinerModels,
} from "@/lib/api/models";
import { invalidateApiCache } from "@/lib/api/request";

import { expectRequest, fetchMock, lastCall, respondWith } from "../_wire";

type ListParams = NonNullable<Parameters<typeof listModels>[0]>;

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("listModels", () => {
  it("asks for the whole library when nothing is filtered", async () => {
    respondWith([]);

    await listModels();

    // A bare path, not a trailing "?": otherwise every cache key doubles.
    expectRequest("/api/v1/models");
  });

  it("carries the single-value filters", async () => {
    respondWith([]);

    await listModels({ collection: "functional", q: "bracket", limit: 10, offset: 20 });

    const { url } = lastCall();
    expect(url).toContain("collection=functional");
    expect(url).toContain("q=bracket");
    expect(url).toContain("limit=10");
    expect(url).toContain("offset=20");
  });

  it("repeats a key for each tag rather than joining them", async () => {
    respondWith([]);

    await listModels({ tag: ["functional", "bracket"] });

    // `tag=a,b` would filter for one tag literally named "a,b".
    expect(lastCall().url).toContain("tag=functional&tag=bracket");
  });

  it.each([
    "file_type",
    "material_type",
    "slicer_name",
    "printer_model",
    "revision_status",
    "print_outcome",
    "storage",
  ])("repeats a key for each %s", async (key) => {
    respondWith([]);

    // SAFETY: `key` comes from the literal list above, every entry of which is
    // a multi-value filter declared on `ListParams` as `string[]`.
    await listModels({ [key]: ["one", "two"] } as ListParams);

    expect(lastCall().url).toContain(`${key}=one&${key}=two`);
  });

  it("sends the boolean filters as flags the backend recognises", async () => {
    respondWith([]);

    await listModels({ direct: true, favorites: true, printed: false });

    const { url } = lastCall();
    expect(url).toContain("direct=true");
    expect(url).toContain("favorites=true");
    expect(url).toContain("printed=false");
  });

  it("carries the printer filters", async () => {
    respondWith([]);

    await listModels({ printer_id: 3, printer_presence: "none" });

    const { url } = lastCall();
    expect(url).toContain("printer_id=3");
    expect(url).toContain("printer_presence=none");
  });

  it("carries the upload date window", async () => {
    respondWith([]);

    await listModels({
      uploaded_after: "2026-01-01",
      uploaded_before: "2026-02-01",
    });

    const { url } = lastCall();
    expect(url).toContain("uploaded_after=2026-01-01");
    expect(url).toContain("uploaded_before=2026-02-01");
  });
});

describe("listModelPage", () => {
  it("asks for the first page when no cursor is held", async () => {
    respondWith({ items: [], total: 0, next_cursor: null });

    await listModelPage();

    expectRequest("/api/v1/models/page");
  });

  it("carries the sort and the cursor together", async () => {
    respondWith({ items: [], total: 0, next_cursor: null });

    await listModelPage({ sort: "name-asc", cursor: "abc" });

    // The cursor is only meaningful under the sort it was issued for, so both
    // travel on every request.
    const { url } = lastCall();
    expect(url).toContain("sort=name-asc");
    expect(url).toContain("cursor=abc");
  });
});

describe("listOutlinerModels", () => {
  it("asks the outliner endpoint", async () => {
    respondWith([]);

    await listOutlinerModels();

    expectRequest("/api/v1/models/outliner");
  });

  it("carries the filters the tree supports", async () => {
    respondWith([]);

    await listOutlinerModels({ tag: ["functional"] });

    expect(lastCall().url).toContain("tag=functional");
  });
});

describe("getModelFacets", () => {
  it("asks the facets endpoint", async () => {
    respondWith({});

    await getModelFacets();

    expectRequest("/api/v1/models/facets");
  });

  it("repeats a key for each multi-value filter", async () => {
    respondWith({});

    await getModelFacets({ tag: ["a", "b"], file_type: ["stl", "gcode"] });

    // Facet counts must be computed under the same filters as the grid, or the
    // numbers do not match what the user is looking at.
    const { url } = lastCall();
    expect(url).toContain("tag=a&tag=b");
    expect(url).toContain("file_type=stl&file_type=gcode");
  });

  it("carries the same single-value filters as the listing", async () => {
    respondWith({});

    await getModelFacets({
      collection: "functional",
      direct: true,
      q: "bracket",
      printer_id: 3,
      printer_presence: "any",
      favorites: true,
      printed: true,
      uploaded_after: "2026-01-01",
      uploaded_before: "2026-02-01",
    });

    const { url } = lastCall();
    expect(url).toContain("collection=functional");
    expect(url).toContain("printer_presence=any");
    expect(url).toContain("uploaded_before=2026-02-01");
  });
});

describe("getVaultStats", () => {
  it("reads the library summary", async () => {
    respondWith({ model_count: 0 });

    await getVaultStats();

    expectRequest("/api/v1/models/stats");
  });
});
