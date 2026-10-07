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
  beginSetup,
  checkSetupStorage,
  prepareSetupStorage,
  completeSetup,
  enrollStorageRoot,
  getHealthDetails,
  getLatestRelease,
  getSetupStatus,
  getStorageProviders,
  getVaultConfig,
  updateVaultConfig,
} from "@/lib/api/config";
import { aVaultConfig } from "@/test-support/factories";
import { json } from "@/test-support/render";

import { expectRequest, fetchMock, lastBody, lastCall, respondWith } from "./_wire";

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();

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

    await completeSetup(
      {
        username: "alice",
        password: "Password123",
      },
      "browser-csrf",
    );

    expectRequest("/api/v1/setup", "POST");
    expect(lastBody()).toMatchObject({ username: "alice" });
  });
});

describe("getVaultConfig", () => {
  it("reads the current vault configuration", async () => {
    const config = aVaultConfig({ currency: "EUR" });
    fetchMock.mockResolvedValueOnce(json(config));

    expect(await getVaultConfig()).toEqual(config);

    expectRequest("/api/v1/config");
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });
});

describe("updateVaultConfig", () => {
  it("sends the reviewed configuration base", async () => {
    const base = aVaultConfig({ edit_version: 4 });
    const saved = aVaultConfig({ edit_version: 5, currency: "EUR" });
    fetchMock.mockResolvedValueOnce(json(saved));
    expect(await updateVaultConfig({ currency: "EUR" }, { base })).toEqual(saved);
    const headers = new Headers(lastCall().init?.headers);
    expect(headers.get("If-Match")).toBe(`"vault-config-e${base.edit_epoch}-v4"`);
    expect(headers.get("X-PrintStash-Edit-Contract")).toBe("conditional-v1");
  });
  it.each([
    { edit_epoch: "invalid" },
    { edit_version: 0 },
    { edit_version: -1 },
    { edit_version: Number.MAX_SAFE_INTEGER + 1 },
  ])("rejects an invalid configuration base before dispatch: %j", async (over) => {
    await expect(
      updateVaultConfig({ currency: "EUR" }, { base: aVaultConfig(over) }),
    ).rejects.toThrow("Invalid editing base");
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it.each([
    { edit_version: 4 },
    { edit_version: 3 },
    { edit_epoch: "b".repeat(32) },
    { edit_epoch: "invalid" },
  ])("rejects an invalid conditional configuration receipt: %j", async (over) => {
    fetchMock.mockResolvedValueOnce(json(aVaultConfig({ edit_version: 5, ...over })));
    await expect(
      updateVaultConfig({ currency: "EUR" }, { base: aVaultConfig({ edit_version: 4 }) }),
    ).rejects.toThrow(/Invalid editing (acknowledgement|base)/);
  });
  it("PUTs a change", async () => {
    const acknowledged = aVaultConfig({ edit_version: 2, storage_backend: "s3" });
    fetchMock.mockResolvedValueOnce(json(acknowledged));

    expect(await updateVaultConfig({ storage_backend: "s3" }, { base: aVaultConfig() })).toEqual(
      acknowledged,
    );

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

describe("enrollStorageRoot", () => {
  it("POSTs an explicit confirmation for the selected root role", async () => {
    respondWith({ enrolled: true, role: "data", restart_required: true });

    await enrollStorageRoot("data", "/reviewed/files");

    expectRequest("/api/v1/config/storage-roots/enroll", "POST");
    expect(lastBody()).toEqual({ role: "data", confirm: true, expected_path: "/reviewed/files" });
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

describe("browser preparation contracts", () => {
  it("starts the preparation session without a manual credential", async () => {
    respondWith({ csrf: "automatic-proof", expires_in: 3600 });
    await beginSetup();
    expectRequest("/api/v1/setup/session", "POST");
    expect(lastBody()).toEqual({});
  });
  it("attaches the anti-CSRF proof to storage checks", async () => {
    respondWith({ ready: true, checks: [] });
    await checkSetupStorage({ storage_backend: "local" }, "automatic-proof");
    expect(new Headers(lastCall().init.headers).get("X-PrintStash-Setup-CSRF")).toBe(
      "automatic-proof",
    );
  });
  it("uses the authenticated preparation recovery endpoint", async () => {
    respondWith({ ready: true, checks: [] });
    await prepareSetupStorage();
    expectRequest("/api/v1/setup/prepare-storage", "POST");
  });
  it("retries preparation with an empty body", async () => {
    respondWith({ ready: true, checks: [] });
    await prepareSetupStorage();
    expect(lastBody()).toEqual({});
  });
  it("sends a storage choice to the preparation endpoint", async () => {
    respondWith({ ready: true, checks: [] });
    await prepareSetupStorage({ storage_backend: "local" });
    expect(lastBody()).toEqual({ storage_backend: "local" });
  });
});

describe("entry endpoint cancellation", () => {
  it.each([
    { label: "status", send: (signal: AbortSignal) => getSetupStatus({ signal }) },
    { label: "catalog", send: (signal: AbortSignal) => getStorageProviders({ signal }) },
    { label: "session", send: (signal: AbortSignal) => beginSetup({ signal }) },
    {
      label: "check",
      send: (signal: AbortSignal) =>
        checkSetupStorage({ storage_provider: "local" }, "automatic-proof", { signal }),
    },
    {
      label: "complete",
      send: (signal: AbortSignal) =>
        completeSetup(
          { username: "maker", password: "password", storage_provider: "local" },
          "automatic-proof",
          { signal },
        ),
    },
  ])("aborts the $label setup endpoint", async ({ send }) => {
    let finish: (response: Response) => void = () => {};
    fetchMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const caller = new AbortController();

    const pending = send(caller.signal);
    caller.abort();

    expect(lastCall().init.signal?.aborted).toBe(true);
    finish(new Response(JSON.stringify({ acknowledged: true })));
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });
});

describe("configuration caller cancellation", () => {
  it("cancels an active configuration read", async () => {
    const controller = new AbortController();
    let signal: AbortSignal | null | undefined;
    fetchMock.mockImplementation(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          signal = init?.signal;
          signal?.addEventListener("abort", () => reject(signal?.reason), { once: true });
        }),
    );
    const read = getVaultConfig({ signal: controller.signal });
    controller.abort();
    await expect(read).rejects.toMatchObject({ name: "AbortError" });
    expect(signal?.aborted).toBe(true);
  });
});

describe("storage provider caller boundary", () => {
  it("cancels a provider read at its caller boundary", async () => {
    const controller = new AbortController();
    let signal: AbortSignal | null | undefined;
    fetchMock.mockImplementation(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          signal = init?.signal;
          signal?.addEventListener("abort", () => reject(signal?.reason), { once: true });
        }),
    );
    const read = getStorageProviders({ signal: controller.signal });
    controller.abort();
    await expect(read).rejects.toMatchObject({ name: "AbortError" });
    expect(signal?.aborted).toBe(true);
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });
});
