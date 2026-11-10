/**
 * The whole life of a model, on the real backend.
 *
 * Upload → real ingestion → edit → trash → restore → purge, in one pass. Each step is
 * covered in isolation elsewhere; what this proves is that the states connect: a trashed
 * model really is invisible and really does come back, and a purge really is the end.
 * Deletion being recoverable is the promise the trash makes, and this is where it is kept.
 */
import { test, expect } from "./helpers";
import { clickModelAction, modelCard, openModelPage, uploadModel } from "./util";

// Full lifecycle on the REAL backend: upload G-code → real ingestion creates the
// model → edit it → soft-delete to trash → restore → purge forever.
test.describe("model library", () => {
  test(
    "@critical upload, edit, trash, restore, and purge a model",
    { tag: "@pr-smoke" },
    async ({ page }) => {
      await page.setViewportSize({ width: 1920, height: 1080 });
      const name = `e2e-model-${Date.now()}`;
      const quickName = `${name}-quick`;
      const tag = `${name}-tag`;
      const renamed = `${name}-renamed`;

      // ── Upload ────────────────────────────────────────────────────────────────
      await uploadModel(page, name, { gcode: true, tag });

      // ── Quick rename keeps the existing Model and its tags ────────────────────
      await openModelPage(page, name);
      const modelUrl = page.url();
      await page.getByRole("button", { name: `Rename ${name}`, exact: true }).click();
      const titleEditor = page.getByRole("dialog", { name: `Rename ${name}`, exact: true });
      await expect(
        page.getByRole("button", { name: `Edit tags for ${name}`, exact: true }),
      ).toBeVisible();
      await expect(page.getByRole("button", { name: "Favorite", exact: true })).toBeVisible();
      await titleEditor.getByRole("textbox", { name: "Model name", exact: true }).fill(quickName);
      await titleEditor.getByRole("button", { name: "Save", exact: true }).click();
      await expect(titleEditor).toHaveCount(0);
      await expect(page.getByRole("heading", { name: quickName, exact: true })).toBeVisible();
      await page.reload();
      await expect(page).toHaveURL(modelUrl);
      await expect(page.getByRole("heading", { name: quickName, exact: true })).toBeVisible();
      await expect(page.getByText(tag, { exact: true })).toBeVisible();

      // ── Edit ──────────────────────────────────────────────────────────────────
      await clickModelAction(page, "Edit details");
      const nameInput = page.getByRole("textbox", { name: "Name (required)" });
      await nameInput.fill(renamed);
      await page.getByRole("button", { name: "Save changes", exact: true }).click();
      await expect(page.getByRole("heading", { name: renamed })).toBeVisible();

      // ── Soft-delete to trash ────────────────────────────────────────────────────
      await clickModelAction(page, "Delete model");
      await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click(); // confirm
      await expect(modelCard(page, renamed)).toHaveCount(0);

      // ── Restore from Settings → Trash ───────────────────────────────────────────
      await page.goto("/settings?section=trash");
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
      await openModelPage(page, renamed);
      await clickModelAction(page, "Delete model");
      await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
      await page.goto("/settings?section=trash");
      await expect(page.getByText(renamed)).toBeVisible();
      await trashRow.getByRole("button", { name: "Delete", exact: true }).click();
      await page.getByRole("button", { name: "Delete forever" }).click();
      await expect(page.getByText(renamed)).toHaveCount(0);
    },
  );
});
