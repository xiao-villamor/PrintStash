/**
 * The printer-profile client: the machines a user has described to PrintStash,
 * separately from the printers it actually talks to.
 *
 * A thin translation from a function call to one HTTP request. A wrong path or
 * verb still type-checks, still compiles, and fails only against a running
 * backend — so every case here asserts the request that was made rather than the
 * value that came back.
 */
import { afterEach, beforeEach, describe, it, vi } from "vitest";

import {
  createPrinterProfile,
  deletePrinterProfile,
  listPrinterProfiles,
  updatePrinterProfile,
} from "@/lib/api/printer-profiles";
import { invalidateApiCache } from "@/lib/api/request";

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

describe("listPrinterProfiles", () => {
  it("reads the profiles the user has saved", async () => {
    respondWith([]);

    await listPrinterProfiles();

    expectRequest("/api/v1/printer-profiles");
  });
});

describe("createPrinterProfile", () => {
  it("POSTs a new profile", async () => {
    respondWith({ id: 1, name: "Voron" });

    await createPrinterProfile({ name: "Voron" });

    expectRequest("/api/v1/printer-profiles", "POST");
  });
});

describe("updatePrinterProfile", () => {
  it("PATCHes only what changed", async () => {
    respondWith({ id: 1, name: "Voron" });

    await updatePrinterProfile(1, { name: "Voron 2.4" });

    expectRequest("/api/v1/printer-profiles/1", "PATCH");
  });
});

describe("deletePrinterProfile", () => {
  it("deletes one by id", async () => {
    respondWith(null, 204);

    await deletePrinterProfile(1);

    expectRequest("/api/v1/printer-profiles/1", "DELETE");
  });
});
