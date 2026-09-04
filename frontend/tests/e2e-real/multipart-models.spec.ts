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

    await page.goto("/");
    await page.getByRole("button", { name: "New multipart set" }).first().click();
    await page.getByLabel("Name", { exact: true }).fill(group);
    await page.getByRole("button", { name: "Create multipart set" }).click();

    await page.getByRole("button", { name: "Add a part" }).click();
    await page.getByRole("button", { name: new RegExp(base) }).click();
    await page.getByRole("button", { name: "Add another part" }).click();
    await page.getByRole("button", { name: new RegExp(short) }).click();
    await page.locator("fieldset").nth(1).getByRole("button", { name: "Add variant" }).click();
    await page.getByRole("button", { name: new RegExp(long) }).click();
    await page
      .locator('input[type="file"][accept="image/png,image/jpeg,image/webp"]')
      .setInputFiles({
        name: "multipart-cover.png",
        mimeType: "image/png",
        buffer: Buffer.from(
          "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAYAAADED76LAAAAFklEQVR4nGOM6rn0nwEPYMInOXwUAADOOgLHyCTqtwAAAABJRU5ErkJggg==",
          "base64",
        ),
      });
    await expect(page.getByText("Uploaded from your computer")).toBeVisible();
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("Changes saved")).toBeVisible();
    const uploadedCover = page.getByRole("img", { name: group });
    await expect(uploadedCover).toBeVisible();
    await expect
      .poll(() =>
        uploadedCover.evaluate((element) =>
          element instanceof HTMLImageElement ? element.naturalWidth : 0,
        ),
      )
      .toBeGreaterThan(0);
    await expect(page.getByText("Choose one").first()).toBeVisible();

    await page.getByRole("button", { name: "Edit multipart set" }).click();
    await page.locator('input[type="file"][accept*=".pdf"]').setInputFiles({
      name: "assembly.md",
      mimeType: "text/markdown",
      buffer: Buffer.from("# Assembly\n\nFit the base before the handle."),
    });
    await expect(page.getByRole("link", { name: "assembly" })).toBeVisible();

    await page.goto("/");
    await expect(page.getByRole("link", { name: group })).toBeVisible();
    await page.getByRole("button", { name: `Add ${group} to favorites` }).click();
    await page.getByRole("button", { name: `Add tags to ${group}` }).click();
    const tagsDialog = page.getByRole("dialog");
    const setTag = `assembly-${stamp}`;
    await tagsDialog.getByRole("textbox", { name: "Tags to add" }).fill(setTag);
    await tagsDialog.getByRole("button", { name: "Create tag" }).click();
    await tagsDialog.getByRole("button", { name: "Save tags" }).click();
    await expect(page.getByText(setTag.toUpperCase())).toBeVisible();
    await page.locator("aside").getByRole("button", { name: "Organized" }).click();
    await expect(modelCard(page, base)).toHaveCount(0);
    await page.goto("/?favorites=true");
    await expect(page.getByRole("link", { name: group })).toBeVisible();
    await page.goto("/?type=all");
    await expect(modelCard(page, base)).toBeVisible();
    await expect(modelCard(page, short)).toBeVisible();
    await expect(modelCard(page, long)).toBeVisible();

    await page.getByRole("link", { name: group }).click();
    const aggregateUrl = page.url();
    await page.getByRole("link", { name: new RegExp(short) }).click();
    await page.getByRole("tab", { name: "Revisions" }).click();
    await expect(page.getByText("Rev 1", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Recommended", { exact: true }).first()).toBeVisible();

    await page.goto(aggregateUrl);
    await page.getByRole("button", { name: "Edit multipart set" }).click();
    await expect(page.getByText("Uploaded from your computer")).toBeVisible();
    await page.getByRole("button", { name: "Delete multipart set" }).click();
    await expect(page.getByRole("dialog")).toContainText("Models, files and revisions stay");
    await page.getByRole("button", { name: "Delete set" }).click();
    await expect(page).toHaveURL(/\?type=all/);
    await expect(modelCard(page, base)).toBeVisible();
    await expect(modelCard(page, short)).toBeVisible();
    await expect(modelCard(page, long)).toBeVisible();
  });
});
