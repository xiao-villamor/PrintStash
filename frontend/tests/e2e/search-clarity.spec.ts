/** Search and narrow detail panels keep their actions predictable and readable. */
import { expect, test } from "@playwright/test";
import { aSimilarityCandidate, similarityStatus } from "../../src/test-support/similarity";
import { aSearchResult, searchResponse, searchStatus } from "../../src/test-support/search";
import { useMockApi } from "./_setup";

useMockApi();

test.describe("search clarity", () => {
  test("keeps every detail tab inside a narrow panel", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.addInitScript(() => localStorage.setItem("ps-model-detail-sidebar-width", "400"));
    await page.goto("/models/1");
    const tabs = page.getByTestId("model-detail-sidebar").getByRole("tablist");
    await expect(tabs.getByRole("tab", { name: "Similar", exact: true })).toBeVisible();
    expect(await tabs.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
    await tabs.getByRole("tab", { name: "Overview", exact: true }).focus();
    await page.keyboard.press("ArrowLeft");
    await expect(tabs.getByRole("tab", { name: "Similar", exact: true })).toBeFocused();
  });

  test("keeps similar Model names readable in a narrow desktop panel", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.addInitScript(() => localStorage.setItem("ps-model-detail-sidebar-width", "400"));
    const candidate = aSimilarityCandidate({
      model_a: { id: 1, name: "m3Sorter_04_mini", slug: "sorter-mini", thumbnail_file_id: null },
      model_b: { id: 2, name: "m3Sorter_top", slug: "sorter-top", thumbnail_file_id: null },
    });
    await page.route("**/api/v1/similarity/status", (route) =>
      route.fulfill({ json: similarityStatus() }),
    );
    await page.route("**/api/v1/similarity/candidates?*", (route) =>
      route.fulfill({ json: { items: [candidate], next_cursor: null } }),
    );
    await page.goto("/models/1");
    await page.getByRole("tab", { name: "Similar", exact: true }).click();
    const name = page.getByRole("link", { name: "m3Sorter_04_mini" });
    await expect(name).toBeVisible();
    expect(await name.evaluate((el) => el.getBoundingClientRect().width)).toBeGreaterThan(220);
    await expect(page.getByRole("link", { name: "Compare", exact: true })).toBeVisible();
    await page.getByRole("link", { name: "Compare", exact: true }).scrollIntoViewIfNeeded();
    await expect(page.getByRole("link", { name: "Compare", exact: true })).toBeInViewport();
    await page.screenshot({ path: "/tmp/printstash-similar-desktop.png" });
  });

  test("reveals library organization on demand", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("button", { name: "Upload", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Create Family", exact: true })).toBeHidden();
    await page.getByRole("button", { name: "Library tools", exact: true }).click();
    await expect(page.getByRole("button", { name: "Create Family", exact: true })).toBeVisible();
    await expect(
      page.getByRole("button", { name: "New multipart set", exact: true }),
    ).toBeVisible();
  });
});

for (const viewport of [
  { width: 390, height: 844 },
  { width: 1280, height: 900 },
]) {
  test.describe(`library search at ${viewport.width}px`, () => {
    test.use({ viewport });
    test("keeps live filtering separate from AI results", async ({ page }, testInfo) => {
      await page.route("**/api/v1/search/status", (route) =>
        route.fulfill({ json: searchStatus({ enabled: true, semantic_ready: true }) }),
      );
      await page.route("**/api/v1/search?*", (route) =>
        route.fulfill({ json: searchResponse({ items: [aSearchResult()] }) }),
      );
      await page.goto("/?tag=functional&favorites=true");
      const input = page.getByRole("searchbox", { name: "Search library" });
      await input.fill("bracket");
      await input.press("Enter");
      await expect(page).toHaveURL(/\/\?tag=functional&favorites=true&q=bracket$/);
      await expect(page.getByRole("button", { name: "Create Family", exact: true })).toBeHidden();
      expect(await input.evaluate((el) => el.getBoundingClientRect().width)).toBeGreaterThan(120);
      await page.screenshot({ path: testInfo.outputPath("library.png"), fullPage: true });
      await input.click();
      await page.getByRole("button", { name: "Search with AI", exact: true }).click();
      // The results route consumes the one-shot parse flag; assert its settled URL.
      await expect(page).toHaveURL(/\/search\?q=bracket$/);
      await expect(page.getByRole("link", { name: "Desk bracket", exact: true })).toBeVisible();
      await expect(page.getByText("Why this result")).toHaveCount(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
      await page.screenshot({ path: testInfo.outputPath("search.png"), fullPage: true });
      await page.goBack();
      await expect(page).toHaveURL(/\/\?tag=functional&favorites=true&q=bracket$/);
      await expect(input).toHaveValue("bracket");
      await expect(page.getByTitle("Remove Tag: functional")).toBeVisible();
      await page.getByRole("button", { name: "Clear search", exact: true }).click();
      await expect(page).toHaveURL(/\/\?tag=functional&favorites=true$/);
      await expect(page.getByTitle("Remove Tag: functional")).toBeVisible();
      await page.getByRole("button", { name: "Toggle theme" }).click();
      await page.screenshot({
        path: testInfo.outputPath("library-alternate-theme.png"),
        fullPage: true,
      });
    });
  });
}
