/** Custom recovery screens fit narrow displays and return to the working Library. */
import { expect, test } from "@playwright/test";
import { useMockApi } from "./_setup";
useMockApi();

test.describe("Error pages", () => {
  test("unknown URL shows a responsive custom 404 with a working exit", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/this-page-does-not-exist");
    await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
    await expect(page.getByText("404", { exact: true })).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
    await page.getByRole("link", { name: "Back to vault" }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("heading", { name: "Page not found" })).toHaveCount(0);
  });

  test("recovers a failed model read in place", async ({ page }) => {
    let failing = true;
    await page.route("**/api/v1/models/1", async (route) => {
      if (failing)
        await route.fulfill({ status: 503, json: { detail: "internal private detail" } });
      else await route.continue();
    });
    await page.goto("/models/1");
    await expect(page.getByRole("heading", { name: "Couldn’t load this model" })).toBeVisible();
    await expect(page.getByText("internal private detail")).toHaveCount(0);
    failing = false;
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Couldn’t load this model" })).toHaveCount(0);
    await expect(page.getByRole("tab", { name: "Overview", exact: true })).toBeVisible();
  });
});
