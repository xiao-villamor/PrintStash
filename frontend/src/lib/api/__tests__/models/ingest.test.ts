/**
 * Getting things *into* the library: uploads, URL imports, and the two-step
 * selections that sit between them.
 *
 * Two shapes here have a failure mode a type checker cannot see.
 *
 * A multipart upload must send a real `FormData`. A JSON-serialised object
 * type-checks perfectly and arrives at the server as an unparseable body, so every
 * upload path is asserted on the body's *type*, not just on the URL.
 *
 * A two-step import must present its token back on exactly the endpoint that
 * issued it, because the token *is* the staged work. A selection posted to the
 * wrong token endpoint imports somebody else's archive, or nothing at all — and
 * the staged upload is lost either way.
 *
 * The selection bodies are pinned whole for a related reason: the backend cannot
 * tell an omitted field from a deliberate one, so a review flag that does not
 * reach the server auto-imports a collection the user wanted to look at first.
 *
 * Job reads are uncached. Polling a cached job status never sees it finish.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  addGcodeRevision,
  getJobStatus,
  ingestArchive,
  ingestModel,
  ingestOrca,
  ingestUrl,
  inspectArchive,
  listIngestJobs,
  selectArchiveEntries,
  selectCollectionMembers,
  selectModelFiles,
} from "@/lib/api/models";
import { invalidateApiCache } from "@/lib/api/request";

import { expectRequest, fetchMock, lastBody, lastCall, respondWith } from "../_wire";

const QUEUED = { job_id: "job-1", state: "pending", message: "ingestion queued" };

function form(): FormData {
  const data = new FormData();
  data.append("file", new File(["x"], "part.gcode"));
  return data;
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("multipart uploads", () => {
  it.each([
    ["a sliced G-code file", () => ingestOrca(form()), "/api/v1/ingest/orca"],
    ["a source mesh", () => ingestModel(form()), "/api/v1/ingest/model"],
    ["an archive", () => ingestArchive(form()), "/api/v1/ingest/archive"],
    ["an archive for inspection", () => inspectArchive(form()), "/api/v1/ingest/archive/inspect"],
    ["a G-code revision", () => addGcodeRevision(4, form()), "/api/v1/models/4/gcode-revisions"],
  ])("POSTs %s as multipart", async (_name, call, url) => {
    respondWith(QUEUED);

    await call();

    // A JSON-serialised object type-checks and arrives as an unparseable body.
    expectRequest(url, "POST");
    expect(lastCall().init.body).toBeInstanceOf(FormData);
  });
});

describe("ingestUrl", () => {
  it("POSTs the review flag for collection imports", async () => {
    respondWith(QUEUED);

    await ingestUrl({
      url: "https://www.printables.com/@u/collections/3525050",
      review: true,
    });

    expectRequest("/api/v1/ingest/url", "POST");
    expect(lastBody()).toMatchObject({
      url: "https://www.printables.com/@u/collections/3525050",
      review: true,
    });
  });
});

describe("selectModelFiles", () => {
  it("POSTs the chosen file ids to the files token endpoint", async () => {
    respondWith(QUEUED);

    await selectModelFiles("tok-files", {
      file_ids: ["10", "11"],
      collection: "Cats",
    });

    expectRequest("/api/v1/ingest/url/files/tok-files/select", "POST");
    expect(lastBody()).toEqual({ file_ids: ["10", "11"], collection: "Cats" });
  });
});

describe("selectCollectionMembers", () => {
  it("POSTs the chosen member ids to the collection token endpoint", async () => {
    respondWith(QUEUED);

    await selectCollectionMembers("tok-coll", { member_ids: ["1", "2"] });

    expectRequest("/api/v1/ingest/collection/tok-coll/select", "POST");
    expect(lastBody()).toEqual({ member_ids: ["1", "2"] });
  });
});

describe("selectArchiveEntries", () => {
  it("presents the archive id back on its own endpoint", async () => {
    respondWith(QUEUED);

    await selectArchiveEntries("arch-1", { names: ["cube.stl"] });

    // The id *is* the staged work; a wrong path imports nothing and loses it.
    expectRequest("/api/v1/ingest/archive/arch-1/select", "POST");
    expect(lastBody()).toMatchObject({ names: ["cube.stl"] });
  });
});

describe("getJobStatus", () => {
  it("reads one job fresh", async () => {
    respondWith({ job_id: "abc", state: "running" });

    await getJobStatus("abc");

    // Polling a cached job status never sees it finish.
    expectRequest("/api/v1/ingest/jobs/abc");
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });
});

describe("listIngestJobs", () => {
  it("lists jobs fresh", async () => {
    respondWith([]);

    await listIngestJobs();

    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });
});
