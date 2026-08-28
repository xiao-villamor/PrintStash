/**
 * The deployment-level reads: first-run setup, vault config, health, thumbnails.
 *
 * Setup and config are the two endpoints that decide where a whole library lives, so
 * a mistake here is not a wrong screen — it is a library pointed at the wrong
 * storage. The URL and the method are the contract, and they are what is pinned.
 *
 * Health is the one that must never be cached. It answers "is this install healthy
 * *right now*", and a stale answer sends an operator looking for a problem that is
 * already fixed — or, worse, not looking for one that is not. The release check is
 * the mirror image: the server caches it deliberately to stay off GitHub's rate
 * limit, so the refresh flag is the only way the "check now" button gets a fresh
 * answer.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  completeSetup,
  getHealthDetails,
  getLatestRelease,
  getSetupStatus,
  getVaultConfig,
  rebuildModelThumbnails,
  updateVaultConfig,
} from "@/lib/api/config";
import { invalidateApiCache } from "@/lib/api/request";

import { expectRequest, fetchMock, lastBody, lastCall, respondWith } from "./_wire";

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("getSetupStatus", () => {
  it("reads whether the deployment has been set up", async () => {
    respondWith({ needs_setup: true });

    await getSetupStatus();

    expectRequest("/api/v1/setup/status");
  });
});

describe("completeSetup", () => {
  it("POSTs the first-run answers", async () => {
    respondWith({ access_token: "token" });

    await completeSetup({
      setup_token: "token",
      username: "alice",
      password: "Password123",
    });

    expectRequest("/api/v1/setup", "POST");
    expect(lastBody()).toMatchObject({ username: "alice" });
  });
});

describe("getVaultConfig", () => {
  it("reads the current vault configuration", async () => {
    respondWith({ storage_backend: "local" });

    await getVaultConfig();

    expectRequest("/api/v1/config");
  });
});

describe("updateVaultConfig", () => {
  it("PUTs a change", async () => {
    respondWith({ storage_backend: "s3" });

    await updateVaultConfig({ storage_backend: "s3" });

    expectRequest("/api/v1/config", "PUT");
    expect(lastBody()).toEqual({ storage_backend: "s3" });
  });
});

describe("getHealthDetails", () => {
  it("reads the details without caching them", async () => {
    respondWith({ status: "ok" });

    await getHealthDetails();

    // A stale health answer sends an operator looking for a problem that is
    // already fixed, or not looking for one that is not.
    expectRequest("/api/v1/health/details");
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });
});

describe("getLatestRelease", () => {
  it("reads the cached release status by default", async () => {
    respondWith({ status: "up_to_date", update_available: false });

    await getLatestRelease();

    expectRequest("/api/v1/health/releases/latest");
  });

  it("forces a re-check when the operator asks for one", async () => {
    respondWith({ status: "up_to_date", update_available: false });

    await getLatestRelease(true);

    // The server caches this to stay off GitHub's rate limit; the flag is how
    // the "check now" button gets past it.
    expectRequest("/api/v1/health/releases/latest?refresh=true");
  });
});

describe("rebuildModelThumbnails", () => {
  it("asks for a forced rebuild", async () => {
    respondWith({ job_id: "abc", state: "pending" });

    await rebuildModelThumbnails();

    expectRequest("/api/v1/files/thumbnails/rebuild?force=true", "POST");
  });
});
