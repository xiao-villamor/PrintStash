/**
 * The whole life of a model, on the real backend.
 *
 * Upload → real ingestion → edit → trash → restore → purge, in one pass. Each step is
 * covered in isolation elsewhere; what this proves is that the states connect: a trashed
 * model really is invisible and really does come back, and a purge really is the end.
 * Deletion being recoverable is the promise the trash makes, and this is where it is kept.
 */
import { test, expect } from "./helpers";
import {
  clickModelAction,
  collapseNavigationSidebar,
  modelCard,
  openModelPage,
  uploadModel,
} from "./util";
import { settleLibraryRead } from "../e2e/_library";
import { selectModelDetailTab } from "../model-detail-navigation";

// Full lifecycle on the REAL backend: upload G-code → real ingestion creates the
// model → edit it → soft-delete to trash → restore → purge forever.
test.describe("model library", () => {
  test("persists a failed print record with an unlisted printer", async ({ page }) => {
    const name = `e2e-print-record-${Date.now()}`;
    const printer = `Workshop ${name}`;
    await uploadModel(page, name);
    await collapseNavigationSidebar(page);
    await openModelPage(page, name);
    const modelPath = new URL(page.url()).pathname;

    try {
      await selectModelDetailTab(page, /Activity/);
      await page.getByRole("button", { name: "Record result", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Record result" });
      await dialog.getByRole("button", { name: "Printer", exact: true }).click();
      await page.getByRole("button", { name: "Other (not listed)…", exact: true }).click();
      await dialog.getByRole("textbox", { name: "Printer name" }).fill(printer);
      await dialog.getByRole("radio", { name: "Failed", exact: true }).check();
      await dialog.getByRole("textbox", { name: "What went wrong" }).fill("Lifted corner");
      const saved = page.waitForResponse(
        (response) =>
          response.url().endsWith(`${modelPath}/print-jobs`) &&
          response.request().method() === "POST",
      );
      await dialog.getByRole("button", { name: "Save record", exact: true }).click();
      expect((await saved).ok()).toBe(true);
      await expect(dialog).toHaveCount(0);
      await page.reload();
      await expect(page.getByText(printer, { exact: false })).toBeVisible();
      await expect(page.getByText("Lifted corner", { exact: true })).toBeVisible();
    } finally {
      expect((await page.request.delete(`/api/v1${modelPath}`)).ok()).toBe(true);
    }
  });

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

      // Resolve the uploaded revision through the existing API from the card.
      const card = modelCard(page, name).locator("xpath=ancestor::article");
      const download = page.waitForEvent("download");
      await card.hover();
      await card.getByRole("button", { name: "Download Rev 1", exact: true }).click();
      expect((await download).suggestedFilename()).toContain(name);
      await card.hover();
      const print = card.getByRole("button", { name: "Print Rev 1", exact: true });
      await expect(print.locator("svg")).toHaveClass(/\blucide-printer-3d\b/);
      await print.click();
      await expect(
        page.getByRole("dialog", { name: "Send Rev 1 to printer", exact: true }),
      ).toBeVisible();
      await page.keyboard.press("Escape");

      // ── Quick rename keeps the existing Model and its tags ────────────────────
      await openModelPage(page, name);
      const modelUrl = page.url();
      await page.getByRole("button", { name: `Rename ${name}`, exact: true }).click();
      const titleEditor = page.getByRole("textbox", { name: "Model name", exact: true });
      await expect(
        page.getByRole("button", { name: `Edit tags for ${name}`, exact: true }),
      ).toBeVisible();
      await expect(
        page.getByRole("button", { name: `Add ${name} to favorites`, exact: true }),
      ).toBeVisible();
      await titleEditor.fill(quickName);
      await titleEditor.press("Enter");
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
      const trash = page.getByRole("group", { name: "Deleted models and multipart sets" });
      const trashedModel = trash.getByText(renamed, { exact: true });
      await expect(trashedModel).toBeVisible();
      const trashRow = trash
        .getByRole("checkbox", { name: `Select deleted model ${renamed}`, exact: true })
        .locator("..");
      await trashRow.getByRole("button", { name: "Restore", exact: true }).click();
      await expect(trashedModel).toHaveCount(0);

      // Back in the library.
      await page.goto("/");
      await settleLibraryRead(page, modelCard(page, renamed));

      // ── Delete + purge forever (cleanup) ────────────────────────────────────────
      await openModelPage(page, renamed);
      await clickModelAction(page, "Delete model");
      await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
      await page.goto("/settings?section=trash");
      await expect(trashedModel).toBeVisible();
      await trashRow.getByRole("button", { name: "Delete", exact: true }).click();
      await page.getByRole("button", { name: "Delete forever" }).click();
      await expect(trashedModel).toHaveCount(0);
    },
  );
});
