/** Multipart groupings link existing Models without taking ownership of their files. */
import { test, expect } from "./helpers";
import { modelCard, uploadModel } from "./util";

test.describe("multipart models", () => {
  test("preserves Models plus G-code after grouping deletion", async ({ page }) => {
    const stamp = Date.now();
    const base = `e2e-base-${stamp}`;
    const short = `e2e-handle-short-${stamp}`;
    const long = `e2e-handle-long-${stamp}`;
    const group = `e2e-multipart-${stamp}`;

    await uploadModel(page, base, { mesh: true, gcode: true });
    await uploadModel(page, short, { mesh: true, gcode: true });
    await uploadModel(page, long, { mesh: true, gcode: true });

    await page.goto("/?v=multipart");
    await page.getByRole("button", { name: "New multipart model" }).first().click();
    await page.getByLabel("Name", { exact: true }).fill(group);
    await page.getByRole("button", { name: "Create multipart model" }).click();

    await page.getByRole("button", { name: "Add a part" }).first().click();
    await page.getByRole("button", { name: new RegExp(base) }).click();
    await page.getByRole("button", { name: "Add another part" }).click();
    await page.getByRole("button", { name: new RegExp(short) }).click();
    await page
      .locator("fieldset")
      .nth(1)
      .getByRole("button", { name: "Add an alternative" })
      .click();
    await page.getByRole("button", { name: new RegExp(long) }).click();
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("Changes saved")).toBeVisible();
    await expect(page.getByText("Choose one").first()).toBeVisible();

    await page.goto("/?v=multipart");
    await page.getByRole("tab", { name: "Models", exact: true }).click();
    await expect(modelCard(page, base)).toBeVisible();
    await expect(modelCard(page, short)).toBeVisible();
    await expect(modelCard(page, long)).toBeVisible();

    await page.goto("/?v=multipart");
    await page.getByRole("link", { name: group }).click();
    const aggregateUrl = page.url();
    await page.getByRole("link", { name: new RegExp(short) }).click();
    await page.getByRole("tab", { name: "Revisions" }).click();
    await expect(page.getByText("Rev 1", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Recommended", { exact: true }).first()).toBeVisible();

    await page.goto(aggregateUrl);
    await page.getByRole("button", { name: "Delete multipart model" }).click();
    await expect(page.getByRole("dialog")).toContainText("Models, files and revisions stay");
    await page.getByRole("button", { name: "Delete grouping" }).click();
    await expect(page).toHaveURL(/\?v=multipart/);
    await page.getByRole("tab", { name: "Models", exact: true }).click();
    await expect(modelCard(page, base)).toBeVisible();
    await expect(modelCard(page, short)).toBeVisible();
    await expect(modelCard(page, long)).toBeVisible();
  });
});
