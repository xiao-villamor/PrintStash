/**
 * The Jobs API: how the UI follows background work it did not start and cannot
 * see directly. Reads are uncached, because polling a cached status never sees
 * a Job finish, and every id is encoded, because a Job id reaches the path
 * verbatim from storage and a stray `/` would address another route.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { cancelJob, getJobStatus, listJobs, listWorkJobs, retryJob } from "@/lib/api/jobs";
import { invalidateApiCache } from "@/lib/api/request";

import { expectRequest, fetchMock, lastBody, lastCall, respondWith } from "./_wire";

const RUNNING = { job_id: "abc", state: "running" };

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("getJobStatus", () => {
  it("reads one Job fresh", async () => {
    respondWith(RUNNING);

    await getJobStatus("abc");

    expectRequest("/api/v1/jobs/abc");
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });

  it("encodes the Job id", async () => {
    respondWith(RUNNING);

    await getJobStatus("job/1");

    expectRequest("/api/v1/jobs/job%2F1");
  });
});

describe("listJobs", () => {
  it("lists the caller's Jobs fresh", async () => {
    respondWith([]);

    await listJobs();

    expectRequest("/api/v1/jobs");
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });

  it("names the Jobs a reconnecting Task Center still tracks", async () => {
    respondWith([]);

    await listJobs(["thumbnail-job", "upload job"]);

    expectRequest("/api/v1/jobs?tracked_job_id=thumbnail-job&tracked_job_id=upload+job");
  });
});

describe("listWorkJobs", () => {
  it("includes system jobs in the administrator's active list", async () => {
    respondWith([]);

    await listWorkJobs();

    expectRequest("/api/v1/jobs?include_system=true&terminal_limit=0");
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });
});

describe("cancelJob", () => {
  it("POSTs to the Job's cancel action", async () => {
    respondWith({ ...RUNNING, state: "cancelled" });

    await cancelJob("abc");

    expectRequest("/api/v1/jobs/abc/cancel", "POST");
    expect(lastBody()).toEqual({});
  });
});

describe("retryJob", () => {
  it("POSTs to the Job's retry action", async () => {
    respondWith({ ...RUNNING, state: "queued" });

    await retryJob("abc");

    expectRequest("/api/v1/jobs/abc/retry", "POST");
  });

  it("surfaces a subject that is gone", async () => {
    respondWith({ detail: "job_subject_gone" }, 410);

    await expect(retryJob("abc")).rejects.toMatchObject({
      status: 410,
      code: "job_subject_gone",
    });
  });
});
