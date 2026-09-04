/**
 * The filament-profile client: the materials a user has told PrintStash about.
 *
 * This is a thin translation from a function call to one HTTP request, and that is
 * exactly why it needs a test: a wrong path or verb still type-checks, still
 * compiles, and fails only against a running backend. So each case asserts the
 * request that was made, not the value that came back.
 *
 * The `PATCH` body carries only what changed. A profile's cost feeds every print's
 * price, so sending the whole object back would overwrite fields another tab
 * edited in between.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createFilamentProfile,
  deleteFilamentProfile,
  listFilamentProfiles,
  updateFilamentProfile,
} from "@/lib/api/filaments";
import { invalidateApiCache } from "@/lib/api/request";

import { expectRequest, fetchMock, lastBody, respondWith } from "./_wire";

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("listFilamentProfiles", () => {
  it("reads the profiles the user has saved", async () => {
    respondWith([{ id: 1, name: "PETG" }]);

    expect(await listFilamentProfiles()).toHaveLength(1);
    expectRequest("/api/v1/filament-profiles");
  });
});

describe("createFilamentProfile", () => {
  it("POSTs a new profile", async () => {
    respondWith({ id: 1, name: "PETG" });

    await createFilamentProfile({ name: "PETG", material_type: "PETG" });

    expectRequest("/api/v1/filament-profiles", "POST");
    expect(lastBody()).toMatchObject({ name: "PETG" });
  });
});

describe("updateFilamentProfile", () => {
  it("PATCHes only what changed", async () => {
    respondWith({ id: 1, name: "PETG" });

    await updateFilamentProfile(1, { cost_per_kg: 21 });

    expectRequest("/api/v1/filament-profiles/1", "PATCH");
    expect(lastBody()).toEqual({ cost_per_kg: 21 });
  });
});

describe("deleteFilamentProfile", () => {
  it("deletes one by id", async () => {
    respondWith(null, 204);

    await deleteFilamentProfile(1);

    expectRequest("/api/v1/filament-profiles/1", "DELETE");
  });
});
