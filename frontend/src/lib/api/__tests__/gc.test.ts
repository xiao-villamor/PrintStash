/** Garbage collection is a destructive workflow, so every transition has its own route. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  abortGcPlan,
  approveGcPlan,
  createGcPlan,
  finalizeGcPlan,
  getActiveGcPlan,
} from "@/lib/api/gc";
import { invalidateApiCache } from "@/lib/api/request";

import { expectRequest, fetchMock, lastBody, lastCall, respondWith } from "./_wire";

const plan = {
  id: 7,
  state: "preview",
  digest: "a".repeat(64),
  resource_count: 1,
  candidate_pool_count: 1,
  key_count: 4,
  size_bytes: 12,
  quarantine_until: null,
  backup_id: null,
  last_error: null,
  items: [],
};

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("getActiveGcPlan", () => {
  it("always reads the durable interlock fresh", async () => {
    respondWith(plan);

    await getActiveGcPlan();

    expectRequest("/api/v1/admin/gc");
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });
});

describe("createGcPlan", () => {
  it("creates only a preview", async () => {
    respondWith(plan);

    await createGcPlan();

    expectRequest("/api/v1/admin/gc", "POST");
    expect(lastBody()).toEqual({});
  });
});

describe("approveGcPlan", () => {
  it("binds approval to the immutable digest", async () => {
    respondWith({ ...plan, state: "quarantined" });

    await approveGcPlan(plan.id, plan.digest);

    expectRequest("/api/v1/admin/gc/7/approve", "POST");
    expect(lastBody()).toEqual({ digest: plan.digest });
  });
});

describe("abortGcPlan", () => {
  it("uses the plan-specific abort transition", async () => {
    respondWith({ ...plan, state: "aborted" });

    await abortGcPlan(plan.id);

    expectRequest("/api/v1/admin/gc/7/abort", "POST");
    expect(lastBody()).toEqual({});
  });
});

describe("finalizeGcPlan", () => {
  it("uses the plan-specific destructive transition", async () => {
    respondWith({ ...plan, state: "completed" });

    await finalizeGcPlan(plan.id);

    expectRequest("/api/v1/admin/gc/7/finalize", "POST");
    expect(lastBody()).toEqual({});
  });
});
