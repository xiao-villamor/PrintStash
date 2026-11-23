/** Navigation leaves the selected destination and keeps displayed cards still while its next read is pending. */
import { expect, test } from "@playwright/test";
import { aCollectionNode, aModelListItem } from "../../src/test-support/factories";
import { useMockApi } from "./_setup";

useMockApi();
test.use({ viewport: { width: 1440, height: 900 } });

test.describe("Library shell navigation", () => {
  test("Search keeps the browsed collection until widened", async ({ page }) => {
    const collection = aCollectionNode({ id: 4, name: "Shop parts", path: "parts" });
    await page.route("**/api/v1/collections/lookup?**", (route) =>
      route.fulfill({ json: { collection, ancestors: [] } }),
    );
    await page.goto("/?c=parts&cid=4");
    await expect(page.getByRole("heading", { level: 1, name: "Shop parts" })).toBeVisible();
    const rail = page.getByRole("navigation", { name: "Navigation", exact: true });

    await rail.getByRole("link", { name: "Search", exact: true }).click();

    const panel = page.getByRole("complementary", { name: "Search panel" });
    await expect(panel).toBeVisible();
    await expect(page.getByRole("heading", { level: 1, name: "Shop parts" })).toBeVisible();
    expect(new URL(page.url()).searchParams.get("c")).toBe("parts");
    await panel.getByRole("searchbox").fill("bracket");
    await expect.poll(() => new URL(page.url()).searchParams.get("q")).toBe("bracket");
    expect(new URL(page.url()).searchParams.get("c")).toBe("parts");
    await panel.getByRole("button", { name: "Whole library", exact: true }).click();
    await expect(page.getByRole("heading", { level: 1, name: "All Models" })).toBeVisible();
    expect(new URL(page.url()).searchParams.get("q")).toBe("bracket");
    await panel.getByRole("button", { name: "Shop parts", exact: true }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Shop parts" })).toBeVisible();
  });

  test("Settings stays directly reachable after Search", async ({ page }) => {
    await page.goto("/settings");
    await expect(page.getByRole("navigation", { name: "Settings sections" })).toBeVisible();
    const rail = page.getByRole("navigation", { name: "Navigation", exact: true });
    await rail.getByRole("link", { name: "Search", exact: true }).click();
    await expect(page.locator("[data-shell-panel-search]")).toBeVisible();
    expect(new URL(page.url()).pathname).toBe("/");

    await rail.getByRole("link", { name: "Settings", exact: true }).press("Enter");

    await expect(page.getByRole("navigation", { name: "Settings sections" })).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Settings", exact: true, level: 1 }),
    ).toBeVisible();
    await expect(rail.getByRole("link", { name: "Settings", exact: true })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  test("Settings restores its sidebar after opening Collections", async ({ page }) => {
    await page.goto("/settings?section=design");
    await expect(page.getByRole("navigation", { name: "Settings sections" })).toBeVisible();
    const rail = page.getByRole("navigation", { name: "Navigation", exact: true });
    await rail.getByRole("link", { name: "Collections", exact: true }).click();
    await expect(
      page.getByRole("complementary", { name: "Collections", exact: true }),
    ).toBeVisible();

    await rail.getByRole("link", { name: "Settings", exact: true }).click();

    await expect(page.getByRole("navigation", { name: "Settings sections" })).toBeVisible();
    await expect(
      page.getByRole("complementary", { name: "Collections", exact: true }),
    ).toBeHidden();
    await expect(page).toHaveURL(/section=design/);
  });
});

test.describe("Collection loading geometry", () => {
  test.beforeEach(async ({ page, context }) => {
    const cdp = await context.newCDPSession(page);
    await cdp.send("Network.enable");
    await cdp.send("Network.setCacheDisabled", { cacheDisabled: true });
    const collection = aCollectionNode({
      id: 1,
      name: "Leaf",
      path: "maraio",
      child_count: 0,
      has_readme: false,
    });
    await page.route("**/api/v1/collections/lookup?**", (route) =>
      route.fulfill({ json: { collection, ancestors: [] } }),
    );
    await page.route("**/api/v1/models/browse?**", (route) =>
      route.fulfill({
        json: {
          items: [
            {
              kind: "model",
              model: aModelListItem({ id: 1, name: "Visible model", thumbnail_url: null }),
            },
          ],
          total: 1,
          next_cursor: null,
          browse_revision: "r1",
          authorization_revision: "a1",
        },
      }),
    );
  });

  test("pending navigation preserves the displayed leaf geometry", async ({ page }) => {
    await page.goto("/?c=maraio");
    const model = page.getByRole("heading", { name: "Visible model", exact: true });
    await expect(model).toBeVisible();
    const original = await model.boundingBox();
    const requested = Promise.withResolvers<void>();
    const release = Promise.withResolvers<void>();
    await page.route("**/api/v1/models/browse?**", async (route) => {
      requested.resolve();
      await release.promise;
      await route.fallback();
    });

    await page
      .locator("header")
      .getByRole("button", { name: "All Models", exact: true })
      .first()
      .press("Enter");
    await requested.promise;

    try {
      await expect(page.locator(".library-subcollections")).toHaveCount(0);
      expect(await model.boundingBox()).toEqual(original);
    } finally {
      release.resolve();
    }
    await expect(page.getByRole("heading", { name: "All Models", exact: true })).toBeVisible();
    await expect(page.getByRole("region", { name: "Collections", exact: true })).toBeVisible();
  });

  test("cold leaf load never reserves a collection strip", async ({ page }) => {
    const requested = Promise.withResolvers<void>();
    const release = Promise.withResolvers<void>();
    await page.route("**/api/v1/models/browse?**", async (route) => {
      requested.resolve();
      await release.promise;
      await route.fallback();
    });

    await page.goto("/?c=maraio");
    await requested.promise;

    try {
      await expect(page.locator(".library-loading-placeholder").first()).toBeVisible();
      await expect(page.locator(".library-subcollections")).toHaveCount(0);
    } finally {
      release.resolve();
    }
    await expect(page.getByRole("heading", { name: "Visible model", exact: true })).toBeVisible();
    await expect(page.locator(".library-subcollections")).toHaveCount(0);
  });

  test("cold parent reserves its own collection strip", async ({ page }) => {
    const collection = aCollectionNode({
      id: 1,
      name: "Parent",
      path: "maraio",
      child_count: 1,
      has_readme: false,
    });
    const child = aCollectionNode({
      id: 2,
      name: "Child",
      path: "maraio/child",
      parent_id: 1,
      child_count: 0,
    });
    await page.route("**/api/v1/collections/lookup?**", (route) =>
      route.fulfill({ json: { collection, ancestors: [] } }),
    );
    await page.route("**/api/v1/collections/children?**", (route) =>
      route.fulfill({ json: { items: [child], next_cursor: null } }),
    );
    const requested = Promise.withResolvers<void>();
    const release = Promise.withResolvers<void>();
    await page.route("**/api/v1/models/browse?**", async (route) => {
      requested.resolve();
      await release.promise;
      await route.fallback();
    });

    await page.goto("/?c=maraio");
    await requested.promise;
    const placeholder = page.locator(".library-subcollections.library-loading-placeholder");
    await expect(placeholder).toBeVisible();
    const reserved = await placeholder.boundingBox();
    release.resolve();

    await expect(page.getByRole("heading", { name: "Visible model", exact: true })).toBeVisible();
    const populated = await page
      .getByRole("region", { name: "Collections", exact: true })
      .boundingBox();
    expect(populated?.height).toBe(reserved?.height);
    expect(populated?.width).toBe(reserved?.width);
  });
});
