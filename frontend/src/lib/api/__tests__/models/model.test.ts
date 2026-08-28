/**
 * One model: reading it, editing it, starring it, and the resources hanging off it.
 *
 * These are pinned on the request rather than the response because the response is
 * the backend's business and has its own tests. What this side owns is the path and
 * the verb, and the two pairs that are easy to transpose are the ones that matter:
 * star is `PUT`/`DELETE` on the same path, and a model's own `DELETE` trashes it
 * rather than destroying it — the destructive one lives behind `/purge`.
 *
 * `PATCH` bodies carry only what changed, which is what makes two people editing
 * different fields of the same model not overwrite each other.
 *
 * The revision comparison is a single request carrying repeated `file_id` keys, so
 * the comparison table renders from one round trip rather than N.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  deleteFileRevision,
  deleteModel,
  getArtifactOutcomes,
  getModel,
  getModelPrintJobs,
  getModelPrinterFiles,
  starModel,
  unstarModel,
  updateFileRevision,
  updateModel,
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

describe("getModel", () => {
  it("reads one by id", async () => {
    respondWith({ id: 1 });

    await getModel(1);

    expectRequest("/api/v1/models/1");
  });
});

describe("updateModel", () => {
  it("PATCHes only what changed", async () => {
    respondWith({ id: 1 });

    await updateModel(1, { name: "Renamed" });

    expectRequest("/api/v1/models/1", "PATCH");
    expect(lastBody()).toEqual({ name: "Renamed" });
  });
});

describe("deleteModel", () => {
  it("trashes it rather than destroying it", async () => {
    respondWith(null, 204);

    await deleteModel(1);

    expectRequest("/api/v1/models/1", "DELETE");
  });
});

describe("starModel", () => {
  it("stars it", async () => {
    respondWith({ model_id: 1, starred: true });

    await starModel(1);

    expectRequest("/api/v1/models/1/star", "PUT");
  });
});

describe("unstarModel", () => {
  it("unstars it", async () => {
    respondWith({ model_id: 1, starred: false });

    await unstarModel(1);

    expectRequest("/api/v1/models/1/star", "DELETE");
  });
});

describe("getModelPrinterFiles", () => {
  it("lists the printers holding its revisions", async () => {
    respondWith([]);

    await getModelPrinterFiles(1);

    expectRequest("/api/v1/models/1/printer-files");
  });
});

describe("getModelPrintJobs", () => {
  it("lists its print history", async () => {
    respondWith([]);

    await getModelPrintJobs(1);

    expectRequest("/api/v1/models/1/print-jobs");
  });
});

describe("getArtifactOutcomes", () => {
  it("compares several revisions in one request", async () => {
    respondWith([]);

    await getArtifactOutcomes(1, [2, 3]);

    // One round trip for the comparison table the UI renders.
    expect(lastCall().url).toContain("file_id=2&file_id=3");
  });
});

describe("updateFileRevision", () => {
  it("PATCHes the revision under its own model", async () => {
    respondWith({ id: 4 });

    await updateFileRevision(4, 9, { revision_status: "known_good" });

    expectRequest("/api/v1/models/4/files/9/revision", "PATCH");
  });
});

describe("deleteFileRevision", () => {
  it("DELETEs the revision under its own model", async () => {
    respondWith({ id: 4 });

    await deleteFileRevision(4, 9);

    expectRequest("/api/v1/models/4/files/9/revision", "DELETE");
  });
});
