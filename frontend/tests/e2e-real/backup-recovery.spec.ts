/**
 * A backup is only useful when the browser can restore real catalog rows and Artifact bytes.
 *
 * This deliberately destroys a fully ingested Model after taking a backup, restores through
 * the operator UI, and reads the recovered bytes through the public download endpoint.
 */
import { test, expect } from "./helpers";
import { clickModelAction, gcodeFor, modelCard, uploadGcodeModel } from "./util";

test.describe("backup recovery", () => {
  test(
    "@critical restores a purged model with its Artifact bytes",
    { tag: "@critical" },
    async ({ page }) => {
      const name = `e2e-backup-recovery-${Date.now()}`;
      const expectedBytes = Buffer.from(gcodeFor(name));

      // ── Persist a real Artifact ─────────────────────────────────────────────
      await uploadGcodeModel(page, name);
      const listing = await page.request.get("/api/v1/models");
      expect(listing.ok()).toBeTruthy();
      const model = (await listing.json()).find((item: { name: string }) => item.name === name);
      expect(model).toBeTruthy();
      const modelId = Number(model.id);

      // ── Create a restorable backup through the operator UI ─────────────────
      await page.goto("/settings?section=backup");
      const created = page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/backups") && response.request().method() === "POST",
      );
      await page.getByRole("button", { name: "Backup now" }).click();
      const metadata = await (await created).json();
      const backupRow = page.locator("div.grid").filter({ hasText: metadata.backup_id }).last();
      await expect(backupRow.getByRole("button", { name: "Restore", exact: true })).toBeVisible();

      // ── Remove the catalog row and owned bytes ──────────────────────────────
      await page.goto("/");
      await modelCard(page, name).click();
      await clickModelAction(page, "Delete model");
      await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
      await page.goto("/settings?section=trash");
      await expect(page.getByText(name)).toBeVisible();
      await page.getByRole("button", { name: "Delete", exact: true }).click();
      await page.getByRole("button", { name: "Delete forever" }).click();
      await expect(page.getByText(name)).toHaveCount(0);
      expect((await page.request.get(`/api/v1/models/${modelId}`)).status()).toBe(404);

      // ── Restore through the real UI and prove the bytes came back ───────────
      await page.goto("/settings?section=backup");
      const restored = page.waitForResponse(
        (response) =>
          response.url().includes(`/api/v1/backups/${metadata.backup_id}/restore`) &&
          response.request().method() === "POST",
      );
      await page
        .locator("div.grid")
        .filter({ hasText: metadata.backup_id })
        .last()
        .getByRole("button", { name: "Restore", exact: true })
        .click();
      await page
        .getByRole("dialog", { name: "Restore backup?" })
        .getByRole("button", { name: "Restore", exact: true })
        .click();
      expect((await restored).status()).toBe(200);

      await expect
        .poll(async () => (await page.request.get(`/api/v1/models/${modelId}`)).status())
        .toBe(200);
      const detail = await (await page.request.get(`/api/v1/models/${modelId}`)).json();
      expect(detail.name).toBe(name);
      expect(detail.files).toHaveLength(1);
      const download = await page.request.get(`/api/v1/files/${detail.files[0].id}/download`);
      expect(download.ok()).toBeTruthy();
      expect(await download.body()).toEqual(expectedBytes);

      // ── Leave the shared real-E2E database clean ────────────────────────────
      expect((await page.request.delete(`/api/v1/models/${modelId}`)).status()).toBe(204);
      expect((await page.request.delete(`/api/v1/models/${modelId}/purge`)).status()).toBe(200);
      const source = new URLSearchParams({ source_ref: metadata.source_ref });
      expect(
        (await page.request.delete(`/api/v1/backups/${metadata.backup_id}?${source}`)).ok(),
      ).toBeTruthy();
    },
  );
});
