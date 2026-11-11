/*
 * An app-store installation reaches its first Model without the setup wizard.
 *
 * The backend starts with VAULT_SETUP_ADMIN_* set and browser registration turned
 * off, as an Unraid or Runtipi install form leaves it. The owner signs in with the
 * provisioned credentials, is sent to the storage step nobody has completed yet,
 * chooses storage, and uploads. If any hand-off regresses, the owner either lands
 * in a library with no storage or cannot get past sign-in.
 */
import { test, expect } from "@playwright/test";
import { gcodeFor } from "../util";

test.describe("Environment-provisioned owner", () => {
  test("reaches a first Model after choosing storage", async ({ page }, testInfo) => {
    // ── Sign in with the credentials the install form provided ──
    await page.goto("/login");
    await page.getByLabel("Username").fill("store-owner");
    await page.getByLabel("Password", { exact: true }).fill("StoreFormPassword123");
    await page.getByRole("button", { name: "Sign in", exact: true }).press("Enter");

    // ── The gate sends the owner to the storage step ──
    await expect(page).toHaveURL(/\/getting-started$/);
    await expect(page.getByRole("heading", { name: "Your files", exact: true })).toBeVisible();
    await expect(page.getByLabel("Your files", { exact: true })).not.toHaveValue("");
    await page.screenshot({ path: testInfo.outputPath("choose-storage.png"), fullPage: true });

    // ── Any other page still leads back there until storage is chosen ──
    await page.goto("/settings");
    await expect(page).toHaveURL(/\/getting-started$/);

    // ── Choosing storage finishes setup ──
    await page.getByRole("button", { name: "Use this storage" }).press("Enter");
    await expect(page.getByRole("button", { name: "Upload my first files" })).toBeVisible();

    // ── The first upload lands in the library ──
    await page.getByRole("button", { name: "Upload my first files" }).press("Enter");
    await page.getByLabel("Model or G-code file").setInputFiles({
      name: "store-model.gcode",
      mimeType: "text/plain",
      buffer: Buffer.from(gcodeFor("Store model")),
    });
    await page.getByPlaceholder("e.g. Bracket v2").fill("Store model");
    await page.getByRole("button", { name: "Add to my library" }).press("Enter");
    await expect(
      page.getByRole("heading", { name: "Your models are ready to explore" }),
    ).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("first-model.png"), fullPage: true });

    // ── Storage no longer blocks the rest of the app ──
    await page.goto("/settings");
    await expect(page.getByRole("heading", { name: "Settings", exact: true })).toBeVisible();
  });
});
