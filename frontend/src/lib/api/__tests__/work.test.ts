/**
 * The administrator's Background work API and an Artifact's derivatives.
 *
 * Lane names and derivative kinds contain dots (`derive.native`) and reach the
 * path verbatim, so each is encoded; a lane reset sends `null` explicitly,
 * because an omitted field would leave the override in place. The events
 * ticket is a POST: an access token must never travel in a WebSocket URL.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { queryClient, queryKeys } from "@/lib/query-client";
import { clearLogin } from "@/lib/auth-store";
import { invalidateApiCache } from "@/lib/api/request";
import {
  cancelQueuedJobs,
  createEventsTicket,
  getWorkOverview,
  listDerivatives,
  regenerateDerivatives,
  retryDerivative,
  setLaneConcurrency,
  updateWorkDerivativePolicy,
} from "@/lib/api/work";

import { expectRequest, fetchMock, lastBody, lastCall, respondWith } from "./_wire";

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("getWorkOverview", () => {
  it("reads the overview fresh", async () => {
    respondWith({ lanes: [], definitions: [], executors: [], failed_jobs: [] });

    await getWorkOverview();

    expectRequest("/api/v1/admin/work");
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });
});

describe("setLaneConcurrency", () => {
  it("PUTs the new concurrency for the lane", async () => {
    respondWith({ lanes: [] });

    await setLaneConcurrency("derive.native", 3);

    expectRequest("/api/v1/admin/work/lanes/derive.native", "PUT");
    expect(lastBody()).toEqual({ concurrency: 3 });
  });

  it("sends null to return the lane to its default", async () => {
    respondWith({ lanes: [] });

    await setLaneConcurrency("ingest", null);

    expect(lastBody()).toEqual({ concurrency: null });
  });
});

describe("cancelQueuedJobs", () => {
  it("names the definition whose queued Jobs to withdraw", async () => {
    respondWith({ cancelled: 2 });

    await expect(cancelQueuedJobs("sources.scan")).resolves.toEqual({ cancelled: 2 });

    expectRequest("/api/v1/admin/work/cancel-queued", "POST");
    expect(lastBody()).toEqual({ definition: "sources.scan" });
  });
});

describe("regenerateDerivatives", () => {
  it.each([
    { label: "only what is missing", mode: "missing" as const },
    { label: "every Artifact", mode: "all" as const },
  ])("asks for $label", async ({ mode }) => {
    respondWith({ kind: "thumbnail", mode });

    await regenerateDerivatives("thumbnail", mode);

    expectRequest("/api/v1/admin/work/derivatives/thumbnail/regenerate", "POST");
    expect(lastBody()).toEqual({ mode });
  });
});

describe("listDerivatives", () => {
  it("reads an Artifact's derivatives fresh", async () => {
    respondWith([]);

    await listDerivatives(7);

    expectRequest("/api/v1/files/7/derivatives");
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });
});

describe("retryDerivative", () => {
  it("POSTs to the kind's retry action", async () => {
    respondWith([]);

    await retryDerivative(7, "thumbnail");

    expectRequest("/api/v1/files/7/derivatives/thumbnail/retry", "POST");
  });

  it("surfaces a permission refusal", async () => {
    respondWith({ detail: "forbidden" }, 403);

    await expect(retryDerivative(7, "thumbnail")).rejects.toMatchObject({ status: 403 });
  });
});

describe("createEventsTicket", () => {
  it("POSTs for a one-use ticket", async () => {
    respondWith({ ticket: "t-1", expires_in: 30 });

    await expect(createEventsTicket()).resolves.toEqual({ ticket: "t-1", expires_in: 30 });

    expectRequest("/api/v1/events/ticket", "POST");
  });
});

describe("events ticket lifetime", () => {
  it("aborts the events ticket request", async () => {
    const controller = new AbortController();
    let finish!: (response: Response) => void;
    fetchMock.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    );
    const pending = createEventsTicket(controller.signal);
    controller.abort();
    expect(lastCall().init?.signal?.aborted).toBe(true);
    finish(new Response(JSON.stringify({ ticket: "late", expires_in: 30 })));
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });

  it("rejects a late events ticket after session retirement", async () => {
    let finish!: (response: Response) => void;
    fetchMock.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    );
    const pending = createEventsTicket();
    clearLogin();
    finish(new Response(JSON.stringify({ ticket: "late", expires_in: 30 })));
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });
});

describe("work owner transport", () => {
  it("reads work through caller cancellation", async () => {
    let finish: (response: Response) => void = () => {};
    fetchMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const caller = new AbortController();
    const pending = getWorkOverview({ signal: caller.signal });
    caller.abort();
    expect(lastCall().init.signal?.aborted).toBe(true);
    finish(new Response(JSON.stringify({ lanes: [] })));
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });

  it.each([
    { label: "lane", send: () => setLaneConcurrency("ingest", 4) },
    { label: "queue", send: () => cancelQueuedJobs("ingestion.upload") },
  ])("keeps raw $label acknowledgements outside compatibility effects", async ({ send }) => {
    queryClient.setQueryData(queryKeys.vaultConfig, { sentinel: true });
    respondWith({ lanes: [], cancelled: 2 });
    await send();
    expect(queryClient.getQueryState(queryKeys.vaultConfig)?.isInvalidated).toBe(false);
  });

  it("writes only requested derivative policy fields", async () => {
    queryClient.setQueryData(queryKeys.vaultConfig, { sentinel: true });
    respondWith({ derivatives_mesh_enabled: false });
    await updateWorkDerivativePolicy({ derivatives_mesh_enabled: false });
    expectRequest("/api/v1/config", "PUT");
    expect(lastBody()).toEqual({ derivatives_mesh_enabled: false });
    expect(queryClient.getQueryState(queryKeys.vaultConfig)?.isInvalidated).toBe(false);
  });

  it("retains regeneration compatibility for settings callers", async () => {
    let finish: (response: Response) => void = () => {};
    fetchMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const caller = new AbortController();
    const pending = regenerateDerivatives("thumbnail", "all", { signal: caller.signal });
    caller.abort();
    expect(lastCall().init.signal?.aborted).toBe(true);
    finish(new Response(JSON.stringify({ kind: "thumbnail", mode: "all" })));
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });
});
