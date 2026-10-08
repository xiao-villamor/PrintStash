/** Verifies production worker delivery and private-cache isolation through real browser requests. */
import { expect, test, type Page } from "@playwright/test";
import type { Server } from "node:http";

import { resetMockApiState, startMockApi } from "./mock-api";

const apiPort = Number(process.env.PLAYWRIGHT_API_PORT ?? 4210);
let api: Server;

// First installation claims the page without discarding the user's current view.
async function openControlledApplication(page: Page): Promise<void> {
  await page.goto("/");
  await page.waitForFunction(() => Boolean(navigator.serviceWorker.controller));
  await page.waitForLoadState("load");
}

async function cachedPaths(page: Page): Promise<string[]> {
  return page.evaluate(async () => {
    const names = await caches.keys();
    const requests = await Promise.all(names.map(async (name) => (await caches.open(name)).keys()));
    return requests.flat().map((request) => new URL(request.url).pathname);
  });
}

async function expectNoPrivateCacheEntries(page: Page): Promise<void> {
  const paths = await cachedPaths(page);
  // A positive shell entry prevents an empty/uninstalled cache from passing.
  expect(paths).toContain("/theme-bootstrap.js");
  expect(paths.filter((path) => path.startsWith("/api/"))).toEqual([]);
}

test.beforeAll(async () => {
  api = await startMockApi(apiPort);
});

test.afterAll(async () => {
  await new Promise<void>((resolve, reject) => {
    api.close((error) => (error ? reject(error) : resolve()));
  });
});

test.beforeEach(async ({ page, context, request, baseURL }) => {
  resetMockApiState();
  if (!baseURL) throw new Error("The production PWA suite requires a base URL");
  const response = await request.get(`http://127.0.0.1:${apiPort}/api/v1/auth/me`);
  expect(response.ok()).toBe(true);
  const storedUser = await response.text();
  await context.addCookies([
    { name: "printstash_session", value: "pwa-test-session", url: baseURL, sameSite: "Strict" },
  ]);
  await page.addInitScript((user) => {
    localStorage.setItem("printstash.user", user);
    localStorage.setItem("printstash.locale", "en");
  }, storedUser);
});

