/**
 * Switching the interface language, and having it stay switched.
 *
 * A language choice is stored per user and applied before the first render, so the two
 * ways it breaks are both invisible in a unit test: strings that were never translated,
 * and a choice that does not survive a reload. This checks both in one pass.
 */
import { test, expect } from "./helpers";

// i18n (0.11.0): switching the locale translates the app's chrome — the
// Settings section nav and the profile menu's navigation links — and persists
// across a reload. `printstash.locale` lives in localStorage, shared across
// this worker's serial specs, so this test restores English when it's done.

test.describe("localization", () => {
  test("switching to Spanish translates settings navigation and survives a reload", async ({
    page,
  }) => {
    await page.goto("/settings");
    await expect(page.getByRole("button", { name: "Trash" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();

    await page.getByRole("button", { name: /^Language:/ }).click();
    await expect(page.locator("html")).toHaveAttribute("lang", "es");
    await expect(page.getByRole("heading", { name: "Ajustes" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Papelera" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Resumen" })).toBeVisible();

    // The profile menu's navigation links translate too.
    await page.locator('header button[data-menu-trigger][aria-haspopup="menu"]').click();
    await expect(page.getByRole("menuitem", { name: "Impresoras" })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "Ajustes" })).toBeVisible();
    await page.keyboard.press("Escape");

    await page.reload();
    await expect(page.getByRole("button", { name: "Papelera" })).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("lang", "es");

    // Restore English so specs that assert on English copy later in this worker
    // aren't affected.
    await page.getByRole("button", { name: /^Idioma:/ }).click();
    await expect(page.getByRole("button", { name: "Trash" })).toBeVisible();
  });
});
