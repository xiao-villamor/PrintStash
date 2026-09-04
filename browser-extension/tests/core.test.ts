import assert from "node:assert/strict";
import { test } from "vitest";

import {
  BROWSER_EXTENSION_SETUP_STORAGE_KEY,
  captureModelPage,
  claimBrowserPairing,
  classifyModelPage,
  isLocalVault,
  normalizeVault,
  parseBrowserExtensionSetup,
  verifyVaultConnection,
} from "../core.ts";
import { buildBrowserCaptureMessage } from "../capture-adapter.ts";

function requestUrl(input: URL | RequestInfo): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.href;
  return input.url;
}

function itemAt<T>(items: readonly T[], index: number): T {
  const item = items.at(index);
  if (item === undefined) throw new Error(`Missing test item at index ${index}`);
  return item;
}

function headerValue(options: RequestInit, name: string): string | undefined {
  return new Headers(options.headers).get(name) ?? undefined;
}

function stringBody(options: RequestInit): string {
  if (typeof options.body !== "string") throw new TypeError("Expected a string request body");
  return options.body;
}

test("claims a pairing code and retains only the returned browser credential", async () => {
  const calls: Array<{ url: string; options: RequestInit }> = [];
  const claimed = await claimBrowserPairing({
    fetchImpl: async (url, options = {}) => {
      const normalizedUrl = requestUrl(url);
      calls.push({ url: normalizedUrl, options });
      if (normalizedUrl.endsWith("/health")) {
        return Response.json({ status: "ok", name: "PrintStash" });
      }
      return Response.json({
        credential: "device-secret",
        device: { id: 3, name: "Browser extension" },
      });
    },
    vault: "https://prints.example.com",
    code: "pairing-secret",
    name: "Browser extension",
  });
  assert.deepEqual(claimed, {
    base: "https://prints.example.com",
    deviceCredential: "device-secret",
    device: { id: 3, name: "Browser extension" },
  });
  const claimCall = itemAt(calls, 1);
  assert.equal(claimCall.url, "https://prints.example.com/api/v1/browser-pairings/claim");
  assert.equal(stringBody(claimCall.options).includes("pairing-secret"), true);
});

test("keeps invalid pairing responses opaque", async () => {
  await assert.rejects(
    claimBrowserPairing({
      fetchImpl: async (url) =>
        requestUrl(url).endsWith("/health")
          ? Response.json({ status: "ok", name: "PrintStash" })
          : Response.json(
              { detail: "invalid_or_expired_pairing_code", debug: "pairing-secret" },
              { status: 400 },
            ),
      vault: "https://prints.example.com",
      code: "pairing-secret",
    }),
    (error: Error) => {
      assert.equal(
        error.message,
        "That pairing code is invalid or expired. Create a new one in PrintStash.",
      );
      assert.equal(error.message.includes("pairing-secret"), false);
      return true;
    },
  );
});

test("explains how to resolve a duplicate browser device name", async () => {
  await assert.rejects(
    claimBrowserPairing({
      fetchImpl: async (url) =>
        requestUrl(url).endsWith("/health")
          ? Response.json({ status: "ok", name: "PrintStash" })
          : Response.json(
              { detail: "browser_device_name_in_use", secret: "pairing-secret" },
              { status: 409 },
            ),
      vault: "https://prints.example.com",
      code: "pairing-secret",
      name: "Browser extension",
    }),
    (error: Error) => {
      assert.equal(
        error.message,
        "A browser with this name is already paired. Revoke it in PrintStash or choose a different device name.",
      );
      assert.equal(error.message.includes("pairing-secret"), false);
      return true;
    },
  );
});

