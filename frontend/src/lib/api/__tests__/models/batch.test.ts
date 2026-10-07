/**
 * Acting on many models at once, and everything to do with the trash.
 *
 * A batch reports success or failure per Model. Edit preconditions belong to
 * the selection snapshot, so a newer writer is not overwritten by a stale batch.
 * Successful versions in the response are the preconditions for a later undo.
 *
 * Revision labels carry the one distinction a partial body cannot express.
 * Clearing a label sends an explicit `null`; leaving it alone omits the key.
 * Collapse the two and "remove this label" silently becomes "change nothing".
 *
 * The trash calls are the destructive ones, and their paths are what separates
 * recoverable from not: `DELETE /models/{id}` trashes, `DELETE /models/{id}/purge`
 * destroys, and `/trash/expired` destroys everything past its retention window.
 */
import { anEditingBase } from "@/test-support/factories";
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

import { expectRequest, fetchMock, lastBody, lastCall, respondWith } from "../_wire";

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
  it("moves with every selected version", async () => {
    respondWith({ succeeded_ids: [] });

    await batchMoveModels([1, 2], "functional", {
      1: anEditingBase({ edit_version: 3 }),
      2: anEditingBase({ edit_version: 7 }),
    });

    expectRequest("/api/v1/models/batch/move", "POST");
    expect(lastBody()).toEqual({
      model_ids: [1, 2],
      collection: "functional",
      expected_versions: {
        1: anEditingBase({ edit_version: 3 }),
        2: anEditingBase({ edit_version: 7 }),
      },
    });
    expect(new Headers(lastCall().init?.headers).get("X-PrintStash-Edit-Contract")).toBe(
      "conditional-v1",
    );
  });
});

describe("batchTagModels", () => {
  it("tags with every selected version", async () => {
    respondWith({ succeeded_ids: [] });

    await batchTagModels([1], ["new"], ["old"], { 1: anEditingBase({ edit_version: 7 }) });

    // Each row is conditional; one row may conflict while another succeeds.
    expect(lastBody()).toEqual({
      model_ids: [1],
      add: ["new"],
      remove: ["old"],
      expected_versions: { 1: anEditingBase({ edit_version: 7 }) },
    });
    expect(new Headers(lastCall().init?.headers).get("X-PrintStash-Edit-Contract")).toBe(
      "conditional-v1",
    );
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

  it("normalizes a legacy response with no storage fields", async () => {
    respondWith({ purged_model_ids: [1], purged_count: 1 });

    await expect(purgeModel(1)).resolves.toMatchObject({
      storage_cleanup_status: "completed",
      storage_completed: 0,
      storage_pending: 0,
      storage_blocked: 0,
    });
  });

  it.each([
    [{ storage_pending: 2 }, "pending"],
    [{ storage_blocked: 2 }, "blocked"],
    [{ storage_completed: 1, storage_pending: 1 }, "partial"],
    [{ storage_completed: 1, storage_blocked: 1 }, "partial"],
  ] as const)("derives the cleanup status from legacy counters (%s)", async (counts, status) => {
    respondWith({ purged_model_ids: [1], purged_count: 1, ...counts });

    await expect(purgeModel(1)).resolves.toMatchObject({ storage_cleanup_status: status });
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
