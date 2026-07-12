import { test, expect } from "./helpers";
import { uploadModel } from "./util";

test("create and delete a tag", async ({ page }) => {
  const tag = `e2e-tag-${Date.now()}`;

  await page.goto("/organize");
  await expect(page.getByRole("heading", { name: "Tags" }).first()).toBeVisible();

  const input = page.getByPlaceholder("New tag...");
  await input.fill(tag);
  await input.press("Enter");

  // Tags are uppercased in the chip; match the delete control instead (stable).
  const del = page.getByRole("button", { name: `Delete tag ${tag}` });
  await expect(del).toBeVisible();

  // Persisted across reload.
  await page.reload();
  await expect(page.getByRole("button", { name: `Delete tag ${tag}` })).toBeVisible();

  // Tags use the shared confirmation dialog regardless of assignment count.
  await page.getByRole("button", { name: `Delete tag ${tag}` }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
  await expect(page.getByRole("button", { name: `Delete tag ${tag}` })).toHaveCount(0);
});

test("delete a tag that is assigned to a model (with confirm)", async ({ page }) => {
  const tag = `e2e-assigned-${Date.now()}`;
  const model = `e2e-tagged-${Date.now()}`;

  // Inline-create the tag during upload, which also assigns it to the model.
  await uploadModel(page, model, { tag });

  await page.goto("/organize");
  const del = page.getByRole("button", { name: `Delete tag ${tag}` });
  await expect(del).toBeVisible();

  // Assigned tags describe their impact in the shared confirmation dialog.
  await del.click();
  await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
  await expect(page.getByRole("button", { name: `Delete tag ${tag}` })).toHaveCount(0);
});
