/*
 * Real timings for the two interactions a user feels, recorded rather than
 * asserted.
 *
 * Vault load and first interaction are where this app is slow if it is slow at
 * all — a large library, thumbnails, a filter tree. This spec exists to produce
 * numbers a human compares between releases, not to fail a build: a hard
 * threshold on a laptop under CI load is a flake, and a flaky performance gate
 * gets disabled and then nobody has numbers at all.
 *
 * It runs against a production build, because the dev server's timings say
 * nothing about what a self-hoster experiences.
 */

import { expect, test, type Page } from "@playwright/test";
import type { Server } from "node:http";

import { resetMockApiState, startMockApi } from "../e2e/mock-api";

const apiPort = Number(process.env.PERF_API_PORT ?? 4220);

interface BrowserMetrics {
  cumulativeLayoutShift: number;
  decodedBodyBytes: number;
  domContentLoadedMs: number;
  firstContentfulPaintMs: number | null;
  largestContentfulPaintMs: number | null;
  loadMs: number;
  requests: number;
  transferredBytes: number;
}

interface PrintStashPerformanceWindow extends Window {
  __printstashPerformance?: {
    cumulativeLayoutShift: number;
    largestContentfulPaintMs: number | null;
  };
}

let api: Server;

test.beforeAll(async () => {
  api = await startMockApi(apiPort);
});

test.afterAll(async () => {
  await new Promise<void>((resolve, reject) => {
    api.close((error) => (error ? reject(error) : resolve()));
  });
});

async function installPerformanceObservers(page: Page): Promise<void> {
  await page.addInitScript(() => {
    // SAFETY: this test owns the optional instrumentation property it adds to window.
    const perfWindow = window as PrintStashPerformanceWindow;
    perfWindow.__printstashPerformance = {
      cumulativeLayoutShift: 0,
      largestContentfulPaintMs: null,
    };

    new PerformanceObserver((list) => {
      const latest = list.getEntries().at(-1);
      if (latest && perfWindow.__printstashPerformance) {
        perfWindow.__printstashPerformance.largestContentfulPaintMs = latest.startTime;
      }
    }).observe({ type: "largest-contentful-paint", buffered: true });

    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        // SAFETY: layout-shift observer entries expose the LayoutShift fields below.
        const shift = entry as PerformanceEntry & { hadRecentInput: boolean; value: number };
        if (!shift.hadRecentInput && perfWindow.__printstashPerformance) {
          perfWindow.__printstashPerformance.cumulativeLayoutShift += shift.value;
        }
      }
    }).observe({ type: "layout-shift", buffered: true });
  });
}

async function collectBrowserMetrics(page: Page): Promise<BrowserMetrics> {
  return page.evaluate(() => {
    // SAFETY: installPerformanceObservers initializes this optional window property.
    const perfWindow = window as PrintStashPerformanceWindow;
    // SAFETY: the navigation entry type returns PerformanceNavigationTiming entries.
    const navigation = performance.getEntriesByType("navigation")[0] as
      | PerformanceNavigationTiming
      | undefined;
    // SAFETY: the resource entry type returns PerformanceResourceTiming entries.
    const resources = performance.getEntriesByType("resource") as PerformanceResourceTiming[];
    const firstContentfulPaint = performance
      .getEntriesByType("paint")
      .find((entry) => entry.name === "first-contentful-paint");

    return {
      cumulativeLayoutShift: perfWindow.__printstashPerformance?.cumulativeLayoutShift ?? 0,
      decodedBodyBytes: resources.reduce((total, resource) => total + resource.decodedBodySize, 0),
      domContentLoadedMs: navigation?.domContentLoadedEventEnd ?? 0,
      firstContentfulPaintMs: firstContentfulPaint?.startTime ?? null,
      largestContentfulPaintMs:
        perfWindow.__printstashPerformance?.largestContentfulPaintMs ?? null,
      loadMs: navigation?.loadEventEnd ?? 0,
      requests: resources.length,
      transferredBytes: resources.reduce((total, resource) => total + resource.transferSize, 0),
    };
  });
}

test.beforeEach(async ({ page }) => {
  resetMockApiState();
  await installPerformanceObservers(page);
  await page.addInitScript(() => {
    localStorage.setItem("printstash.token", "test-token");
    localStorage.setItem(
      "printstash.user",
      JSON.stringify({ id: 1, username: "tester", email: null, is_superuser: true }),
    );
  });
});

test.describe("vault performance", () => {
  test("records production vault loading and interaction timings", async ({ page }, testInfo) => {
    await page.goto("/");
    await expect(page.getByText("skadis_kitchen-roll_screw").first()).toBeVisible();

    const interactionStarted = performance.now();
    await page.getByRole("button", { name: "Display" }).click();
    await page.getByRole("menuitem", { name: "List View" }).click();
    await expect(page.getByText("Thumb", { exact: true })).toBeVisible();
    const displayChangeMs = performance.now() - interactionStarted;

    await page.waitForTimeout(100);
    const browser = await collectBrowserMetrics(page);
    const metrics = {
      buildMode: process.env.PERF_BUILD_MODE === "react-compiler" ? "react-compiler" : "baseline",
      displayChangeMs,
      ...browser,
    };

    console.log(`PRINTSTASH_PERF ${JSON.stringify(metrics)}`);
    await testInfo.attach("performance-metrics", {
      body: Buffer.from(`${JSON.stringify(metrics, null, 2)}\n`),
      contentType: "application/json",
    });

    expect(browser.requests).toBeGreaterThan(0);
    expect(browser.cumulativeLayoutShift).toBeGreaterThanOrEqual(0);
  });
});
