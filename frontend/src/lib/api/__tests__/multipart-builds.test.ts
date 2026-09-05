/** Manufacturing requests retain quantities, selected choices and concurrency tokens. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  archiveMultipartBuild,
  confirmBuildResult,
  createMultipartBuild,
  duplicateMultipartBuild,
  getMultipartBuild,
  listMultipartBuilds,
  queueBuildPart,
  selectBuildRevision,
} from "../multipart-builds";
import { invalidateApiCache } from "../request";
import { expectRequest, fetchMock, lastBody } from "./_wire";
import { json } from "@/test-support/render";
import { aBuild } from "@/test-support/factories";

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
  fetchMock.mockImplementation(async () => json(aBuild()));
});
afterEach(() => vi.unstubAllGlobals());

describe("Manufacturing wire contract", () => {
  it("lists archived history with an explicit page", async () => {
    await listMultipartBuilds(true, 50);
    expectRequest("/api/v1/multipart-builds?archived=true&offset=50&limit=50");
  });
  it("reads current results without a cached response", async () => {
    await getMultipartBuild(3);
    fetchMock.mockImplementation(async () => json(aBuild({ version: 2 })));
    expect((await getMultipartBuild(3)).version).toBe(2);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  it("creates the requested number of objects", async () => {
    await createMultipartBuild({ name: "Table", multipart_model_id: 7, object_quantity: 2 });
    expectRequest("/api/v1/multipart-builds", "POST");
    expect(lastBody()).toEqual({ name: "Table", multipart_model_id: 7, object_quantity: 2 });
  });
  it("selects an exact historical choice for future jobs", async () => {
    await selectBuildRevision(3, 4, { version: 2, choice_id: 8, revision_id: 9 });
    expectRequest("/api/v1/multipart-builds/3/parts/4", "PATCH");
    expect(lastBody()).toEqual({ version: 2, choice_id: 8, revision_id: 9 });
  });
  it("queues physical units with explicit excess acknowledgement", async () => {
    const body = {
      version: 2,
      units_per_job: 3,
      job_count: 2,
      confirm_excess: true,
      routing: { strategy: "manual" as const, printer_id: 5 },
    };
    await queueBuildPart(3, 4, body);
    expectRequest("/api/v1/multipart-builds/3/parts/4/queue", "POST");
    expect(lastBody()).toEqual(body);
  });
  it("preserves the confirmation concurrency tokens", async () => {
    await confirmBuildResult(3, 6, { version: 1, valid_units: 3, idempotency_key: "inspection-1" });
    expectRequest("/api/v1/multipart-builds/3/attempts/6/confirm", "POST");
    expect(lastBody()).toEqual({ version: 1, valid_units: 3, idempotency_key: "inspection-1" });
  });
  it("duplicates configuration without copying results in the request", async () => {
    await duplicateMultipartBuild(3, "Another table");
    expectRequest("/api/v1/multipart-builds/3/duplicate", "POST");
    expect(lastBody()).toEqual({ name: "Another table" });
  });
  it("unarchives the version the user reviewed", async () => {
    await archiveMultipartBuild(3, 2, false);
    expectRequest("/api/v1/multipart-builds/3/archive", "PATCH");
    expect(lastBody()).toEqual({ version: 2, archived: false });
  });
});
