/** Multipart groupings link existing Models without taking ownership of their files. */
import { openLibraryTools } from "./util";
import { test, expect } from "./helpers";
import { createCollectionViaVault, modelCard, uploadModel } from "./util";

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
    await openLibraryTools(page);
    await page.getByRole("button", { name: "New multipart set" }).first().click();
    await page.getByLabel("Name", { exact: true }).fill(group);
    await page.getByRole("button", { name: "Create multipart set" }).click();

    await page.getByRole("button", { name: "Add a part" }).click();
    await page.getByRole("button", { name: new RegExp(base) }).click();
    await page.getByRole("button", { name: "Add parts (1)" }).click();
    await page.getByRole("button", { name: "Add another part" }).click();
    await page.getByRole("button", { name: new RegExp(short) }).click();
    await page.getByRole("button", { name: "Add parts (1)" }).click();
    await page.locator("fieldset").nth(1).getByRole("button", { name: "Add variant" }).click();
    await page.getByRole("button", { name: new RegExp(long) }).click();
    await page.getByRole("button", { name: "Add variants (1)" }).click();
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
  test("builds multiple parts while browsing collections", async ({ page }) => {
    const stamp = Date.now();
    const folder = `picker-${stamp}`;
    const first = `base-${stamp}`;
    const second = `body-${stamp}`;
    const group = `kit-${stamp}`;

    await createCollectionViaVault(page, folder);
    await uploadModel(page, first, { collection: folder });
    await uploadModel(page, second, { collection: folder });
    await page.goto("/");
    await openLibraryTools(page);
    await page.getByRole("button", { name: "New multipart set" }).first().click();
    await page.getByLabel("Name", { exact: true }).fill(group);
    await page.getByRole("button", { name: "Create multipart set" }).click();
    await page.getByRole("button", { name: "Add a part" }).click();
    const picker = page.getByRole("dialog", { name: "Choose existing models" });
    await picker.getByRole("button", { name: folder, exact: true }).click();
    await picker.getByRole("button", { name: new RegExp(first) }).click();
    await picker.getByRole("textbox", { name: "Search existing models" }).fill(second);
    await picker.getByRole("button", { name: new RegExp(second) }).click();
    await picker.getByRole("button", { name: "Add parts (2)" }).click();
    await expect(page.getByLabel("Part name", { exact: true })).toHaveCount(2);
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("Changes saved")).toBeVisible();
    await page.reload();
    await expect(page.getByRole("link", { name: new RegExp(first) })).toBeVisible();
    await expect(page.getByRole("link", { name: new RegExp(second) })).toBeVisible();

    await page.getByRole("button", { name: "Edit multipart set" }).click();
    await page.getByRole("button", { name: "Delete multipart set" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Delete set" }).click();
    await page.goto(`/?c=${folder}`);
    const sidebar = page.locator("aside");
    const label = sidebar.getByRole("button", { name: folder, exact: true });
    await label.hover();
    await label.locator("xpath=following-sibling::button[@title='Delete collection']").click();
    await sidebar.getByRole("button", { name: "Delete", exact: true }).click();
    await expect(label).toHaveCount(0);
  });
});
