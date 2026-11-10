/** The reference shell keeps its production collection tree beside the rail, independently of page content. */
import { expect, test } from "@playwright/test";
import { useMockApi } from "./_setup";

useMockApi();

test.describe("Global collection pane", () => {
  test("starts the desktop topbar after the full-height Library tree", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    const pane = page.getByRole("complementary", { name: "Collections" });
    await expect(page.getByRole("heading", { name: "All Models", exact: true })).toBeVisible();
    await expect(pane).toBeVisible();

    const geometry = await pane.evaluate((node) => {
      const pane = node.getBoundingClientRect();
      const header = document.querySelector("header")!.getBoundingClientRect();
      return {
        paneTop: pane.top,
        paneHeight: pane.height,
        paneRight: pane.right,
        headerLeft: header.left,
        viewportHeight: window.innerHeight,
      };
    });

    expect(geometry.paneTop).toBe(0);
    expect(geometry.paneHeight).toBe(geometry.viewportHeight);
    expect(geometry.headerLeft).toBe(geometry.paneRight);
    await expect(page.locator("header").getByRole("navigation")).toBeVisible();
  });

  test("preserves an explicit collapsed preference after reload", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/search");
    const toggle = page
      .locator("header")
      .getByRole("button", { name: "Toggle sidebar", exact: true });
    await expect(toggle).toHaveAttribute("aria-expanded", "true");

    await toggle.click();
    await page.reload();

    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByRole("complementary", { name: "Search panel" })).toBeHidden();
  });

  test("keeps Library content usable when the desktop pane is absent on phones", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");

    await expect(page.getByRole("main")).toBeVisible();
    await expect(page.getByRole("heading", { name: "All Models", exact: true })).toBeVisible();
    await expect(page.getByRole("complementary", { name: "Collections" })).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
    const main = await page.getByRole("main").boundingBox();
    const navigation = await page
      .getByRole("navigation", { name: "Navigation", exact: true })
      .boundingBox();
    expect(main).not.toBeNull();
    expect(navigation).not.toBeNull();
    expect(main!.y + main!.height).toBeLessThanOrEqual(navigation!.y);
  });
});
