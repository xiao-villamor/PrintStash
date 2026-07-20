import { test, expect, authBundleFor, authedContext } from "./helpers";
import { createCollectionViaVault, modelCard, uploadModel } from "./util";

// Batch move/tag/delete (new in 0.8.0). Models live in their own collection so
// "Select all on screen" is scoped to just them — the shared serial DB is never
// at risk of a batch op hitting another test's models.

test("batch-tag and batch-delete two models from the toolbar", async ({ page }) => {
  const stamp = Date.now();
  const col = `e2e-batch-${stamp}`;
  const tag = `batch-tag-${stamp}`;
  const m1 = `e2e-batch-a-${stamp}`;
  const m2 = `e2e-batch-b-${stamp}`;

  await createCollectionViaVault(page, col);
  await uploadModel(page, m1, { collection: col });
  await uploadModel(page, m2, { collection: col });

  await page.goto(`/?c=${col}`);
  await expect(modelCard(page, m1)).toBeVisible();
  await expect(modelCard(page, m2)).toBeVisible();

  // Enter select mode, select both (scoped to this collection), open the toolbar.
  await page.getByRole("button", { name: "Select", exact: true }).click();
  await page.getByRole("button", { name: /Select all on screen \(2\)/ }).click();
  // Both the grid header and the floating toolbar say "2 selected" — either proves it.
  await expect(page.getByText("2 selected").first()).toBeVisible();

  // Batch tag → the chip shows up on the cards. exact: true — the shared
  // serial DB can carry tag chips from earlier tests whose accessible names
  // contain "Tag" as a substring (e.g. "e2e-view-tag-..."), which a
  // non-exact match here would collide with.
  await page.getByRole("button", { name: "Tag", exact: true }).click();
  const tagInput = page.getByPlaceholder("Search or create — press Enter");
  await tagInput.fill(tag);
  await tagInput.press("Enter");
  await page.getByRole("button", { name: "Apply" }).click();

  await page.goto(`/?c=${col}`);
  await expect(modelCard(page, m1).getByText(tag, { exact: false })).toBeVisible();

  // Batch delete → both move to trash, collection empties.
  await page.getByRole("button", { name: "Select", exact: true }).click();
  await page.getByRole("button", { name: /Select all on screen \(2\)/ }).click();
  // Scope to the floating toolbar — "Delete" otherwise also matches the sidebar's
  // "Delete collection" button (substring match).
  await page.locator("div.fixed.bottom-4").getByRole("button", { name: "Delete" }).click();
  // The toolbar's button and the confirm modal both say "Delete" — scope to the modal.
  await page.getByRole("dialog").getByRole("button", { name: "Delete", exact: true }).click();
  await expect(modelCard(page, m1)).toHaveCount(0);
  await expect(modelCard(page, m2)).toHaveCount(0);

  // Cleanup: purge both from the trash so the shared DB doesn't accumulate. The
  // e2e backend is wiped per run, so the only exact-"Delete" buttons here are the
  // two trash rows. Purge sequentially, waiting for the row count to drop each
  // time (robust against ordering and the busy-disable between clicks).
  await page.goto("/settings");
  await page.getByRole("button", { name: "Trash" }).click();
  const purgeButtons = page.getByRole("button", { name: "Delete", exact: true });
  await expect(purgeButtons).toHaveCount(2);
  await purgeButtons.first().click();
  await page.getByRole("button", { name: "Delete forever" }).click();
  await expect(purgeButtons).toHaveCount(1);
  await purgeButtons.first().click();
  await page.getByRole("button", { name: "Delete forever" }).click();
  await expect(purgeButtons).toHaveCount(0);
});

test("bulk-move a collection's models to another folder, then undo", async ({ page }) => {
  const stamp = Date.now();
  const source = `e2e-batch-move-src-${stamp}`;
  const dest = `e2e-batch-move-dst-${stamp}`;
  const model = `e2e-batch-move-model-${stamp}`;

  await createCollectionViaVault(page, source);
  await createCollectionViaVault(page, dest);
  await uploadModel(page, model, { collection: source });

  await page.goto(`/?c=${source}`);
  await expect(modelCard(page, model)).toBeVisible();
  await page.getByRole("button", { name: "Select", exact: true }).click();
  await page.getByRole("button", { name: /Select all on screen \(1\)/ }).click();

  await page.locator("div.fixed.bottom-4").getByRole("button", { name: "Move" }).click();
  const moveDialog = page.getByRole("dialog", { name: /^Move \d+ item/ });
  await expect(moveDialog).toBeVisible();
  await moveDialog.getByPlaceholder("Find destination").fill(dest);
  await moveDialog.getByRole("button", { name: new RegExp(`^${dest} `) }).click();
  await moveDialog.getByRole("button", { name: "Move here" }).click();

  await expect(modelCard(page, model)).toHaveCount(0);
  // `page.goto` is a hard navigation that would unmount the undo toast — use
  // the sidebar (client-side routing) to check the destination instead, so
  // the toast (and its "Undo" action) survives to be clicked below.
  await page.locator("aside").getByRole("button", { name: dest, exact: true }).click();
  await expect(modelCard(page, model)).toBeVisible();

  // Undo restores it to the source collection.
  await page.getByRole("button", { name: "Undo" }).click();
  await expect(modelCard(page, model)).toHaveCount(0);
  await page.locator("aside").getByRole("button", { name: source, exact: true }).click();
  await expect(modelCard(page, model)).toBeVisible();
});