test("does not expose unexpected pairing response bodies", async () => {
  await assert.rejects(
    claimBrowserPairing({
      fetchImpl: async (url) =>
        requestUrl(url).endsWith("/health")
          ? Response.json({ status: "ok", name: "PrintStash" })
          : Response.json(
              { detail: "database exploded", secret: "pairing-secret" },
              { status: 503 },
            ),
      vault: "https://prints.example.com",
      code: "pairing-secret",
    }),
    (error: Error) => {
      assert.equal(error.message, "PrintStash could not complete pairing. Try again.");
      assert.equal(error.message.includes("database exploded"), false);
      assert.equal(error.message.includes("pairing-secret"), false);
      return true;
    },
  );
});

test("uses a paired browser credential for capture without legacy login fields", async () => {
  const calls: Array<{ url: string; options: RequestInit }> = [];
  await captureModelPage({
    fetchImpl: async (url, options = {}) => {
      calls.push({ url: requestUrl(url), options });
      return Response.json({ id: 1, state: "captured" }, { status: 202 });
    },
    vault: "https://prints.example.com",
    deviceCredential: "device-secret",
    pageUrl: "https://www.printables.com/model/3161-3d-benchy/files",
    title: "3DBenchy",
  });
  assert.equal(calls.length, 1);
  assert.equal(headerValue(itemAt(calls, 0).options, "Authorization"), "Bearer device-secret");
});

test("rejects rich Printables capture before contacting the legacy inbox endpoint", async () => {
  const calls: Array<{ url: string; options: RequestInit }> = [];
  const captureSource = buildBrowserCaptureMessage({
    provider: "Printables",
    pageUrl: "https://www.printables.com/model/3161-3d-benchy/files?source-cookie=secret",
    pageTitle: "3DBenchy",
  }).source;

  await assert.rejects(
    captureModelPage({
      fetchImpl: async (url, options = {}) => {
        calls.push({ url: requestUrl(url), options });
        return Response.json({ id: 1, state: "captured" }, { status: 202 });
      },
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_secret",
      accessToken: "vault-jwt",
      pageUrl: "https://www.printables.com/model/3161-3d-benchy/files",
      captureSource,
    }),
    (error: Error) => {
      assert.match(error.message, /user_file_required/);
      assert.match(error.message, /local file/i);
      assert.equal(error.message.includes("psk_secret"), false);
      assert.equal(error.message.includes("vault-jwt"), false);
      assert.equal(error.message.includes("source-cookie"), false);
      return true;
    },
  );

  assert.equal(
    calls.some(({ url }) => url.endsWith("/api/v1/inbox")),
    false,
  );
  assert.equal(calls.length, 0);
});

test("recognizes supported provider pages and direct model downloads", () => {
  assert.equal(classifyModelPage("https://makerworld.com/en/models/1234-widget"), "MakerWorld");
  assert.equal(classifyModelPage("https://www.makerworld.com/en/collections/42-parts"), null);
  assert.equal(
    classifyModelPage("https://www.printables.com/model/3161-3d-benchy/files"),
    "Printables",
  );
  assert.equal(classifyModelPage("https://www.printables.com/@user/collections/77"), "Printables");
  assert.equal(classifyModelPage("https://www.thingiverse.com/thing:763622/files"), "Thingiverse");
  assert.equal(classifyModelPage("https://thingiverse.com/things/763622"), "Thingiverse");
  assert.equal(classifyModelPage("https://cults3d.com/en/3d-model/art/cult-cube"), "Cults");
  assert.equal(classifyModelPage("https://evilcults3d.com/en/3d-model/art/cult-cube"), null);
  assert.equal(
    classifyModelPage("https://cdn.example.com/models/widget.3mf?download=1"),
    "Direct file",
  );
  assert.equal(classifyModelPage("https://cdn.example.com/archive/parts.ZIP#files"), "Direct file");
  assert.equal(classifyModelPage("https://example.com/model/3161"), null);
  assert.equal(classifyModelPage("https://evilmakerworld.com/models/123"), null);
  assert.equal(classifyModelPage("https://evil.makerworld.com/models/123"), null);
  assert.equal(classifyModelPage("https://evilthingiverse.com/thing:763622"), null);
  assert.equal(classifyModelPage("https://cdn.example.com/models/widget.pdf"), null);
});

