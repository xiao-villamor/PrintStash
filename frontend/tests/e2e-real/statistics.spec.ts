/*
 * Overview and Rankings share the reporting period. The real desktop flow
 * checks the persisted selection across tabs without depending on the vault's
 * starting print history.
 */
import { test, expect } from "./helpers";
import { collapseNavigationSidebar, openModelPage, uploadModel } from "./util";

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

  test("persists the failures panel for a manually recorded print", async ({ page }) => {
    const name = `statistics-failure-${Date.now()}`;
    const printer = "Statistics workshop printer";
    await uploadModel(page, name);
    await openModelPage(page, name);
    const modelPath = `/api/v1${new URL(page.url()).pathname}`;

    try {
      const response = await page.request.get(modelPath);
      expect(response.ok()).toBe(true);
      const { files } = await response.json();
      const job = await page.request.post(`${modelPath}/print-jobs`, {
        data: { file_id: files[0].id, printer_name: printer, state: "failed" },
      });
      expect(job.ok()).toBe(true);

      await page.goto("/statistics");
      await collapseNavigationSidebar(page);
      await page.getByRole("button", { name: "Customize" }).click();
      await page.getByRole("checkbox", { name: "Failures by printer" }).check();
      await page.keyboard.press("Escape");
      await expect(page.getByText(printer)).toBeVisible();

      await page.reload();
      await expect(page.getByText(printer)).toBeVisible();
    } finally {
      expect((await page.request.delete(modelPath)).ok()).toBe(true);
    }
  });
});
