/**
 * The auth API client: logging in, reading who you are, and managing API keys.
 *
 * Two things here are not interchangeable and the tests pin both. Anything that answers
 * "who am I, right now" — `getMe`, the API-key list, the admin user list — must go to the
 * network with `no-store`, because a cached answer after a role change or a logout is a
 * UI showing permissions the server no longer grants. Everything else may cache.
 *
 * The admin endpoints live on a different router (`/admin`) than the rest of this module,
 * which is easy to get wrong in a URL and impossible to notice until an operator cannot
 * add a user.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createAdminUser,
  createApiKey,
  deactivateAdminUser,
  getAuthProviders,
  getMe,
  listAdminUsers,
  listApiKeys,
  login,
  logout,
  oidcLoginUrl,
  resetAdminUserPassword,
  revokeApiKey,
  updateAdminUser,
} from "@/lib/api/auth";
import { invalidateApiCache } from "@/lib/api/request";

import { expectRequest, fetchMock, lastBody, lastCall, respondWith } from "./_wire";

const USER = { id: 1, username: "alice", is_active: true, is_superuser: false };

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("getAuthProviders", () => {
  it("asks which login methods the deployment offers", async () => {
    respondWith({ password: true, oidc: false });

    const providers = await getAuthProviders();

    expect(providers).toEqual({ password: true, oidc: false });
    expectRequest("/api/v1/auth/providers");
  });
});

describe("oidcLoginUrl", () => {
  it("builds the redirect the browser navigates to", () => {
    // A navigation, not a fetch: the browser has to leave the app for the IdP.
    expect(oidcLoginUrl()).toBe("/api/v1/auth/oidc/login");
  });
});

describe("login", () => {
  it("POSTs the credentials and returns the token", async () => {
    respondWith({ access_token: "token", token_type: "bearer" });

    const result = await login({ username: "alice", password: "secret" });

    expect(result.access_token).toBe("token");
    expectRequest("/api/v1/auth/login", "POST");
    expect(lastBody()).toEqual({ username: "alice", password: "secret" });
  });
});

describe("logout", () => {
  it("POSTs to the logout endpoint", async () => {
    respondWith(null, 204);

    await logout();

    expectRequest("/api/v1/auth/logout", "POST");
  });
});

describe("getMe", () => {
  it("returns the current user", async () => {
    respondWith(USER);

    expect(await getMe()).toEqual(USER);
  });

  it("never serves a cached identity", async () => {
    respondWith(USER);

    await getMe();

    // A cached answer after a role change shows permissions the server no
    // longer grants.
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });
});

describe("listApiKeys", () => {
  it("returns the caller's keys", async () => {
    respondWith([{ id: 1, name: "laptop" }]);

    expect(await listApiKeys()).toHaveLength(1);
    expectRequest("/api/v1/auth/api-keys");
  });

  it("never serves a cached list", async () => {
    respondWith([]);

    await listApiKeys();

    // A revoked key that still appears is a key somebody thinks still works.
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });
});

describe("createApiKey", () => {
  it("POSTs the name and returns the one-time secret", async () => {
    respondWith({ id: 2, name: "ci", api_key: "printstash_abc" });

    const created = await createApiKey("ci");

    expect(created.api_key).toBe("printstash_abc");
    expectRequest("/api/v1/auth/api-keys", "POST");
    expect(lastBody()).toEqual({ name: "ci" });
  });
});

describe("revokeApiKey", () => {
  it("DELETEs the key", async () => {
    respondWith(null, 204);

    await revokeApiKey(9);

    expectRequest("/api/v1/auth/api-keys/9", "DELETE");
  });
});

describe("listAdminUsers", () => {
  it("reads the admin router, not the auth one", async () => {
    respondWith([USER]);

    await listAdminUsers();

    expectRequest("/api/v1/admin/users");
  });

  it("bypasses the cache", async () => {
    respondWith([]);

    await listAdminUsers();

    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });
});

describe("createAdminUser", () => {
  it("POSTs the new user", async () => {
    respondWith(USER);

    await createAdminUser({ username: "bob", password: "Password123" });

    expectRequest("/api/v1/admin/users", "POST");
    expect(lastBody()).toMatchObject({ username: "bob" });
  });
});

describe("updateAdminUser", () => {
  it("PATCHes only what changed", async () => {
    respondWith(USER);

    await updateAdminUser(3, { is_superuser: true });

    expectRequest("/api/v1/admin/users/3", "PATCH");
    expect(lastBody()).toEqual({ is_superuser: true });
  });
});

describe("resetAdminUserPassword", () => {
  it("POSTs to the password sub-resource", async () => {
    respondWith(USER);

    await resetAdminUserPassword(3, { password: "NewPassword123" });

    // A separate endpoint from the user PATCH, so a password change is audited
    // as its own act.
    expectRequest("/api/v1/admin/users/3/password", "POST");
  });
});

describe("deactivateAdminUser", () => {
  it("DELETEs the user", async () => {
    respondWith(null, 204);

    await deactivateAdminUser(3);

    expectRequest("/api/v1/admin/users/3", "DELETE");
  });
});
