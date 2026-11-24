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
    await page.goto("/search");
    const input = page.getByRole("searchbox", { name: "Search library" });
    await input.fill("crimpA");
    const field = await input.boundingBox();
    expect(field).not.toBeNull();
    for (const name of ["AI Search", "Clear search"]) {
      const action = page.getByRole("button", { name });
      await expect(action).toBeVisible();
      const bounds = await action.boundingBox();
      expect(bounds).not.toBeNull();
      expect(bounds!.y).toBeGreaterThan(field!.y);
      expect(bounds!.y + bounds!.height).toBeLessThan(field!.y + field!.height);
    }
  });

  test("scrolls search results without moving the global header or filters", async ({ page }) => {
    const items = Array.from({ length: 42 }, (_, index) =>
      aSearchResult({
        subject_id: index + 1,
        name: `Model ${index + 1}`,
        href: `/models/${index + 1}`,
      }),
    );
    await page.route("**/api/v1/search/status", (route) =>
      route.fulfill({ json: searchStatus({ enabled: true, semantic_ready: true }) }),
    );
    await page.route("**/api/v1/search?*", (route) =>
      route.fulfill({ json: searchResponse({ items, outcome: "results" }) }),
    );
    await page.goto("/search?q=part");
    const toggle = page.getByRole("button", { name: "Toggle sidebar" });
    if ((await toggle.getAttribute("aria-expanded")) === "true") await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    const filters = page.getByRole("button", { name: "Add filter" });
    const results = page.getByRole("list", { name: "Search results" });
    const lastResult = results.getByRole("listitem").last();
    await expect(results.getByRole("listitem")).toHaveCount(items.length);

    const filtersTop = (await filters.boundingBox())?.y;
    await expect(lastResult).not.toBeInViewport();
    await lastResult.scrollIntoViewIfNeeded();

    await expect(lastResult).toBeInViewport();
    expect((await filters.boundingBox())?.y).toBe(filtersTop);
    expect((await page.getByRole("banner").boundingBox())?.y).toBe(0);
    expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBe(1080);
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
    const ai = page.getByRole("button", { name: "AI Search", exact: true });
    await expect(ai).toHaveText("");
    await expect(ai).toHaveAttribute("aria-pressed", "true");
    const results = page.getByRole("list", { name: "Search results" });
    await expect(results.getByRole("listitem")).toHaveCount(30);
    const desktop = await results.boundingBox();
    const rail = await page.getByRole("complementary", { name: "Search panel" }).boundingBox();
    expect(desktop!.x - (rail!.x + rail!.width)).toBeLessThan(40);
    expect(desktop!.width).toBeGreaterThan(1920 - rail!.x - rail!.width - 80);
    const cards = results.getByRole("listitem");
    expect((await cards.nth(2).boundingBox())?.y).toBe((await cards.first().boundingBox())?.y);
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

  for (const viewport of [
    { width: 1920, height: 1080 },
    { width: 390, height: 844 },
  ]) {
    test(`keeps Model detail navigation in one row at ${viewport.width}px`, async ({
      page,
    }, testInfo) => {
      await page.addInitScript(() =>
        localStorage.setItem("ps-model-detail-inspector-preferred-width", "400"),
      );
      await page.setViewportSize(viewport);
      await page.goto("/models/1");
      const inspector = page.getByTestId("model-detail-sidebar");
      const row = inspector.locator(".model-detail-tabs");
      const overview = row.getByRole("tab", { name: "Overview", exact: true });
      const more = row.getByRole("button", { name: "More tabs", exact: true });
      await expect(more).toBeInViewport();
      const [first, last] = await Promise.all([overview.boundingBox(), more.boundingBox()]);
      expect(Math.abs(first!.y - last!.y)).toBeLessThan(2);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
      await more.press("Enter");
      await page.getByRole("menuitemradio", { name: "Similar", exact: true }).click();
      await expect(inspector.getByRole("tabpanel", { name: "Similar", exact: true })).toBeVisible();
      await page.screenshot({
        path: testInfo.outputPath(`model-tabs-narrow-${viewport.width}.png`),
      });
    });
  }

  test("keeps similar Model names readable in a narrow desktop panel", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.addInitScript(() =>
      localStorage.setItem("ps-model-detail-inspector-preferred-width", "400"),
    );
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
    await page.getByRole("button", { name: "More tabs", exact: true }).click();
    await page.getByRole("menuitemradio", { name: "Similar", exact: true }).click();
    const name = page.getByText("m3Sorter_04_mini ↔ m3Sorter_top", { exact: true });
    await expect(name).toBeVisible();
    expect(await name.evaluate((el) => el.getBoundingClientRect().width)).toBeGreaterThan(220);
    await expect(page.getByRole("link", { name: "Compare", exact: true })).toBeVisible();
    await page.getByRole("link", { name: "Compare", exact: true }).scrollIntoViewIfNeeded();
    await expect(page.getByRole("link", { name: "Compare", exact: true })).toBeInViewport();
    await page.screenshot({ path: "/tmp/printstash-similar-desktop.png" });
  });

  test("keeps library organization beside Filters", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("button", { name: "Upload", exact: true })).toBeVisible();
    const actions = page.getByRole("main").getByRole("group", { name: "Library tools" });

    await expect(
      page.getByRole("main").getByRole("button", { name: "New collection", exact: true }),
    ).toBeVisible();
    await expect(actions.getByRole("button", { name: "Select", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Library tools", exact: true })).toHaveCount(0);
    await page.getByRole("main").getByRole("button", { name: "More", exact: true }).click();
    await expect(
      page.getByRole("menuitem", { name: "New multipart set", exact: true }),
    ).toBeVisible();
  });

  test("opens Similar models from More", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto("/");
    await page.getByRole("main").getByRole("button", { name: "More", exact: true }).click();
    await page.getByRole("menuitem", { name: "Similar models", exact: true }).click();

    await expect(page).toHaveURL(/\/library\/similar$/);
    await expect(page.getByRole("heading", { name: "Similar models", level: 1 })).toBeVisible();
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
      async function openSearch() {
        if (viewport.width < 768) {
          await page.getByRole("button", { name: "Search", exact: true }).click();
        } else {
          await page
            .getByRole("navigation", { name: "Navigation" })
            .getByRole("link", { name: "Search", exact: true })
            .click();
        }
      }
      await openSearch();
      const input = page.getByRole("searchbox", { name: "Search library" });
      const ai = page.getByRole("button", { name: "AI Search", exact: true });
      await expect(ai).toHaveAttribute("aria-pressed", "false");
      await input.fill("bracket");
      await input.press("Enter");
      const localQuery = (url: URL) =>
        url.pathname === "/" &&
        url.searchParams.get("tag") === "functional" &&
        url.searchParams.get("favorites") === "true" &&
        url.searchParams.get("q") === "bracket";
      await expect(page).toHaveURL(localQuery);
      const libraryUrl = page.url();
      expect(await input.evaluate((el) => el.getBoundingClientRect().width)).toBeGreaterThan(120);
      await page.screenshot({ path: testInfo.outputPath("library.png"), fullPage: true });
      await ai.click();
      await expect(page).toHaveURL(
        (url) => url.pathname === "/search" && url.searchParams.get("q") === "bracket",
      );
      await expect(page.getByRole("link", { name: "Desk bracket", exact: true })).toBeVisible();
      await expect(ai).toHaveAttribute("aria-pressed", "true");
      await ai.click();
      await expect(page).toHaveURL(
        (url) => url.pathname === "/search" && url.searchParams.get("mode") === "lexical",
      );
      await expect(ai).toHaveAttribute("aria-pressed", "false");
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
      await page.screenshot({ path: testInfo.outputPath("search.png"), fullPage: true });

      // Returning to the bookmarked local view retains its scope and draft.
      await page.goto(libraryUrl);
      await expect(page).toHaveURL(localQuery);
      await openSearch();
      await expect(input).toHaveValue("bracket");
      await expect(page.getByTitle("Remove Tag: functional")).toBeVisible();
      await input.fill("");
      await input.press("Enter");
      await expect(page).toHaveURL(
        (url) =>
          url.pathname === "/" &&
          url.searchParams.get("tag") === "functional" &&
          url.searchParams.get("favorites") === "true" &&
          !url.searchParams.has("q"),
      );
      await expect(page.getByTitle("Remove Tag: functional")).toBeVisible();
    });
  });
}