test("keeps the generic capture seam from using the legacy MakerWorld upload path", async () => {
  let called = false;
  await assert.rejects(
    captureModelPage({
      fetchImpl: async () => {
        called = true;
        return Response.json({ access_token: "jwt" });
      },
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_secret",
      pageUrl: "https://makerworld.com/en/models/1234-widget",
    }),
    /active-tab package confirmation flow/,
  );
  assert.equal(called, false);
});

test("normalizes a self-hosted Vault URL without accepting credentials", () => {
  assert.equal(
    normalizeVault(" https://prints.example.com/app/ "),
    "https://prints.example.com/app",
  );
  assert.throws(() => normalizeVault("ftp://prints.example.com"), /HTTP or HTTPS/);
  assert.throws(() => normalizeVault("https://admin:secret@prints.example.com"), /credentials/);
});

test("defaults localhost and private Vault addresses to HTTP", () => {
  assert.equal(normalizeVault("localhost:8000"), "http://localhost:8000");
  assert.equal(normalizeVault("127.0.0.1:3000/"), "http://127.0.0.1:3000");
  assert.equal(normalizeVault("192.168.1.20:8080"), "http://192.168.1.20:8080");
  assert.equal(normalizeVault("prints.example.com"), "https://prints.example.com");
  assert.equal(isLocalVault("http://localhost:8000"), true);
  assert.equal(isLocalVault("https://prints.example.com"), false);
});

test("accepts only fresh same-origin setup packages from the active PrintStash tab", () => {
  const now = Date.UTC(2026, 7, 21, 12, 0, 0);
  const payload = JSON.stringify({
    version: 1,
    vault: "http://localhost:3000",
    username: " owner ",
    apiKey: " psk_browser ",
    expiresAt: now + 5 * 60 * 1000,
  });
  assert.equal(BROWSER_EXTENSION_SETUP_STORAGE_KEY, "printstash.browser-extension-setup:v1");
  assert.deepEqual(parseBrowserExtensionSetup(payload, "http://localhost:3000/settings", now), {
    vault: "http://localhost:3000",
    username: "owner",
    apiKey: "psk_browser",
  });
  assert.equal(parseBrowserExtensionSetup(payload, "https://attacker.example/settings", now), null);
  assert.equal(
    parseBrowserExtensionSetup(payload, "http://localhost:3000/settings", now + 6 * 60 * 1000),
    null,
  );
});

