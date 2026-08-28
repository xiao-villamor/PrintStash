/**
 * Saved views: a named bundle of vault filters the user can come back to.
 *
 * The filter object is stored verbatim and replayed into the model list later, so
 * the create call is pinned on the *whole* body rather than on a field or two. A
 * filter silently dropped on the way out produces a saved view that quietly
 * returns the wrong models — and it looks like the user saved it wrong.
 *
 * The list is read fresh for the same reason the task list is: a view created in
 * another tab, or on another device, has to appear here without a reload.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { invalidateApiCache } from "@/lib/api/request";
import {
  createSavedView,
  deleteSavedView,
  listSavedViews,
  updateSavedView,
} from "@/lib/api/saved-views";

import { expectRequest, fetchMock, lastBody, lastCall, respondWith } from "./_wire";

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("listSavedViews", () => {
  it("reads them fresh", async () => {
    respondWith([]);

    await listSavedViews();

    // A saved view added in another tab should show up here.
    expectRequest("/api/v1/saved-views");
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });
});

describe("createSavedView", () => {
  it("sends the name and the current filters", async () => {
    respondWith({ id: 1, name: "PETG" });

    await createSavedView("PETG", {
      direct: false,
      tag: [],
      favorites: false,
      material_type: ["PETG"],
    });

    expectRequest("/api/v1/saved-views", "POST");
    expect(lastBody()).toEqual({
      name: "PETG",
      filters: { direct: false, tag: [], favorites: false, material_type: ["PETG"] },
    });
  });
});

describe("updateSavedView", () => {
  it("PATCHes only what changed", async () => {
    respondWith({ id: 1, name: "Renamed" });

    await updateSavedView(1, { name: "Renamed" });

    expectRequest("/api/v1/saved-views/1", "PATCH");
  });
});

describe("deleteSavedView", () => {
  it("deletes one by id", async () => {
    respondWith(null, 204);

    await deleteSavedView(1);

    expectRequest("/api/v1/saved-views/1", "DELETE");
  });
});
