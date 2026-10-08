/** Real browser navigation evidence, independent of the Library readiness observer. */
import { expect, test } from "@playwright/test";
import { loadLibraryDocument } from "../../scripts/library-performance/navigation.mjs";

test("measures a browser reload after the initial visit", async ({ page, baseURL }) => {
  if (!baseURL) throw new Error("A browser server is required");
  const url = new URL("/reload-measurement", baseURL).href;
  await page.route(url, (route) =>
    route.fulfill({
      contentType: "text/html",
      body: "<!doctype html><title>Measurement target</title><main>Ready</main>",
    }),
  );
  const navigationType = () =>
    page.evaluate(() => {
      const entry = performance.getEntriesByType("navigation")[0];
      if (!(entry instanceof PerformanceNavigationTiming)) throw new Error("Missing navigation");
      return entry.type;
    });
  await loadLibraryDocument(page, url);
  expect(await navigationType()).toBe("navigate");
  await page.evaluate(() => {
    sessionStorage.setItem("printstash:performance-navigation-start", "old");
    sessionStorage.setItem("remembered-library-view", "keep");
  });
  await loadLibraryDocument(page, url);
  expect(await navigationType()).toBe("reload");
  expect(page.url()).toBe(url);
  expect(
    await page.evaluate(() => ({
      previousTiming: sessionStorage.getItem("printstash:performance-navigation-start"),
      rememberedView: sessionStorage.getItem("remembered-library-view"),
    })),
  ).toEqual({ previousTiming: null, rememberedView: "keep" });
});
