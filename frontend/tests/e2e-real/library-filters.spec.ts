/** Library dropdowns filter real uploaded Models and retain their state after a reload. */
import { test, expect } from "./helpers";
import { modelCard, uploadGcodeModel } from "./util";

test.describe("Library filter dropdowns", () => {
  test("filters a real Model through compact pickers", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const name = `e2e-filter-${Date.now()}`;
    await uploadGcodeModel(page, name);
    try {
      await page
        .getByRole("main")
        .getByRole("button", { name: /^Filters(?: \d+)?$/ })
        .filter({ visible: true })
        .click();
      const filters = page.getByRole("dialog", { name: "Filter Models", exact: true });
      // ── Printer location shares the same checkbox choices ──
      await filters.getByRole("button", { name: "Printer", exact: true }).click();
      const printer = filters;
      await printer.getByRole("checkbox", { name: "Vault only", exact: true }).click();
      await expect(page).toHaveURL(/printer_presence=none/);
      await expect(
        printer.getByRole("checkbox", { name: "Vault only", exact: true }),
      ).toBeChecked();
      await expect(modelCard(page, name)).toBeVisible();
      await printer.getByRole("checkbox", { name: "On a printer", exact: true }).click();
      await expect(page).toHaveURL(/printer_presence=any/);
      await expect(page.getByRole("main")).toHaveAttribute("aria-busy", "false");
      await expect(modelCard(page, name)).toHaveCount(0);
      await printer.getByRole("checkbox", { name: "On a printer", exact: true }).uncheck();
      await expect(page).not.toHaveURL(/printer_presence=/);
      await expect(modelCard(page, name)).toBeVisible();
      await page.keyboard.press("Escape");

      // ── File type and printed status ──
      await page
        .getByRole("button", { name: "Filters", exact: true })
        .filter({ visible: true })
        .click();
      await filters.getByRole("button", { name: "File type", exact: true }).click();
      await filters.getByRole("checkbox", { name: "G-code" }).click();
      await expect(page).toHaveURL(/file_type=gcode/);
      await expect(filters.getByRole("checkbox", { name: "G-code" })).toBeChecked();
      await page.keyboard.press("Escape");
      await page
        .getByRole("button", { name: "Filters", exact: true })
        .filter({ visible: true })
        .click();
      await filters.getByRole("button", { name: /^Printed/ }).click();
      await filters.getByRole("checkbox", { name: "Not printed", exact: true }).click();
      await expect(page).toHaveURL(/printed=no/);
      await page.keyboard.press("Escape");
      await expect(modelCard(page, name)).toBeVisible();

      // ── Upload calendar retains a range through a reload ──
      await page
        .getByRole("button", { name: "Filters", exact: true })
        .filter({ visible: true })
        .click();
      await filters.getByRole("button", { name: /^Uploaded/ }).click();
      const uploaded = filters;
      await uploaded.getByLabel("After", { exact: true }).fill("2000-01-01");
      await expect(page).toHaveURL(/uploaded_after=2000-01-01/);
      await expect(uploaded.getByLabel("After", { exact: true })).toHaveValue("2000-01-01");
      await uploaded.getByLabel("Before", { exact: true }).fill("2099-01-01");
      await expect(page).toHaveURL(/uploaded_before=2099-01-01/);
      await expect(uploaded.getByLabel("Before", { exact: true })).toHaveValue("2099-01-01");
      await page.keyboard.press("Escape");
      await expect(modelCard(page, name)).toBeVisible();
      await page.reload();
      await expect(modelCard(page, name)).toBeVisible();
      await expect(page.getByRole("dialog")).toHaveCount(0);
      await page
        .getByRole("button", { name: "Filters", exact: true })
        .filter({ visible: true })
        .click();
      await filters.getByRole("button", { name: /^Uploaded/ }).click();
      await expect(uploaded.getByLabel("After", { exact: true })).toHaveValue("2000-01-01");
      await expect(uploaded.getByLabel("Before", { exact: true })).toHaveValue("2099-01-01");
      await page.keyboard.press("Escape");

      // ── A history date excludes a Model without history; clearing restores it ──
      await page
        .getByRole("button", { name: "Filters", exact: true })
        .filter({ visible: true })
        .click();
      await filters.getByRole("button", { name: /^Printed/ }).click();
      await filters.getByRole("checkbox", { name: "Not printed", exact: true }).click();
      await expect(page).not.toHaveURL(/printed=no/);
      await page.keyboard.press("Escape");
      await page
        .getByRole("button", { name: "Filters", exact: true })
        .filter({ visible: true })
        .click();
      await filters.getByRole("button", { name: "Print history", exact: true }).click();
      const history = filters;
      await history.getByLabel("Finished on or after", { exact: true }).fill("2000-01-01");
      await expect(page).toHaveURL(/printed_after=2000-01-01/);
      await expect(page.getByRole("main")).toHaveAttribute("aria-busy", "false");
      await expect(modelCard(page, name)).toHaveCount(0);
      await history.getByLabel("Finished on or after", { exact: true }).clear();
      await expect(page).not.toHaveURL(/printed_after=/);
      await page.keyboard.press("Escape");
      await expect(modelCard(page, name)).toBeVisible();
    } finally {
      const found = await (
        await page.request.get(`/api/v1/models?q=${encodeURIComponent(name)}`)
      ).json();
      for (const model of found.items ?? found) {
        expect((await page.request.delete(`/api/v1/models/${model.id}`)).status()).toBe(204);
      }
    }
  });
});
