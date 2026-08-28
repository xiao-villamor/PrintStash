/**
 * Building and dismantling the folder tree the whole library is organised by.
 *
 * A collection is a path, and every model under it carries that path, so deleting one is
 * never just deleting a row. The recursive delete is here because it is the one action
 * that can take a shelf of somebody's library with it, and the flow that guards it lives
 * in the UI rather than the API.
 */
import { test, expect } from "./helpers";
import { createCollectionViaVault, uploadModel } from "./util";

// Real CRUD against the backend DB.

test.describe("collections", () => {
  test("create and delete an empty collection (persists across reload)", async ({ page }) => {
    const name = `e2e-col-${Date.now()}`;
    await createCollectionViaVault(page, name);
    const sidebar = page.locator("aside");
    const collection = sidebar.getByRole("button", { name, exact: true });

    // Survives a full reload — proves real persistence, not optimistic UI.
    await page.reload();
    await expect(collection).toBeVisible();

    await collection.hover();
    await collection.locator("xpath=following-sibling::button[@title='Delete collection']").click();
    await sidebar.getByRole("button", { name: "Delete", exact: true }).click();
    await expect(collection).toHaveCount(0);

    await page.reload();
    await expect(collection).toHaveCount(0);
  });

  test("nest a subcollection and delete the child", async ({ page }) => {
    const parent = `e2e-parent-${Date.now()}`;
    const child = `e2e-child-${Date.now()}`;

    await createCollectionViaVault(page, parent);
    await createCollectionViaVault(page, child, parent);
    await page.goto("/");
    const sidebar = page.locator("aside");
    const parentRow = sidebar.getByRole("button", { name: parent, exact: true });
    await parentRow.locator("xpath=preceding-sibling::button[@aria-label='Expand']").click();
    const childRow = sidebar.getByRole("button", { name: child, exact: true });
    await childRow.hover();
    await childRow.locator("xpath=following-sibling::button[@title='Delete collection']").click();
    await sidebar.getByRole("button", { name: "Delete", exact: true }).click();
    await expect(childRow).toHaveCount(0);

    // The parent deletes cleanly even though it once had a (now soft-deleted)
    // child — regression guard for the has-children check ignoring trashed rows.
    await parentRow.hover();
    await parentRow.locator("xpath=following-sibling::button[@title='Delete collection']").click();
    await sidebar.getByRole("button", { name: "Delete", exact: true }).click();
    await expect(parentRow).toHaveCount(0);
  });

  test("recursive-delete a non-empty collection from the sidebar", async ({ page }) => {
    const stamp = Date.now();
    const col = `e2e-recur-${stamp}`;
    const model = `e2e-recur-model-${stamp}`;

    await createCollectionViaVault(page, col);
    await uploadModel(page, model, { collection: col });

    // Sidebar outliner: hover the collection row, hit its delete, confirm.
    await page.goto("/");
    const sidebar = page.locator("aside");
    const label = sidebar.getByRole("button", { name: col, exact: true });
    await expect(label).toBeVisible();
    await label.hover();
    await label.locator("xpath=following-sibling::button[@title='Delete collection']").click();
    await sidebar.getByRole("button", { name: "Delete", exact: true }).click();
    await expect(sidebar.getByRole("button", { name: col, exact: true })).toHaveCount(0);

    // The contained model landed in the trash (recycle bin), not hard-deleted.
    await page.goto("/settings");
    await page.getByRole("button", { name: "Trash" }).click();
    await expect(page.getByText(model)).toBeVisible();

    // Clean up: purge it forever so the shared DB doesn't accumulate trash.
    await page.getByRole("button", { name: "Delete", exact: true }).click();
    await page.getByRole("button", { name: "Delete forever" }).click();
    await expect(page.getByText(model)).toHaveCount(0);
  });
});

// The Vault outliner recursively deletes non-empty collections, moving the
// contained models to the trash.
