/**
 * External roots are deliberately opt-in for real-browser coverage.
 *
 * Set PLAYWRIGHT_EXTERNAL_LIBRARY_ROOT to an existing directory shared by the
 * browser test process and the backend process. The test removes only the
 * PrintStash marker it created, enrolls the resulting legacy/unbound row, and
 * proves a subsequent upload is written back into that exact root. Without the
 * explicit environment path the suite reports this contract as skipped rather
 * than pretending a local directory is safe to use.
 */
import { access, rm } from "node:fs/promises";
import path from "node:path";

import { test, expect } from "./helpers";

const externalRoot = process.env.PLAYWRIGHT_EXTERNAL_LIBRARY_ROOT;
const markerName = ".printstash-external-root.json";

test.describe("mounted library source root recovery", () => {
  test("enrolls an unbound root before external write-back", async ({ page }) => {
    if (!externalRoot) {
      test.skip(
        true,
        "Set PLAYWRIGHT_EXTERNAL_LIBRARY_ROOT to an existing test-owned directory to run this contract.",
      );
      return;
    }
    const root = externalRoot;
    const name = `e2e-external-${Date.now()}`;
    const marker = path.join(root, markerName);
    let libraryId: number | null = null;

    try {
      const enable = await page.request.put("/api/v1/config", {
        data: { external_libraries_enabled: true },
      });
      expect(enable.ok()).toBe(true);

      const create = await page.request.post("/api/v1/libraries", {
        data: { name, root_path: root, scan_schedule: "", watch_mode: "off" },
      });
      expect(create.status()).toBe(201);
      const created = await create.json();
      libraryId = Number(created.id);

      await rm(marker, { force: true });
      const unbound = await page.request.get("/api/v1/libraries");
      expect(unbound.ok()).toBe(true);
      const listed = await unbound.json();
      expect(listed.find((library: { id: number }) => library.id === libraryId)).toMatchObject({
        binding_state: "unbound",
        root_enrollable: true,
        watch_active: false,
      });

      await page.goto("/settings?section=libraries");
      await expect(page.getByText("Needs enrollment")).toBeVisible();
      await page.getByRole("button", { name: "Review and enroll" }).click();
      const confirmation = page.getByRole("dialog", { name: "Enroll mounted source root?" });
      await expect(confirmation).toBeVisible();
      await expect(confirmation).toContainText(root);
      await confirmation.getByRole("button", { name: "Enroll root" }).click();
      await expect(page.getByText("Source verified")).toBeVisible();

      await page.goto("/");
      await page.getByRole("button", { name: "Upload", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Upload model" });
      await dialog.locator('input[accept=".gcode,.g,.gco,.bgcode"]').setInputFiles({
        name: `${name}.gcode`,
        mimeType: "text/plain",
        buffer: Buffer.from(`; external root test ${name}\nG28\n`),
      });
      await dialog.getByPlaceholder("e.g. Bracket v2").fill(name);
      const destination = dialog.getByRole("combobox").filter({ hasText: "Vault storage" });
      await destination.selectOption(String(libraryId));
      await dialog.getByRole("button", { name: /upload to vault/i }).click();
      await expect(dialog).toHaveCount(0);

      const written = path.join(root, `${name}.gcode`);
      await expect
        .poll(async () => {
          try {
            await access(written);
            return true;
          } catch {
            return false;
          }
        })
        .toBe(true);
    } finally {
      if (libraryId !== null) {
        await page.request.delete(`/api/v1/libraries/${libraryId}`);
      }
      await rm(path.join(root, `${name}.gcode`), { force: true });
      await rm(marker, { force: true });
      await page.request.put("/api/v1/config", {
        data: { external_libraries_enabled: false },
      });
    }
  });
});
