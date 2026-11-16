import { expect, type Page } from "@playwright/test";

/** The first page adapts to the viewport; virtualization need not mount every item. */
export async function assertBoundedFirstRender(page: Page) {
  await expect(page.locator("main article").first()).toBeVisible();
  const mounted = await page.locator("main article").count();
  expect(mounted).toBeGreaterThan(0);
  expect(mounted).toBeLessThanOrEqual(24);
}

/** Observe all fixture identities through user scrolling, including recycled rows. */
export async function visitDenseModels(page: Page) {
  const seen = new Set<string>();
  await expect
    .poll(
      async () => {
        const identities = await page
          .locator("main article [data-library-entry]")
          .evaluateAll((links) => links.map((link) => link.getAttribute("data-library-entry")!));
        expect(new Set(identities).size).toBe(identities.length);
        for (const identity of identities) seen.add(identity);
        if (seen.size < 90) {
          await page.getByRole("main").evaluate((main) => {
            main.scrollTop += main.clientHeight / 2;
          });
        }
        return seen.size;
      },
      { timeout: 30_000, intervals: [100] },
    )
    .toBe(90);
  await expect(page.locator('[data-library-pagination="models"]')).toHaveCount(0);
}
