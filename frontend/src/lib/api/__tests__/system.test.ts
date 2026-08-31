/**
 * System operations are intentionally tiny but high impact: the restart action
 * must target the administrator endpoint with the exact mutation verb.
 */
import { afterEach, beforeEach, describe, it, vi } from "vitest";

import { restartPrintStash } from "@/lib/api/system";
import { invalidateApiCache } from "@/lib/api/request";

import { expectRequest, fetchMock, respondWith } from "./_wire";

describe("restartPrintStash", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
    invalidateApiCache();
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("POSTs the restart request", async () => {
    respondWith(null, 202);

    await restartPrintStash();

    expectRequest("/api/v1/system/restart", "POST");
  });
});
