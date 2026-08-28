/**
 * The provider-connections API client: linked accounts and paired browsers.
 *
 * Everything readable here is read `fresh`, and for once the reason is not staleness in
 * the ordinary sense. These lists answer "what currently has access to my library" — which
 * accounts are linked, which browsers are paired — and a cached answer after a disconnect
 * or a revoke shows somebody access they no longer have, or hides access they do.
 *
 * The two routers are deliberately separate: provider connections are credentials the
 * *user* holds for someone else's service, while browser pairings are credentials someone
 * else's browser holds for *this* library. Mixing their paths would be a permissions
 * mistake, not just a 404.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  authorizeMyMiniFactory,
  connectCults,
  createBrowserPairing,
  disconnectProvider,
  listBrowserDevices,
  listProviderConnections,
  renameBrowserDevice,
  revokeBrowserDevice,
} from "@/lib/api/provider-connections";
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

describe("listProviderConnections", () => {
  it("lists which providers are linked", async () => {
    respondWith([{ provider: "cults", connected: false, updated_at: null }]);

    await listProviderConnections();

    expectRequest("/api/v1/provider-connections");
  });

  it("never serves a cached answer", async () => {
    respondWith([]);

    await listProviderConnections();

    // A cached list after a disconnect shows access that is already gone.
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });
});

describe("authorizeMyMiniFactory", () => {
  it("asks for the URL to send the browser to", async () => {
    respondWith({ authorization_url: "https://www.myminifactory.com/oauth" });

    const { authorization_url } = await authorizeMyMiniFactory();

    expect(authorization_url).toContain("myminifactory");
    expectRequest("/api/v1/provider-connections/myminifactory/authorize", "POST");
  });
});

describe("connectCults", () => {
  it("POSTs the credentials the user typed", async () => {
    respondWith({ provider: "cults", connected: true });

    await connectCults({ username: "alice", password: "secret" });

    expectRequest("/api/v1/provider-connections/cults/connect", "POST");
    expect(lastBody()).toEqual({ username: "alice", password: "secret" });
  });
});

describe("disconnectProvider", () => {
  it("DELETEs the named provider's connection", async () => {
    respondWith(null, 204);

    await disconnectProvider("cults");

    expectRequest("/api/v1/provider-connections/cults/disconnect", "DELETE");
  });
});

describe("createBrowserPairing", () => {
  it("asks for a pairing code", async () => {
    respondWith({ code: "abc", expires_at: "2026-01-01T00:05:00Z" });

    const { code } = await createBrowserPairing();

    expect(code).toBe("abc");
    expectRequest("/api/v1/browser-pairings", "POST");
  });
});

describe("listBrowserDevices", () => {
  it("lists the paired browsers", async () => {
    respondWith([]);

    await listBrowserDevices();

    expectRequest("/api/v1/browser-pairings");
  });

  it("never serves a cached answer", async () => {
    respondWith([]);

    await listBrowserDevices();

    // A revoked browser that still shows is one somebody thinks still works.
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });
});

describe("renameBrowserDevice", () => {
  it("PATCHes the name", async () => {
    respondWith({ id: 1, name: "Laptop" });

    await renameBrowserDevice(1, { name: "Laptop" });

    expectRequest("/api/v1/browser-pairings/1", "PATCH");
    expect(lastBody()).toEqual({ name: "Laptop" });
  });
});

describe("revokeBrowserDevice", () => {
  it("DELETEs the pairing", async () => {
    respondWith(null, 204);

    await revokeBrowserDevice(1);

    expectRequest("/api/v1/browser-pairings/1", "DELETE");
  });
});
