/**
 * The taxonomy API client: collections and tags.
 *
 * Collections are a tree stored as paths, and every mutation here is a specific verb on a
 * specific resource rather than a general "save the tree": moving is a PATCH of
 * `parent_id`, renaming is a PATCH of `name`, and the two are separate calls because the
 * backend re-computes every descendant's path on a rename and must not do that work for a
 * move that only changes a parent.
 *
 * Deleting takes an explicit `recursive` flag in the query, never as a default. A
 * collection with children is a whole shelf of somebody's library, and "delete this and
 * everything under it" has to be something the caller asked for in as many words.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { invalidateApiCache } from "@/lib/api/request";
import {
  createCollection,
  createTag,
  deleteCollection,
  deleteCollectionPermission,
  deleteTag,
  getCollectionReadme,
  listCollectionPermissions,
  listCollections,
  listTags,
  moveCollection,
  renameCollection,
  setCollectionReadme,
  updateCollectionPermission,
  uploadCollectionImage,
} from "@/lib/api/taxonomy";

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

describe("collections", () => {
  it("lists the tree", async () => {
    respondWith([{ id: 1, name: "Functional", path: "functional" }]);

    expect(await listCollections()).toHaveLength(1);
    expectRequest("/api/v1/collections");
  });

  it("creates one", async () => {
    respondWith({ id: 1, name: "Functional" });

    await createCollection({ name: "Functional", parent_id: null });

    expectRequest("/api/v1/collections", "POST");
    expect(lastBody()).toMatchObject({ name: "Functional" });
  });

  it("deletes one on its own by default", async () => {
    respondWith(null, 204);

    await deleteCollection(1);

    // No `recursive` unless asked: a shelf of somebody's library is not
    // something to remove by default.
    expectRequest("/api/v1/collections/1", "DELETE");
  });

  it("deletes one with its children only when asked", async () => {
    respondWith(null, 204);

    await deleteCollection(1, true);

    expectRequest("/api/v1/collections/1?recursive=true", "DELETE");
  });

  it("moves one by changing only its parent", async () => {
    respondWith({ id: 1, name: "Functional" });

    await moveCollection(1, 4);

    expectRequest("/api/v1/collections/1", "PATCH");
    expect(lastBody()).toEqual({ parent_id: 4 });
  });

  it("moves one to the root with an explicit null parent", async () => {
    respondWith({ id: 1, name: "Functional" });

    await moveCollection(1, null);

    expect(lastBody()).toEqual({ parent_id: null });
  });

  it("renames one by changing only its name", async () => {
    respondWith({ id: 1, name: "Renamed" });

    await renameCollection(1, "Renamed");

    // A separate call from the move: a rename re-computes every descendant's
    // path and a move must not pay for that.
    expect(lastBody()).toEqual({ name: "Renamed" });
  });
});

describe("collection readme", () => {
  it("reads it fresh", async () => {
    respondWith({ readme: "# Shelf" });

    await getCollectionReadme(1);

    // A readme someone is editing must not come from cache.
    expectRequest("/api/v1/collections/1/readme");
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });

  it("PUTs an edit", async () => {
    respondWith({ readme: "# Edited" });

    await setCollectionReadme(1, "# Edited");

    expectRequest("/api/v1/collections/1/readme", "PUT");
    expect(lastBody()).toEqual({ readme: "# Edited" });
  });

  it("clears it with an explicit null", async () => {
    respondWith({ readme: null });

    await setCollectionReadme(1, null);

    expect(lastBody()).toEqual({ readme: null });
  });

  it("uploads an inline image as multipart", async () => {
    respondWith({ url: "/api/v1/collections/1/images/a.webp" });

    await uploadCollectionImage(1, new File(["png"], "a.png"));

    expectRequest("/api/v1/collections/1/images", "POST");
    expect(lastCall().init.body).toBeInstanceOf(FormData);
  });
});

describe("collection permissions", () => {
  it("lists them fresh", async () => {
    respondWith([]);

    await listCollectionPermissions(1);

    // Who can see a shelf is the answer most likely to have just changed.
    expectRequest("/api/v1/collections/1/permissions");
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });

  it("PUTs a role for one user", async () => {
    respondWith({ id: 1, role: "edit" });

    await updateCollectionPermission(1, 7, { role: "edit" });

    expectRequest("/api/v1/collections/1/permissions/7", "PUT");
    expect(lastBody()).toEqual({ role: "edit" });
  });

  it("DELETEs a role", async () => {
    respondWith(null, 204);

    await deleteCollectionPermission(1, 7);

    expectRequest("/api/v1/collections/1/permissions/7", "DELETE");
  });
});

describe("tags", () => {
  it("lists them", async () => {
    respondWith([{ id: 1, name: "functional", slug: "functional" }]);

    await listTags();

    expectRequest("/api/v1/tags");
  });

  it("creates one", async () => {
    respondWith({ id: 1, name: "functional", slug: "functional" });

    await createTag({ name: "functional" });

    expectRequest("/api/v1/tags", "POST");
  });

  it("deletes one", async () => {
    respondWith(null, 204);

    await deleteTag(1);

    expectRequest("/api/v1/tags/1", "DELETE");
  });
});
