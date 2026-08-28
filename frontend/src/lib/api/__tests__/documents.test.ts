/**
 * Notes and manuals that live beside the models they belong to.
 *
 * Documents are the one part of the library edited in place rather than uploaded,
 * which is why the single read is fetched `fresh`: a document somebody is editing
 * must not come from cache, or the editor opens on a version that has already been
 * superseded and saving it silently reverts someone else's work.
 *
 * The collection filter carries a user-typed path, so it has to survive URL
 * encoding — a `/` in a collection name that reaches the query raw truncates the
 * filter and returns the wrong documents.
 *
 * Binary documents and inline images go up as multipart, where a JSON-serialised
 * object type-checks and arrives unparseable. Their form fields are asserted
 * directly, including the case where the document belongs to no collection: an
 * empty `collection_id` and an absent one are different requests.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createDocument,
  deleteDocument,
  getDocument,
  listDocuments,
  updateDocument,
  uploadDocument,
  uploadDocumentImage,
} from "@/lib/api/documents";
import { invalidateApiCache } from "@/lib/api/request";

import { expectRequest, fetchMock, lastBody, lastCall, lastForm, respondWith } from "./_wire";

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("listDocuments", () => {
  it("lists every document when no collection is named", async () => {
    respondWith([]);

    await listDocuments(null);

    expectRequest("/api/v1/documents");
  });

  it("filters by collection when one is named", async () => {
    respondWith([]);

    await listDocuments("functional/brackets");

    // The path is a user-typed string, so it has to survive URL encoding.
    expectRequest("/api/v1/documents?collection=functional%2Fbrackets");
  });
});

describe("getDocument", () => {
  it("reads one fresh", async () => {
    respondWith({ id: 1, name: "Manual" });

    await getDocument(1);

    // A document someone is editing must not come from cache.
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });
});

describe("createDocument", () => {
  it("POSTs a markdown document", async () => {
    respondWith({ id: 1, name: "Manual" });

    await createDocument({ name: "Manual", collection_id: null, body: "# Hi" });

    expectRequest("/api/v1/documents", "POST");
    expect(lastBody()).toMatchObject({ name: "Manual", body: "# Hi" });
  });
});

describe("uploadDocument", () => {
  it("POSTs a binary document as multipart", async () => {
    respondWith({ id: 1, name: "Manual" });

    await uploadDocument(new File(["pdf"], "manual.pdf"), 3, "Manual");

    expectRequest("/api/v1/documents/upload", "POST");
    expect(() => lastForm()).not.toThrow();
  });

  it("carries the collection and name alongside the uploaded file", async () => {
    respondWith({ id: 1, name: "Manual" });

    await uploadDocument(new File(["pdf"], "manual.pdf"), 3, "Manual");

    const form = lastForm();
    expect(form.get("collection_id")).toBe("3");
    expect(form.get("name")).toBe("Manual");
  });

  it("leaves the collection out when the document belongs nowhere", async () => {
    respondWith({ id: 1, name: "Manual" });

    await uploadDocument(new File(["pdf"], "manual.pdf"), null);

    expect(lastForm().get("collection_id")).toBeNull();
  });
});

describe("updateDocument", () => {
  it("PUTs an edit", async () => {
    respondWith({ id: 1, name: "Manual" });

    await updateDocument(1, { body: "# Edited" });

    expectRequest("/api/v1/documents/1", "PUT");
  });
});

describe("deleteDocument", () => {
  it("deletes one by id", async () => {
    respondWith(null, 204);

    await deleteDocument(1);

    expectRequest("/api/v1/documents/1", "DELETE");
  });
});

describe("uploadDocumentImage", () => {
  it("POSTs to the document's own image sub-resource", async () => {
    respondWith({ url: "/api/v1/documents/1/images/a.webp" });

    await uploadDocumentImage(1, new File(["png"], "a.png"));

    expectRequest("/api/v1/documents/1/images", "POST");
  });
});
