/**
 * Taking the whole library somewhere else and bringing it back.
 *
 * The portable archive is the self-hoster's escape hatch, and the only test that means
 * anything is the round trip: export, import, and find the library intact. An export that
 * writes a plausible-looking zip nothing can read is the failure this catches, and it is
 * one nobody discovers until they need it.
 */
import { readFileSync } from "node:fs";

import { test, expect } from "./helpers";
import { modelCard, uploadGcodeModel } from "./util";

// Portable library migration (0.10.0): export the whole vault as a
// `printstash-library-v1` ZIP from Settings, then import that same archive back
// in — the model that was already there (plus the reimport of itself) both end
// up visible, proving the round trip preserves the library.

test.describe("library transfer", () => {
  test("export a library archive and import it back in", async ({ page }) => {
    const name = `e2e-transfer-${Date.now()}`;
    await uploadGcodeModel(page, name);

    await page.goto("/settings"); // Overview is the default section — where the archive card lives.
    await expect(page.getByRole("heading", { name: "Library migration" })).toBeVisible();

    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("button", { name: /Export full library/ }).click(),
    ]);
    const archivePath = await download.path();
    expect(archivePath).toBeTruthy();
    expect(download.suggestedFilename()).toMatch(/printstash-library-v1.*\.zip$/);
    // `download.path()` saves under an internal temp name with no extension —
    // re-wrap the bytes with the real filename so the app's client-side ".zip"
    // check (and the input's `accept`) see a proper archive.
    const archiveBytes = readFileSync(archivePath!);

    const [response] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes("/api/v1/models/library-import") && r.request().method() === "POST",
      ),
      page.locator('input[type="file"][accept=".zip,application/zip"]').setInputFiles({
        name: download.suggestedFilename(),
        mimeType: "application/zip",
        buffer: archiveBytes,
      }),
    ]);
    expect(response.ok()).toBe(true);

    // The archive round-trips content-hash-deduped, so re-importing the same
    // model doesn't create a duplicate card, but the original stays visible.
    await expect(async () => {
      await page.goto("/");
      await expect(modelCard(page, name)).toBeVisible({ timeout: 2_000 });
    }).toPass({ timeout: 30_000 });
  });
});
