/**
 * The print-statistics client.
 *
 * One call, one thing that can go wrong: the reporting window. The period is what
 * decides whether the numbers on the statistics page describe the last week or the
 * last year, and a dropped or misnamed parameter silently returns the server's
 * default — a page of plausible figures answering a question nobody asked.
 */
import { afterEach, beforeEach, describe, it, vi } from "vitest";

import { invalidateApiCache } from "@/lib/api/request";
import { getPrintStatistics } from "@/lib/api/statistics";

import { expectRequest, fetchMock, respondWith } from "./_wire";

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("getPrintStatistics", () => {
  it("asks for the window the caller chose", async () => {
    respondWith({ total_cost: 0 });

    await getPrintStatistics("90d");

    expectRequest("/api/v1/models/stats/prints?period=90d");
  });
});
