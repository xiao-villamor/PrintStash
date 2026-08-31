/**
 * Deleting a tag that models are actually using.
 *
 * The confirmation step is the point. Removing a tag from the system removes it from
 * every model that carries it, which is not what "delete" looks like from a single model's
 * edit form — so the flow asks first, and this test is what keeps it asking.
 */
import { test, expect } from "./helpers";
import { clickModelAction, modelCard, uploadModel } from "./util";

test.describe("tags", () => {
  test("assigns a newly created tag from the model card", async ({ page }) => {
    const tag = `e2e-quick-tag-${Date.now()}`;
    const model = `e2e-quick-tagged-${Date.now()}`;

    await uploadModel(page, model);
    await page.getByRole("button", { name: `Add tags to ${model}` }).click();
    const dialog = page.getByRole("dialog", { name: "Model tags" });
    await dialog.getByLabel("Search or create a tag").fill(tag);
    await dialog.getByRole("option", { name: /Create tag/ }).click();
    await dialog.getByRole("button", { name: "Save tags" }).click();

    await expect(modelCard(page, model)).toContainText(tag);
  });

  test("delete an assigned tag from model editing (with confirm)", async ({ page }) => {
    const tag = `e2e-assigned-${Date.now()}`;
    const model = `e2e-tagged-${Date.now()}`;

    await uploadModel(page, model, { tag });
    await modelCard(page, model).click();
    await clickModelAction(page, "Edit details");
    await page.getByRole("button", { name: `Remove ${tag}` }).click();
    await page.getByPlaceholder("Search or create — press Enter").fill(tag);
    const del = page.getByRole("button", { name: `Delete tag ${tag}` });
    await expect(del).toBeVisible();

    await del.click();
    await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
    await expect(del).toHaveCount(0);
  });
});
