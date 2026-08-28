/**
 * Acting on many models at once, and everything to do with the trash.
 *
 * A batch call is one request on purpose: the server applies the whole change or
 * none of it, so tagging forty models cannot leave half of them tagged when the
 * connection drops. That is why the bodies here are pinned whole — a batch that
 * degrades into N requests looks identical from the outside until it fails.
 *
 * Revision labels carry the one distinction a partial body cannot express.
 * Clearing a label sends an explicit `null`; leaving it alone omits the key.
 * Collapse the two and "remove this label" silently becomes "change nothing".
 *
 * The trash calls are the destructive ones, and their paths are what separates
 * recoverable from not: `DELETE /models/{id}` trashes, `DELETE /models/{id}/purge`
 * destroys, and `/trash/expired` destroys everything past its retention window.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  batchDeleteModels,
  batchMoveModels,
  batchSetRevisionLabels,
  batchTagModels,
  listTrash,
  purgeExpiredTrash,
  purgeModel,
  restoreModel,
} from "@/lib/api/models";
import { invalidateApiCache } from "@/lib/api/request";

import { expectRequest, fetchMock, lastBody, respondWith } from "../_wire";

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("batchMoveModels", () => {
  it("moves several models into one collection", async () => {
    respondWith({ succeeded_ids: [] });

    await batchMoveModels([1, 2], "functional");

    expectRequest("/api/v1/models/batch/move", "POST");
    expect(lastBody()).toEqual({ model_ids: [1, 2], collection: "functional" });
  });
});

describe("batchTagModels", () => {
  it("adds and removes tags in one request", async () => {
    respondWith({ succeeded_ids: [] });

    await batchTagModels([1], ["new"], ["old"]);

    // One request, so the whole change is atomic on the server.
    expect(lastBody()).toEqual({ model_ids: [1], add: ["new"], remove: ["old"] });
  });
});

describe("batchSetRevisionLabels", () => {
  it("PATCHes a label onto several revisions", async () => {
    respondWith({ succeeded_ids: [] });

    await batchSetRevisionLabels([4], "PETG fast");

    expectRequest("/api/v1/models/batch/revision-labels", "PATCH");
    expect(lastBody()).toEqual({ file_ids: [4], revision_label: "PETG fast" });
  });

  it("clears revision labels with an explicit null", async () => {
    respondWith({ succeeded_ids: [] });

    await batchSetRevisionLabels([4], null);

    // `null` rather than an omitted key: "clear it" and "leave it" are
    // different requests.
    expect(lastBody()).toEqual({ file_ids: [4], revision_label: null });
  });
});

describe("batchDeleteModels", () => {
  it("trashes several models", async () => {
    respondWith({ succeeded_ids: [] });

    await batchDeleteModels([1, 2]);

    expectRequest("/api/v1/models/batch/delete", "POST");
  });
});

describe("listTrash", () => {
  it("lists what is in it", async () => {
    respondWith([]);

    await listTrash();

    expectRequest("/api/v1/models/trash");
  });
});

describe("restoreModel", () => {
  it("brings one back out", async () => {
    respondWith({ id: 1 });

    await restoreModel(1);

    expectRequest("/api/v1/models/1/restore", "POST");
  });
});

describe("purgeModel", () => {
  it("destroys one for good", async () => {
    respondWith({ purged_model_ids: [1], purged_count: 1 });

    await purgeModel(1);

    expectRequest("/api/v1/models/1/purge", "DELETE");
  });

  it("adds the one-shot storage-risk confirmation when requested", async () => {
    respondWith({ purged_model_ids: [1], purged_count: 1 });

    await purgeModel(1, true);

    expectRequest("/api/v1/models/1/purge?confirm_storage_risk=true", "DELETE");
  });
});

describe("purgeExpiredTrash", () => {
  it("destroys everything past its retention", async () => {
    respondWith({ purged_model_ids: [], purged_count: 0 });

    await purgeExpiredTrash();

    expectRequest("/api/v1/models/trash/expired", "DELETE");
  });

  it("adds the one-shot storage-risk confirmation when requested", async () => {
    respondWith({ purged_model_ids: [], purged_count: 0 });

    await purgeExpiredTrash(true);

    expectRequest("/api/v1/models/trash/expired?confirm_storage_risk=true", "DELETE");
  });
});
