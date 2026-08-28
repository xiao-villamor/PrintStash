/**
 * The bits that make this installable rather than just a website.
 *
 * A manifest and a service worker are inert files until a browser parses them, and a
 * mistake in either fails silently: the install prompt simply never appears, or an old
 * worker keeps serving a stale build. Only a real browser can tell us they took.
 */
import { test, expect } from "@playwright/test";
import type { Server } from "node:http";

import { startMockApi } from "./mock-api";

// PWA support (0.11.0): the manifest is served with the right shape/content
// type, and the service worker script registers and installs successfully.
// `registerPwa()` (src/lib/pwa.ts) only auto-registers in a production build
// (`import.meta.env.PROD`), which this dev-server-backed suite isn't running
// under — so this test drives `navigator.serviceWorker.register()` directly,
// exercising the same script the app ships.

const apiPort = Number(process.env.PLAYWRIGHT_API_PORT ?? 4210);

let api: Server;

test.beforeAll(async () => {
  api = await startMockApi(apiPort);
});

test.afterAll(async () => {
  await new Promise<void>((resolve, reject) => {
    api.close((error) => (error ? reject(error) : resolve()));
  });
});

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("printstash.token", "test-token");
    localStorage.setItem(
      "printstash.user",
      JSON.stringify({ id: 1, username: "tester", email: null, is_superuser: true }),
    );
  });
});

test.describe("progressive web app", () => {
  test("the web app manifest is served with the expected shape", async ({ page }) => {
    const response = await page.request.get("/manifest.webmanifest");
    expect(response.ok()).toBe(true);
    expect(response.headers()["content-type"]).toMatch(/json|manifest/);

    const manifest = await response.json();
    expect(manifest.name).toBe("PrintStash");
    expect(manifest.short_name).toBe("PrintStash");
    expect(manifest.display).toBe("standalone");
    expect(manifest.start_url).toBe("/");
    expect(Array.isArray(manifest.icons)).toBe(true);
    expect(manifest.icons.length).toBeGreaterThan(0);

    // The document links to it, so a browser's install prompt can find it.
    await page.goto("/");
    await expect(page.locator('link[rel="manifest"]')).toHaveAttribute(
      "href",
      "/manifest.webmanifest",
    );
  });

  test("the service worker script registers and installs", async ({ page }) => {
    await page.goto("/");

    const registered = await page.evaluate(async () => {
      if (!("serviceWorker" in navigator)) return false;
      const registration = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
      await new Promise<void>((resolve) => {
        const worker = registration.installing ?? registration.waiting ?? registration.active;
        if (!worker || worker.state === "activated") {
          resolve();
          return;
        }
        worker.addEventListener("statechange", () => {
          if (worker.state === "activated" || worker.state === "installed") resolve();
        });
      });
      return true;
    });

    expect(registered).toBe(true);

    const scope = await page.evaluate(async () => {
      const registration = await navigator.serviceWorker.getRegistration("/");
      return registration?.scope ?? null;
    });
    expect(scope).toContain("/");
  });
});
