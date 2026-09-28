/** Search and narrow detail panels keep their actions predictable and readable. */
import { expect, test } from "@playwright/test";
import { aSimilarityCandidate, similarityStatus } from "../../src/test-support/similarity";
import { aSearchResult, searchResponse, searchStatus } from "../../src/test-support/search";
import { useMockApi } from "./_setup";

useMockApi();

test.describe("search clarity", () => {
  test("keeps search actions inside the focused field border", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.route("**/api/v1/search/status", (route) =>
      route.fulfill({
        json: searchStatus({
          enabled: true,
          semantic_ready: true,
          legs: ["lexical", "semantic_text", "thumbnail"],
        }),
      }),
    );
    await page.goto("/");
    const input = page.getByRole("searchbox", { name: "Search library" });
    await input.fill("crimpA");
    const field = await input.boundingBox();
    expect(field).not.toBeNull();
    for (const name of ["Search by image", "Clear search"]) {
      const action = page.getByRole("button", { name });
      await expect(action).toBeVisible();
      const bounds = await action.boundingBox();
      expect(bounds).not.toBeNull();
      expect(bounds!.y).toBeGreaterThan(field!.y);
      expect(bounds!.y + bounds!.height).toBeLessThan(field!.y + field!.height);
    }
  });

  test("keeps dense results in the full browsing surface", async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.addInitScript(() => localStorage.setItem("printstash.theme", "dark"));
    const items = Array.from({ length: 30 }, (_, index) => {
      const subjectType =
        index === 1
          ? "collection"
          : index === 2
            ? "multipart_model"
            : index === 3
              ? "document"
              : "model";
      return aSearchResult({
        subject_type: subjectType,
        subject_id: index + 1,
        name:
          index === 0
            ? "drawer-divider-extra-long-model-name-that-must-wrap-without-clipping"
            : `${subjectType} result ${index + 1}`,
        href:
          subjectType === "collection"
            ? "/?c=Search"
            : subjectType === "multipart_model"
              ? `/multipart-models/${index + 1}`
              : subjectType === "document"
                ? `/documents/${index + 1}`
                : `/models/${index + 1}`,
      });
    });
    await page.route("**/api/v1/search/status", (route) =>
      route.fulfill({ json: searchStatus({ enabled: true, semantic_ready: true }) }),
    );
    await page.route("**/api/v1/search?*", (route) =>
      route.fulfill({ json: searchResponse({ items, outcome: "results" }) }),
    );
    await page.goto("/search?q=divider");
    await expect(page.getByText("30 results shown")).toBeVisible();
    await expect(page.getByRole("group", { name: "Result types" })).toBeVisible();
    const ai = page.getByRole("button", { name: "Turn off AI search" });
    await expect(ai).toHaveText("");
    await expect(ai).toHaveAttribute("aria-pressed", "true");
    const results = page.getByRole("list", { name: "Search results" });
    await expect(results.getByRole("listitem")).toHaveCount(30);
    const desktop = await results.boundingBox();
    expect(desktop?.x).toBeLessThan(40);
    expect(desktop?.width).toBeGreaterThan(1800);
    const cards = results.getByRole("listitem");
    expect((await cards.nth(5).boundingBox())?.y).toBe((await cards.first().boundingBox())?.y);
    await cards.last().scrollIntoViewIfNeeded();
    await expect(cards.last()).toBeInViewport();
    await cards.first().scrollIntoViewIfNeeded();
    await page.screenshot({
      path: testInfo.outputPath("search-dense-desktop-dark.png"),
      fullPage: true,
    });

    await page.setViewportSize({ width: 390, height: 844 });
    expect((await cards.nth(1).boundingBox())?.y).toBeGreaterThan(
      (await cards.first().boundingBox())?.y ?? 0,
    );
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await page.screenshot({ path: testInfo.outputPath("search-dense-mobile-dark.png") });

    await page.getByRole("button", { name: "List view" }).click();
    await expect(page.getByRole("button", { name: "List view" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect(results.getByRole("listitem")).toHaveCount(30);
    await page.screenshot({ path: testInfo.outputPath("search-dense-mobile-list-dark.png") });
  });

  test("keeps Model detail navigation on one row responsively", async ({ page }, testInfo) => {
    await page.addInitScript(() => localStorage.setItem("ps-model-detail-sidebar-width", "400"));
    for (const viewport of [
      { width: 1920, height: 1080 },
      { width: 390, height: 844 },
    ]) {
      await page.setViewportSize(viewport);
      await page.goto("/models/1");
      const tabs = page.getByTestId("model-detail-sidebar").getByRole("tablist");
      const overview = tabs.getByRole("tab", { name: "Overview", exact: true });
      const similar = tabs.getByRole("tab", { name: "Similar", exact: true });
      await expect(similar).toBeVisible();
      const tabWidths = await tabs.evaluate((el) => ({
        scroll: el.scrollWidth,
        client: el.clientWidth,
      }));
      expect(tabWidths.scroll).toBeLessThanOrEqual(tabWidths.client);
      const positions = await Promise.all([
        overview.evaluate((el) => el.getBoundingClientRect().top),
        similar.evaluate((el) => el.getBoundingClientRect().top),
      ]);
      expect(Math.abs(positions[0] - positions[1])).toBeLessThan(2);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
      await overview.focus();
      await page.keyboard.press("ArrowLeft");
      await expect(similar).toBeFocused();
      await similar.click();
      await expect(similar).toHaveAttribute("aria-selected", "true");
      await page.screenshot({ path: testInfo.outputPath(`model-tabs-${viewport.width}.png`) });
    }
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
    const libraryTools = page.getByRole("button", { name: "Library tools", exact: true });
    await expect(libraryTools).toHaveAttribute("aria-expanded", "false");
    await libraryTools.click();
    await expect(page.getByRole("region", { name: "Library tools" })).toBeVisible();
    await expect(
      page.getByRole("button", { name: "New multipart set", exact: true }),
    ).toBeVisible();
  });
});

for (const viewport of [
  { width: 390, height: 844 },
  { width: 1920, height: 1080 },
]) {
  test.describe(`library search at ${viewport.width}px`, () => {
    test.use({ viewport });
    test("keeps live filtering separate from AI results", async ({ page }, testInfo) => {
      await page.route("**/api/v1/search/status", (route) =>
        route.fulfill({
          json: searchStatus({
            enabled: true,
            semantic_ready: true,
            legs: ["lexical", "semantic_text", "thumbnail"],
          }),
        }),
      );
      await page.route("**/api/v1/search?*", (route) =>
        route.fulfill({ json: searchResponse({ items: [aSearchResult()] }) }),
      );
      await page.goto("/?tag=functional&favorites=true");
      await expect(page.getByRole("button", { name: "Upload", exact: true })).toBeVisible();
      const input = page.getByRole("searchbox", { name: "Search library" });
      await expect(page.getByRole("button", { name: "Enter a search to use AI" })).toBeDisabled();
      await expect(page.getByRole("button", { name: "Search by image" })).toBeVisible();
      await input.fill("bracket");
      await input.press("Enter");
      await expect(page).toHaveURL(/\/\?tag=functional&favorites=true&q=bracket$/);
      expect(await input.evaluate((el) => el.getBoundingClientRect().width)).toBeGreaterThan(120);
      expect(
        await input.evaluate((el) => {
          const style = getComputedStyle(el);
          return el.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
        }),
      ).toBeGreaterThan(50);
      if (viewport.width < 640) {
        await expect(page.getByRole("button", { name: "Search by image" })).toBeHidden();
      } else {
        await expect(page.getByRole("button", { name: "Search by image" })).toBeVisible();
      }
      await expect(page.getByRole("button", { name: "Search with AI" })).toHaveAttribute(
        "aria-pressed",
        "false",
      );
      await page.screenshot({ path: testInfo.outputPath("library.png"), fullPage: true });
      await page.getByRole("button", { name: "Search with AI", exact: true }).click();
      // The results route consumes the one-shot parse flag; assert its settled URL.
      await expect(page).toHaveURL(/\/search\?q=bracket$/);
      await expect(page.getByRole("link", { name: "Desk bracket", exact: true })).toBeVisible();
      const activeAi = page.getByRole("button", { name: "Turn off AI search" });
      await expect(activeAi).toHaveAttribute("aria-pressed", "true");
      await expect(page.getByText("Why this result")).toHaveCount(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
      await page.screenshot({ path: testInfo.outputPath("search.png"), fullPage: true });
      await activeAi.click();
      await expect(page).toHaveURL(/\/search\?q=bracket&mode=lexical$/);
      await expect(page.getByRole("button", { name: "Search with AI" })).toHaveAttribute(
        "aria-pressed",
        "false",
      );
      await page.goBack();
      await page.goBack();
      await expect(page).toHaveURL(/\/\?tag=functional&favorites=true&q=bracket$/);
      await expect(page.getByRole("button", { name: "Upload", exact: true })).toBeVisible();
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
