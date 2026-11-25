/**
 * Building and dismantling the folder tree the whole library is organised by.
 *
 * A collection has a stable identity and a mutable path; Models belong to that identity. Deleting one is
 * never just deleting a row. The recursive delete is here because it is the one action
 * that can take a shelf of somebody's library with it, and the flow that guards it lives
 * in the UI rather than the API. Keyboard focus keeps each action exposed while
 * deferred tree reads move rows away from the pointer. Heading renames must keep the
 * selected branch reachable after reload and let keyboard users discard an unsaved name.
 */
import { readFile } from "node:fs/promises";
import { test, expect } from "./helpers";
import {
  openModelPage,
  createCollectionViaVault,
  gcodeFor,
  modelCard,
  openLibraryTools,
  uploadModel,
} from "./util";

// Real CRUD against the backend DB.

test.describe("collections", () => {
  test("create and delete an empty collection (persists across reload)", async ({ page }) => {
    const name = `e2e-col-${Date.now()}`;
    await createCollectionViaVault(page, name);
    const sidebar = page.locator("aside");
    const collection = sidebar.getByRole("treeitem", { name, exact: true });

    // Survives a full reload — proves real persistence, not optimistic UI.
    await page.reload();
    await expect(collection).toBeVisible();

    await collection.focus();
    await collection
      .locator("..")
      .getByRole("button", { name: "Delete collection", exact: true })
      .click();
    await sidebar.getByRole("button", { name: "Delete", exact: true }).click();
    await expect(collection).toHaveCount(0);

    await page.reload();
    await expect(collection).toHaveCount(0);
  });

  test("quick-renames a populated nested collection from its heading", async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    const stamp = Date.now();
    const parent = `e2e-rename-parent-${stamp}`;
    const name = `e2e-rename-child-${stamp}`;
    const renamed = `${name}-renamed`;
    const model = `e2e-rename-model-${stamp}`;
    await createCollectionViaVault(page, parent);
    await createCollectionViaVault(page, name, parent);
    await uploadModel(page, model, { collection: `${parent}/${name}` });
    const modelHref = await modelCard(page, model).getAttribute("href");
    expect(modelHref).toMatch(/^\/models\/\d+(?:\?|$)/);
    const modelPath = new URL(modelHref!, page.url()).pathname;
    const sameModel = new RegExp(`^${modelPath}(?:\\?|$)`);

    // ── Double-click the heading to rename the selected collection ──
    const heading = page.getByRole("heading", { name, exact: true });
    await page.evaluate(() => document.fonts.ready);
    const headingBox = await heading.boundingBox();
    await heading.dblclick();
    const input = page.getByRole("textbox", { name: "Collection name", exact: true });
    await expect(input).toBeFocused();
    await expect(input).toHaveValue(name);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.locator("aside").getByRole("treeitem", { name, exact: true })).toBeVisible();
    await expect(modelCard(page, model)).toBeVisible();
    const editorBox = await input.boundingBox();
    expect(headingBox).not.toBeNull();
    expect(editorBox).not.toBeNull();
    expect(Math.abs(editorBox!.y - headingBox!.y)).toBeLessThanOrEqual(8);
    expect(editorBox!.x).toBeLessThanOrEqual(headingBox!.x);
    expect(editorBox!.x + editorBox!.width).toBeGreaterThan(headingBox!.x + headingBox!.width);
    const surface = (await page.getByRole("main").boundingBox())!;
    expect(editorBox!.x + editorBox!.width).toBeLessThanOrEqual(surface.x + surface.width);
    expect(editorBox!.height).toBeLessThan(200);
    await input.fill(renamed);
    await input.press("Enter");
    await expect(input).toHaveCount(0);
    await expect(page.getByRole("heading", { name: renamed, exact: true })).toBeVisible();
    await expect(page).toHaveURL((url) => url.searchParams.get("c") === `${parent}/${renamed}`);
    await expect(modelCard(page, model)).toHaveAttribute("href", sameModel);
    await page.reload();
    await expect(page.getByRole("heading", { name: renamed, exact: true })).toBeVisible();

    // ── The same Model and its Artifact survive the collection's new path ──
    await expect(modelCard(page, model)).toHaveAttribute("href", sameModel);
    await openModelPage(page, model);
    await expect(page).toHaveURL((url) => url.pathname === modelPath);
    await expect(page.getByRole("heading", { name: model, exact: true })).toBeVisible();
    await page.getByRole("tab", { name: /^Revisions/ }).click();
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("button", { name: "Download", exact: true }).first().click(),
    ]);
    expect(download.suggestedFilename()).toBe(`${model}.gcode`);
    const downloadedPath = await download.path();
    expect(downloadedPath).not.toBeNull();
    expect(await readFile(downloadedPath!, "utf8")).toBe(gcodeFor(model));
    await page.getByRole("link", { name: "Back", exact: true }).click();
    await expect(page).toHaveURL((url) => url.searchParams.get("c") === `${parent}/${renamed}`);
    await expect(page.getByRole("heading", { name: renamed, exact: true })).toBeVisible();

    // ── The pen is keyboard-accessible; Escape discards an unsaved rename ──
    const renameButton = page
      .getByRole("main")
      .getByRole("button", { name: `Rename ${renamed}`, exact: true });
    await renameButton.focus();
    await renameButton.press("Enter");
    const cancelledInput = page.getByRole("textbox", {
      name: "Collection name",
      exact: true,
    });
    await expect(cancelledInput).toBeFocused();
    await cancelledInput.fill(`${renamed}-discarded`);
    await cancelledInput.press("Escape");
    await expect(cancelledInput).toHaveCount(0);
    await expect(renameButton).toBeFocused();
    await page.reload();
    await expect(page.getByRole("heading", { name: renamed, exact: true })).toBeVisible();

    // ── Remove this flow's collection branch ──
    const sidebar = page.locator("aside");
    const parentRow = sidebar.getByRole("treeitem", { name: parent, exact: true });
    await parentRow.focus();
    await parentRow
      .locator("..")
      .getByRole("button", { name: "Delete collection", exact: true })
      .click();
    await sidebar.getByRole("button", { name: "Delete", exact: true }).click();
    await expect(parentRow).toHaveCount(0);
    await page.goto("/settings?section=trash");
    await page
      .getByRole("checkbox", { name: `Select deleted model ${model}`, exact: true })
      .locator("..")
      .getByRole("button", { name: "Delete", exact: true })
      .click();
    await page.getByRole("button", { name: "Delete forever", exact: true }).click();
    await expect(page.getByText(model, { exact: true })).toHaveCount(0);
  });

  test("keeps populated descendants reachable through ancestor renames", async ({ page }) => {
    const stamp = Date.now();
    const parent = `e2e-history-${stamp}`;
    const child = `child-${stamp}`;
    const model = `e2e-history-model-${stamp}`;
    const headingName = `${parent}-heading`;
    const bulkName = `${parent}-bulk`;
    await createCollectionViaVault(page, parent);
    await createCollectionViaVault(page, child, parent);
    await uploadModel(page, model, { collection: `${parent}/${child}` });
    await expect(page).toHaveURL((url) => url.searchParams.has("cid"));
    const childId = new URL(page.url()).searchParams.get("cid");
    const modelHref = await modelCard(page, model).getAttribute("href");
    const modelPath = new URL(modelHref!, page.url()).pathname;

    // ── History retains the descendant identity across a parent heading rename ──
    await page
      .getByRole("complementary", { name: "Collections", exact: true })
      .getByRole("treeitem", { name: parent, exact: true })
      .click();
    await page.getByRole("button", { name: `Rename ${parent}`, exact: true }).click();
    await page.getByRole("textbox", { name: "Collection name", exact: true }).fill(headingName);
    await page.getByRole("textbox", { name: "Collection name", exact: true }).press("Enter");
    await expect(page.getByRole("heading", { name: headingName, exact: true })).toBeVisible();
    await page.goBack();
    await expect(page).toHaveURL((url) => url.searchParams.get("c") === `${headingName}/${child}`);
    await expect(page).toHaveURL((url) => url.searchParams.get("cid") === childId);
    await expect(modelCard(page, model)).toHaveAttribute(
      "href",
      new RegExp(`^${modelPath}(?:\\?|$)`),
    );
    await page.reload();
    await expect(modelCard(page, model)).toBeVisible();

    // ── Bulk rename resolves persisted Recent entries through the same identity ──
    await page.goto("/");
    await openLibraryTools(page);

    const folderChoice = page.getByRole("checkbox", {
      name: `Select folder ${headingName}`,
      exact: true,
    });
    await folderChoice.locator("..").hover();
    await folderChoice.check();
    await page.getByRole("button", { name: "Rename", exact: true }).click();
    const rename = page.getByRole("dialog");
    await rename.getByRole("textbox").fill(bulkName);
    await rename.getByRole("button", { name: "Rename", exact: true }).click();
    await expect(rename).toHaveCount(0);
    await page.reload();
    await page.getByRole("button", { name: "Recent", exact: true }).click();
    await page.getByRole("menuitem", { name: `${bulkName}/${child}`, exact: true }).click();
    await expect(page).toHaveURL((url) => url.searchParams.get("cid") === childId);
    await expect(modelCard(page, model)).toHaveAttribute(
      "href",
      new RegExp(`^${modelPath}(?:\\?|$)`),
    );
    await openModelPage(page, model);
    await page.getByRole("tab", { name: /^Revisions/ }).click();
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("button", { name: "Download", exact: true }).first().click(),
    ]);
    expect(await readFile((await download.path())!, "utf8")).toBe(gcodeFor(model));

    // ── Clean only this flow's Models and collection branch ──
    await page.goto("/");
    const parentRow = page.locator("aside").getByRole("treeitem", { name: bulkName, exact: true });
    await parentRow.focus();
    await parentRow
      .locator("..")
      .getByRole("button", { name: "Delete collection", exact: true })
      .click();
    await page.locator("aside").getByRole("button", { name: "Delete", exact: true }).click();
    await expect(parentRow).toHaveCount(0);
    await page.goto("/settings?section=trash");
    await page
      .getByRole("checkbox", { name: `Select deleted model ${model}`, exact: true })
      .locator("..")
      .getByRole("button", { name: "Delete", exact: true })
      .click();
    await page.getByRole("button", { name: "Delete forever", exact: true }).click();
    await expect(page.getByText(model, { exact: true })).toHaveCount(0);
  });

  test("deletes an emptied parent collection after its child", async ({ page }) => {
    const parent = `e2e-parent-${Date.now()}`;
    const child = `e2e-child-${Date.now()}`;

    await createCollectionViaVault(page, parent);
    await createCollectionViaVault(page, child, parent);
    await page.goto("/");
    const sidebar = page.locator("aside");
    const parentRow = sidebar.getByRole("treeitem", { name: parent, exact: true });
    await parentRow.focus();
    await parentRow.press("ArrowRight");
    const childRow = sidebar.getByRole("treeitem", { name: child, exact: true });
    await childRow.focus();
    await childRow
      .locator("..")
      .getByRole("button", { name: "Delete collection", exact: true })
      .click();
    await sidebar.getByRole("button", { name: "Delete", exact: true }).click();
    await expect(childRow).toHaveCount(0);

    // The parent deletes cleanly even though it once had a (now soft-deleted)
    // child — regression guard for the has-children check ignoring trashed rows.
    // Refetching the emptied branch can move the pointer off its row. Keyboard
    // focus reveals the same action and survives that layout change.
    await parentRow.focus();
    await parentRow
      .locator("..")
      .getByRole("button", { name: "Delete collection", exact: true })
      .click();
    await sidebar.getByRole("button", { name: "Delete", exact: true }).click();
    await expect(parentRow).toHaveCount(0);
  });

  test("parent count includes Models stored in a child collection", async ({ page }, testInfo) => {
    const stamp = Date.now();
    const suffix = stamp.toString(36).slice(-5);
    const parent = `p-${suffix}`;
    const child = `c-${suffix}`;
    const model = `e2e-count-model-${stamp}`;

    await createCollectionViaVault(page, parent);
    await createCollectionViaVault(page, child, parent);
    await uploadModel(page, model, { collection: `${parent}/${child}` });

    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto("/");
    const sidebar = page.locator("aside");
    const parentRow = sidebar.getByRole("treeitem", { name: parent, exact: true });
    const childRow = sidebar.getByRole("treeitem", { name: child, exact: true });
    for (const row of [parentRow, childRow]) {
      const badge = row.locator("xpath=following-sibling::span[last()]");
      await expect(badge).toHaveText("1");
      const sidebarBounds = await sidebar.boundingBox();
      const badgeBounds = await badge.boundingBox();
      if (!sidebarBounds || !badgeBounds) throw new Error("Collection badge is not rendered");
      expect(badgeBounds.x + badgeBounds.width).toBeLessThanOrEqual(
        sidebarBounds.x + sidebarBounds.width,
      );
    }
    await sidebar.screenshot({ path: testInfo.outputPath("nested-collection-counts.png") });

    await parentRow.focus();
    await parentRow
      .locator("..")
      .getByRole("button", { name: "Delete collection", exact: true })
      .click();
    await sidebar.getByRole("button", { name: "Delete", exact: true }).click();
    await expect(parentRow).toHaveCount(0);

    await page.goto("/settings?section=trash");
    await expect(page.getByText(model)).toBeVisible();
    await page
      .getByRole("checkbox", { name: `Select deleted model ${model}`, exact: true })
      .locator("..")
      .getByRole("button", { name: "Delete", exact: true })
      .click();
    await page.getByRole("button", { name: "Delete forever" }).click();
    await expect(page.getByText(model)).toHaveCount(0);
  });

  test("recursive-delete a non-empty collection from the sidebar", async ({ page }) => {
    const stamp = Date.now();
    const col = `e2e-recur-${stamp}`;
    const model = `e2e-recur-model-${stamp}`;

    await createCollectionViaVault(page, col);
    await uploadModel(page, model, { collection: col });

    // Keep the action exposed even when the pointer leaves its moving row.
    await page.goto("/");
    const sidebar = page.locator("aside");
    const label = sidebar.getByRole("treeitem", { name: col, exact: true });
    await expect(label).toBeVisible();
    await label.focus();
    const deleteAction = label
      .locator("..")
      .getByRole("button", { name: "Delete collection", exact: true });
    await page.mouse.move(0, 0);
    await expect(deleteAction).toBeVisible();
    await deleteAction.click();
    await sidebar.getByRole("button", { name: "Delete", exact: true }).click();
    await expect(sidebar.getByRole("treeitem", { name: col, exact: true })).toHaveCount(0);

    // The contained model landed in the trash (recycle bin), not hard-deleted.
    await page.goto("/settings?section=trash");
    await expect(page.getByText(model)).toBeVisible();

    // Clean up: purge it forever so the shared DB doesn't accumulate trash.
    await page
      .getByRole("checkbox", { name: `Select deleted model ${model}`, exact: true })
      .locator("..")
      .getByRole("button", { name: "Delete", exact: true })
      .click();
    await page.getByRole("button", { name: "Delete forever" }).click();
    await expect(page.getByText(model)).toHaveCount(0);
  });
});

// The Vault outliner recursively deletes non-empty collections, moving the
// contained models to the trash.