test.describe("production PWA cache contracts", () => {
  test("automatically controls the production application", async ({ page, baseURL }) => {
    const documents: string[] = [];
    page.on("request", (request) => {
      if (request.isNavigationRequest() && request.frame() === page.mainFrame())
        documents.push(request.url());
    });
    await openControlledApplication(page);
    expect(documents).toHaveLength(1);
    expect(
      await page.evaluate(() =>
        performance
          .getEntriesByType("navigation")
          .map((entry) => (entry instanceof PerformanceNavigationTiming ? entry.type : null)),
      ),
    ).toEqual(["navigate"]);

    await expect(page.locator('script[type="module"][src^="/assets/"]')).toHaveAttribute(
      "src",
      /\/assets\/index-[^/]+\.js$/,
    );
    const worker = await page.evaluate(async () => {
      const registration = await navigator.serviceWorker.getRegistration("/");
      return {
        scriptURL: navigator.serviceWorker.controller?.scriptURL,
        state: registration?.active?.state,
        scope: registration?.scope,
      };
    });
    expect(worker).toEqual({
      scriptURL: new URL("/sw.js", baseURL).href,
      state: "activated",
      scope: new URL("/", baseURL).href,
    });
    await expectNoPrivateCacheEntries(page);
  });

  test("reloads once when replacing an installed worker", async ({ page }) => {
    await page.goto("/offline.html");
    await page.evaluate(async () => {
      await navigator.serviceWorker.register("/api/v1/pwa-old-worker.js", { scope: "/" });
      await navigator.serviceWorker.ready;
    });
    await page.waitForFunction(() =>
      navigator.serviceWorker.controller?.scriptURL.endsWith("/pwa-old-worker.js"),
    );
    let documents = 0;
    page.on("request", (request) => {
      if (request.isNavigationRequest() && request.frame() === page.mainFrame()) documents++;
    });
    await page.goto("/");
    await page.waitForFunction(
      () =>
        navigator.serviceWorker.controller?.scriptURL.endsWith("/sw.js") &&
        performance
          .getEntriesByType("navigation")
          .some((entry) => entry instanceof PerformanceNavigationTiming && entry.type === "reload"),
    );
    await expect(page.getByRole("heading", { name: "All Models", exact: true })).toBeVisible();
    expect(documents).toBe(2);
  });

  test("excludes private JSON responses from worker caches", async ({ page, baseURL }) => {
    await openControlledApplication(page);
    const response = await page.evaluate(async () => {
      const result = await fetch("/api/v1/auth/me", { cache: "no-store", credentials: "include" });
      return {
        status: result.status,
        url: result.url,
        contentType: result.headers.get("content-type"),
        body: await result.text(),
      };
    });
    expect(response.status).toBe(200);
    expect(response.url).toBe(new URL("/api/v1/auth/me", baseURL).href);
    expect(response.contentType).toContain("application/json");
    expect(response.body).toContain('"username":"tester"');
    await expectNoPrivateCacheEntries(page);
  });

  test("excludes private image bytes from worker caches", async ({ page, baseURL }) => {
    await openControlledApplication(page);
    const response = await page.evaluate(async () => {
      const result = await fetch("/api/v1/files/1/thumbnail", {
        cache: "no-store",
        credentials: "include",
      });
      const bytes = new Uint8Array(await result.arrayBuffer());
      return {
        status: result.status,
        url: result.url,
        contentType: result.headers.get("content-type"),
        signature: Array.from(bytes.slice(0, 8)),
      };
    });
    expect(response.status).toBe(200);
    expect(response.url).toBe(new URL("/api/v1/files/1/thumbnail", baseURL).href);
    expect(response.contentType).toBe("image/png");
    expect(response.signature).toEqual([137, 80, 78, 71, 13, 10, 26, 10]);
    await expectNoPrivateCacheEntries(page);
  });

  for (const path of ["/api/v1/auth/me", "/api/v1/files/1/thumbnail"]) {
    test(`refuses offline replay of private responses: ${path}`, async ({ page, context }) => {
      await openControlledApplication(page);
      const status = await page.evaluate(async (url) => {
        const response = await fetch(url, { cache: "no-store", credentials: "include" });
        await response.arrayBuffer();
        return response.status;
      }, path);
      expect(status).toBe(200);
      await expectNoPrivateCacheEntries(page);

      await context.setOffline(true);
      try {
        const rejected = await page.evaluate(async (url) => {
          try {
            const response = await fetch(url, { cache: "no-store", credentials: "include" });
            await response.arrayBuffer();
            return false;
          } catch {
            return true;
          }
        }, path);
        expect(rejected).toBe(true);
        await expectNoPrivateCacheEntries(page);
      } finally {
        await context.setOffline(false);
      }
    });
  }

  test("delivers bootstrap when cache storage rejects", async ({ page, context }) => {
    await openControlledApplication(page);
    const worker = context.serviceWorkers()[0];
    await worker.evaluate(() => {
      caches.open = async () => {
        throw new DOMException("Storage unavailable", "SecurityError");
      };
    });

    const response = await page.goto("/settings");

    expect(response?.status()).toBe(200);
    await expect(page.getByRole("heading", { name: "Settings", exact: true })).toBeVisible();
  });

  test("delivers the production bootstrap offline", async ({ page, context }) => {
    await openControlledApplication(page);
    await expectNoPrivateCacheEntries(page);
    await context.setOffline(true);
    try {
      const response = await page.evaluate(async () => {
        const result = await fetch("/theme-bootstrap.js", { cache: "no-store" });
        return { status: result.status, body: await result.text() };
      });
      expect(response.status).toBe(200);
      expect(response.body).toContain('document.documentElement.classList.toggle("dark"');
      expect(response.body).toContain('localStorage.getItem("printstash.theme")');
    } finally {
      await context.setOffline(false);
    }
  });
});

test.describe("production route recovery", () => {
  test.use({ serviceWorkers: "block" });

  test("recovers a persistently missing route through explicit reload", async ({
    page,
    context,
  }) => {
    let documents = 0;
    page.on("request", (request) => {
      if (request.isNavigationRequest() && request.frame() === page.mainFrame()) documents += 1;
    });
    await context.route(/\/assets\/settings-[^/]+\.js$/, (route) =>
      route.fulfill({ status: 404, body: "Missing deployed chunk" }),
    );

    await page.goto("/settings");

    await expect(page.getByRole("heading", { name: "Page unavailable" })).toBeVisible();
    expect(documents).toBe(2);
    await expect(page.getByRole("button", { name: "Reload page" })).toBeVisible();
    await context.unroute(/\/assets\/settings-[^/]+\.js$/);
    await page.getByRole("button", { name: "Reload page" }).click();
    await expect(page.getByRole("heading", { name: "Settings", exact: true })).toBeVisible();
    expect(documents).toBe(3);
  });
});
