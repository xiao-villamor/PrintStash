/**
 * The whole life of a model, on the real backend.
 *
 * Upload → real ingestion → edit → trash → restore → purge, in one pass. Each step is
 * covered in isolation elsewhere; what this proves is that the states connect: a trashed
 * model really is invisible and really does come back, and a purge really is the end.
 * Deletion being recoverable is the promise the trash makes, and this is where it is kept.
 */
import { test, expect } from "./helpers";
import { clickModelAction, modelCard, uploadGcodeModel } from "./util";

// Full lifecycle on the REAL backend: upload G-code → real ingestion creates the
// model → edit it → soft-delete to trash → restore → purge forever.
test.describe("model library", () => {
  test("@critical upload, edit, trash, restore, and purge a model", async ({ page }) => {
    const name = `e2e-model-${Date.now()}`;
    const renamed = `${name}-renamed`;

    // Unique bytes avoid content deduplication across retries. The helper waits
    // for browser-owned upload finalization before leaving the page.
    await uploadGcodeModel(page, name);

    // ── Edit ──────────────────────────────────────────────────────────────────
    await modelCard(page, name).click();
    await expect(page.getByRole("heading", { name })).toBeVisible();
    await clickModelAction(page, "Edit details");
    const nameInput = page.getByPlaceholder("Model name");
    await nameInput.fill(renamed);
    await page.getByRole("button", { name: /^Save$/ }).click();
    await expect(page.getByRole("heading", { name: renamed })).toBeVisible();

    // ── Soft-delete to trash ────────────────────────────────────────────────────
    await clickModelAction(page, "Delete model");
    await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click(); // confirm
    await expect(modelCard(page, renamed)).toHaveCount(0);

    // ── Restore from Settings → Trash ───────────────────────────────────────────
    await page.goto("/settings");
    await page.getByRole("button", { name: "Trash" }).click();
    await expect(page.getByText(renamed)).toBeVisible();
    const trashRow = page.getByText(renamed, { exact: true }).locator("../../..");
    await trashRow.getByRole("button", { name: "Restore", exact: true }).click();
    await expect(page.getByText(renamed)).toHaveCount(0); // gone from trash

    // Back in the library.
    await expect(async () => {
      await page.goto("/");
      await expect(modelCard(page, renamed)).toBeVisible({ timeout: 2_000 });
    }).toPass({ timeout: 15_000 });

    // ── Delete + purge forever (cleanup) ────────────────────────────────────────
    await modelCard(page, renamed).click();
    await clickModelAction(page, "Delete model");
    await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
    await page.goto("/settings");
    await page.getByRole("button", { name: "Trash" }).click();
    await expect(page.getByText(renamed)).toBeVisible();
    await trashRow.getByRole("button", { name: "Delete", exact: true }).click();
    await page.getByRole("button", { name: "Delete forever" }).click();
    await expect(page.getByText(renamed)).toHaveCount(0);
  });
});
