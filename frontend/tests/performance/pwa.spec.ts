/**
 * A first visit must stay on the page while offline support installs. Reloading
 * after installation repeats mobile startup work and can discard typed input.
 * This runs the production registration path, which is disabled in dev builds.
 */
import { expect, test } from "@playwright/test";
import type { Server } from "node:http";

import { startMockApi } from "../e2e/mock-api";

const apiPort = Number(process.env.PERF_API_PORT ?? 4220);
let api: Server;

test.beforeAll(async () => {
  api = await startMockApi(apiPort);
});

test.afterAll(async () => {
  await new Promise<void>((resolve, reject) => {
    api.close((error) => (error ? reject(error) : resolve()));
  });
});

test.describe("production PWA startup", () => {
  test("opens the production app without an installation reload", async ({ page }) => {
    const documents: string[] = [];
    page.on("request", (request) => {
      if (request.isNavigationRequest() && request.frame() === page.mainFrame()) {
        documents.push(request.url());
      }
    });

    await page.goto("/login");
    await page.waitForFunction(() => navigator.serviceWorker.controller !== null);
    await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();

    expect(documents).toEqual([new URL("/login", page.url()).href]);
  });
});
