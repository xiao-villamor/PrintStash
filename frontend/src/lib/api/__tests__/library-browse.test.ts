/** The mixed browse client sends the server-owned scope without sorting its response. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getLibraryRevision, listLibraryPage } from "@/lib/api/library-browse";
import { fetchMock, lastCall, respondWith } from "./_wire";

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

describe("listLibraryPage", () => {
  it("sends the browse scope to the server", async () => {
    const page = {
      items: [],
      total: 0,
      next_cursor: null,
      browse_revision: "42",
      authorization_revision: "7",
    };
    respondWith(page);

    const result = await listLibraryPage({
      view: "multipart",
      sort: "name-asc",
      limit: 24,
      collection: "parts",
      tag: ["red", "blue"],
      cursor: "opaque+/=",
    });

    expect(result).toEqual(page);
    const url = new URL(lastCall().url, "http://printstash.test");
    expect(url.pathname).toBe("/api/v1/models/browse");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      view: "multipart",
      sort: "name-asc",
      limit: "24",
      collection: "parts",
      tag: "blue",
      cursor: "opaque+/=",
    });
    expect(url.searchParams.getAll("tag")).toEqual(["red", "blue"]);
  });
});

describe("getLibraryRevision", () => {
  it("reads the lightweight authority revision", async () => {
    respondWith({ browse_revision: "42", authorization_revision: "7" });

    const result = await getLibraryRevision();

    expect(lastCall().url).toBe("/api/v1/models/browse/revision");
    expect(result).toEqual({ browse_revision: "42", authorization_revision: "7" });
  });
});