test("bulk-apply a revision label across selected G-code revisions", async ({ page }) => {
  const name = `e2e-batch-labels-${Date.now()}`;
  const label = `verified-${Date.now()}`;

  await uploadModel(page, name);
  await modelCard(page, name).click();
  await expect(page.getByRole("heading", { name })).toBeVisible();
  await page.getByRole("tab", { name: /Revisions/ }).click();
  await expect(page.getByText("Rev 1", { exact: true }).first()).toBeVisible();

  await page.getByRole("button", { name: "Add", exact: true }).click();
  await page.locator('input[accept=".gcode,.g,.gco"]').setInputFiles({
    name: "rev2.gcode",
    mimeType: "text/plain",
    buffer: Buffer.from(`; second revision for ${name}\nG28\n`),
  });
  await page.getByRole("button", { name: "Add revision" }).click();
  await expect(page.getByText("Rev 2", { exact: true }).first()).toBeVisible();

  await page.getByRole("button", { name: "Edit labels" }).click();
  await page.getByLabel("Select revision 1").check();
  await page.getByLabel("Select revision 2").check();
  await expect(page.getByText("2 selected")).toBeVisible();
  await page.getByPlaceholder("Label (blank clears)").fill(label);
  await page.getByRole("button", { name: "Apply label" }).click();

  await expect(page.getByText(label)).toHaveCount(2);
});

// "Every model is preflighted; any missing or non-editable model rejects the
// whole request without writes" (backend/app/api/v1/models.py batch endpoints).
// A viewer-role user can still open the batch toolbar (it isn't role-gated in
// the UI), but the backend's all-or-nothing preflight must reject the request
// server-side and leave the model untouched.
test("a batch delete from a viewer-role user is rejected by the backend preflight, leaving the model in place", async ({
  page,
  browser,
}) => {
  const stamp = Date.now();
  const col = `e2e-batch-preflight-${stamp}`;
  const model = `e2e-batch-preflight-model-${stamp}`;
  const viewer = `e2e-batch-viewer-${stamp}`;
  const password = "userpass123";

  await createCollectionViaVault(page, col);
  await uploadModel(page, model, { collection: col });

  await page.goto("/settings");
  await page.getByRole("button", { name: "Users & Access" }).click();
  await page.getByLabel("Username").fill(viewer);
  await page.getByLabel("Initial password").fill(password);
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByRole("paragraph").filter({ hasText: viewer })).toBeVisible();

  await page.goto("/settings?section=access");
  const accessCard = page.getByRole("group", { name: "Collection access" });
  await accessCard.getByRole("combobox").filter({ has: page.getByRole("option", { name: "Select user" }) }).selectOption({ label: viewer });
  await accessCard.getByRole("combobox").filter({ has: page.getByRole("option", { name: "Select collection" }) }).selectOption({ label: col });
  await accessCard.getByRole("combobox").filter({ has: page.getByRole("option", { name: "Admin", exact: true }) }).selectOption({ label: "View" });
  await Promise.all([
    page.waitForResponse((r) => /\/collections\/\d+\/permissions\/\d+/.test(r.url()) && r.request().method() === "PUT"),
    accessCard.getByRole("button", { name: "Grant" }).click(),
  ]);

  const bundle = await authBundleFor(viewer, password);
  const { context, page: viewerPage } = await authedContext(browser, bundle);
  try {
    await viewerPage.goto(`/?c=${col}`);
    await expect(modelCard(viewerPage, model)).toBeVisible();
    await viewerPage.getByRole("button", { name: "Select", exact: true }).click();
    await viewerPage.getByRole("button", { name: /Select all on screen \(1\)/ }).click();
    await viewerPage.locator("div.fixed.bottom-4").getByRole("button", { name: "Delete" }).click();
    await viewerPage.getByRole("dialog").getByRole("button", { name: "Delete", exact: true }).click();

    // Rejected server-side — no toast.undo (that only fires on success), the
    // model is still present, and it survives a reload.
    await expect(viewerPage.getByRole("button", { name: "Undo" })).toHaveCount(0);
    await expect(modelCard(viewerPage, model)).toBeVisible();
    await viewerPage.reload();
    await expect(modelCard(viewerPage, model)).toBeVisible();
  } finally {
    await context.close();
  }
});