test("connects a scheme-less loopback Vault over HTTP", async () => {
  const calls: Array<{ url: string; options: RequestInit }> = [];
  await verifyVaultConnection({
    fetchImpl: async (url, options = {}) => {
      const normalizedUrl = requestUrl(url);
      calls.push({ url: normalizedUrl, options });
      if (normalizedUrl.endsWith("/health")) {
        return Response.json({ status: "ok", name: "PrintStash" });
      }
      if (normalizedUrl.endsWith("/auth/login")) {
        return Response.json({ access_token: "jwt" });
      }
      return Response.json({ username: "owner", is_superuser: true });
    },
    vault: "localhost:8000",
    username: "owner",
    apiKey: "psk_browser",
  });

  assert.equal(calls.length, 3);
  assert.match(itemAt(calls, 0).url, /^http:\/\/localhost:8000\//);
});

test("verifies the PrintStash service and authenticated user before connecting", async () => {
  const calls: Array<{ url: string; options: RequestInit }> = [];
  const fetchImpl = async (url: URL | RequestInfo, options: RequestInit = {}) => {
    const normalizedUrl = requestUrl(url);
    calls.push({ url: normalizedUrl, options });
    if (normalizedUrl.endsWith("/health")) {
      return Response.json({ status: "ok", name: "PrintStash" });
    }
    if (normalizedUrl.endsWith("/auth/login")) {
      return Response.json({ access_token: "jwt", scope: "admin" });
    }
    return Response.json({
      id: 7,
      username: "owner",
      email: null,
      is_superuser: true,
      is_active: true,
      oidc_managed: false,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    });
  };

  const result = await verifyVaultConnection({
    fetchImpl,
    vault: "https://prints.example.com/",
    username: " owner ",
    apiKey: " psk_secret ",
  });

  assert.equal(result.base, "https://prints.example.com");
  assert.equal(result.accessToken, "jwt");
  assert.equal(result.user.username, "owner");
  assert.equal(result.user.is_superuser, true);
  assert.deepEqual(
    calls.map((call) => call.url),
    [
      "https://prints.example.com/api/v1/health",
      "https://prints.example.com/api/v1/auth/login",
      "https://prints.example.com/api/v1/auth/me",
    ],
  );
  assert.equal(headerValue(itemAt(calls, 0).options, "Authorization"), undefined);
  assert.deepEqual(JSON.parse(stringBody(itemAt(calls, 1).options)), {
    username: "owner",
    api_key: "psk_secret",
    remember_me: false,
  });
  assert.equal(headerValue(itemAt(calls, 2).options, "Authorization"), "Bearer jwt");
});

test("does not send credentials when the configured URL is not PrintStash", async () => {
  const calls: Array<{ url: string; options: RequestInit }> = [];
  await assert.rejects(
    verifyVaultConnection({
      fetchImpl: async (url, options = {}) => {
        calls.push({ url: requestUrl(url), options });
        return Response.json({ status: "ok", name: "Another service" });
      },
      vault: "https://wrong.example.com",
      username: "owner",
      apiKey: "psk_secret",
    }),
    /not a PrintStash server/,
  );
  assert.equal(calls.length, 1);
  assert.equal(itemAt(calls, 0).options.body, undefined);
});

test("turns API login codes and network failures into actionable connection errors", async () => {
  const health = () => Response.json({ status: "ok", name: "PrintStash" });
  let request = 0;
  await assert.rejects(
    verifyVaultConnection({
      fetchImpl: async () => {
        request += 1;
        return request === 1
          ? health()
          : Response.json({ detail: "invalid_credentials" }, { status: 401 });
      },
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "wrong",
    }),
    /username or API key is incorrect/,
  );

  await assert.rejects(
    verifyVaultConnection({
      fetchImpl: async () => {
        throw new TypeError("fetch failed");
      },
      vault: "https://offline.example.com",
      username: "owner",
      apiKey: "psk_secret",
    }),
    /Couldn't reach PrintStash at offline\.example\.com/,
  );
});

test("logs in with a named API key and captures the browser source", async () => {
  const calls: Array<{ url: string; options: RequestInit; body: unknown }> = [];
  const fetchImpl = async (url: URL | RequestInfo, options: RequestInit = {}) => {
    const normalizedUrl = requestUrl(url);
    const body = typeof options.body === "string" ? JSON.parse(options.body) : null;
    calls.push({ url: normalizedUrl, options, body });
    if (normalizedUrl.endsWith("/auth/login")) {
      return Response.json({ access_token: "jwt" });
    }
    return Response.json({ id: 9, state: "captured" });
  };

  const result = await captureModelPage({
    fetchImpl,
    vault: "https://prints.example.com/",
    username: "owner",
    apiKey: "psk_secret",
    pageUrl: "https://www.printables.com/model/3161-3d-benchy/files",
    title: "3DBenchy",
  });

  assert.equal(result.source, "Printables");
  assert.equal(result.item.id, 9);
  assert.equal(result.inboxUrl, "https://prints.example.com/inbox");
  assert.deepEqual(itemAt(calls, 0).body, {
    username: "owner",
    api_key: "psk_secret",
    remember_me: false,
  });
  assert.deepEqual(itemAt(calls, 1).body, {
    url: "https://www.printables.com/model/3161-3d-benchy/files",
    title: "3DBenchy",
    source_kind: "browser",
  });
  assert.equal(headerValue(itemAt(calls, 1).options, "Authorization"), "Bearer jwt");
});

test("captures Thingiverse and direct-file URLs through the server resolver", async () => {
  for (const [pageUrl, source] of [
    ["https://www.thingiverse.com/thing:763622/files", "Thingiverse"],
    ["https://cdn.example.com/models/widget.stl?download=1", "Direct file"],
  ]) {
    const calls: Array<{ url: string; options: RequestInit }> = [];
    const fetchImpl = async (url: URL | RequestInfo, options: RequestInit = {}) => {
      const normalizedUrl = requestUrl(url);
      calls.push({ url: normalizedUrl, options });
      if (normalizedUrl.endsWith("/auth/login")) {
        return new Response(JSON.stringify({ access_token: "jwt" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ id: 11, state: "captured" }), {
        status: 202,
        headers: { "Content-Type": "application/json" },
      });
    };

    const result = await captureModelPage({
      fetchImpl,
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_secret",
      pageUrl,
      title: "Captured model",
    });

    assert.equal(result.source, source);
    const captureCall = itemAt(calls, 1);
    assert.equal(captureCall.url, "https://prints.example.com/api/v1/inbox");
    assert.deepEqual(JSON.parse(stringBody(captureCall.options)), {
      url: pageUrl.replace(/\?.*$/, ""),
      title: "Captured model",
      source_kind: "browser",
    });
  }
});

test("reuses an already verified access token for capture", async () => {
  const calls: Array<{ url: string; options: RequestInit }> = [];
  const result = await captureModelPage({
    fetchImpl: async (url, options = {}) => {
      calls.push({ url: requestUrl(url), options });
      return Response.json({ id: 12, state: "captured" }, { status: 202 });
    },
    vault: "https://prints.example.com",
    username: "owner",
    apiKey: "psk_secret",
    accessToken: "verified-jwt",
    pageUrl: "https://www.thingiverse.com/thing:763622/files",
    title: "Whistle",
  });

  assert.equal(result.item.id, 12);
  assert.equal(calls.length, 1);
  const captureCall = itemAt(calls, 0);
  assert.equal(captureCall.url, "https://prints.example.com/api/v1/inbox");
  assert.equal(headerValue(captureCall.options, "Authorization"), "Bearer verified-jwt");
});

test("keeps vault and source credentials out of the capture payload", async () => {
  const calls: Array<{ url: string; options: RequestInit }> = [];
  await captureModelPage({
    fetchImpl: async (url, options = {}) => {
      const normalizedUrl = requestUrl(url);
      calls.push({ url: normalizedUrl, options });
      if (normalizedUrl.endsWith("/auth/login")) {
        return Response.json({ access_token: "vault-jwt" });
      }
      return Response.json({ id: 18, state: "captured" }, { status: 202 });
    },
    vault: "https://prints.example.com",
    username: "owner",
    apiKey: "psk_vault_secret",
    pageUrl: "https://www.printables.com/model/3161-3d-benchy/files?session=source-cookie",
    title: "3DBenchy",
  });

  const capture = itemAt(calls, -1);
  const payload = stringBody(capture.options);
  assert.equal(capture.options.credentials, undefined);
  assert.equal(headerValue(capture.options, "Cookie"), undefined);
  assert.equal(payload.includes("psk_vault_secret"), false);
  assert.equal(payload.includes("vault-jwt"), false);
  assert.equal(payload.includes("source-cookie"), false);
});

test("rejects unsupported pages before sending credentials", async () => {
  let called = false;
  await assert.rejects(
    captureModelPage({
      fetchImpl: async () => {
        called = true;
        return Response.json({});
      },
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_secret",
      pageUrl: "https://example.com/model/1",
    }),
    /supported model page or direct model file/,
  );
  assert.equal(called, false);
});
