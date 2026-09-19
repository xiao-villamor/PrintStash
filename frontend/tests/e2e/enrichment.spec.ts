/* Saved sources receive optional previews through background work without a reload. */
import { test, expect } from "@playwright/test";
import { useMockApi } from "./_setup";

useMockApi();

test.describe("Background preview readiness", () => {
  test("a library card refreshes when its background preview becomes ready", async ({ page }) => {
    let ready = false;
    await page.route("**/api/v1/models/page?**", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      const model = body.items[0];
      await route.fulfill({
        response,
        json: {
          ...body,
          items: [
            {
              ...model,
              enrichment_pending: !ready,
              thumbnail_url: ready ? model.thumbnail_url : null,
            },
          ],
          total: 1,
          next_cursor: null,
        },
      });
    });
    await page.goto("/");
    const card = page.locator('a[href="/models/1"]');
    await expect(card.getByText("Preparing previews and details")).toBeVisible();
    await expect(card.getByRole("img")).toHaveCount(0);
    ready = true;
    await expect(card.getByRole("img")).toBeVisible();
    await expect(card.getByText("Preparing previews and details")).toHaveCount(0);
  });
});
