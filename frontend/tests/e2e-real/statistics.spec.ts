/*
 * Overview and Rankings share the reporting period. The real desktop flow
 * checks the persisted selection across tabs without depending on the vault's
 * starting print history.
 */
import { test, expect } from "./helpers";
import { collapseNavigationSidebar } from "./util";

test.describe("Statistics", () => {
  test("keeps the reporting period when switching to Rankings", async ({ page }) => {
    await page.goto("/statistics");
    await collapseNavigationSidebar(page);

    const period = page.getByRole("combobox", { name: "Period" });
    const result = page.waitForResponse((response) =>
      response.url().includes("/models/stats/prints?period=all"),
    );
    await period.selectOption("all");
    expect((await result).ok()).toBe(true);

    await page.getByRole("tab", { name: "Rankings" }).click();
    await expect(period).toHaveValue("all");
  });
});
