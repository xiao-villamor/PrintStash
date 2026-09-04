/*
 * The profiles route renders the filament and printer presets we detected.
 *
 * Presets are derived from uploaded G-code rather than entered by hand, so an
 * empty page is ambiguous: it means either "you have uploaded nothing" or "the
 * detection stopped working". This asserts the second case cannot hide behind the
 * first.
 */
import { expect, test } from "@playwright/test";

import { collectPageProblems, useMockApi } from "./_setup";

useMockApi();

test.describe("profiles route", () => {
  test("profiles route renders detected filament and printer presets", async ({ page }) => {
    const problems = await collectPageProblems(page);

    await page.goto("/profiles");

    await expect(page.getByRole("heading", { name: "Profiles" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Filament presets" })).toBeVisible();
    await expect(page.getByLabel("Filament preset name 1")).toHaveValue("Generic PLA");
    await expect(page.getByLabel("Filament brand 1")).toHaveValue("Generic");
    await page.getByRole("tab", { name: /Printers/ }).click();
    await expect(page.getByRole("heading", { name: "Printer presets" })).toBeVisible();
    await expect(page.getByLabel("Printer preset name 1")).toHaveValue("Creality Ender-3 V3 SE");
    await expect(page.getByText("Failed to fetch")).toHaveCount(0);
    await expect(page.getByText("This page could not be found")).toHaveCount(0);
    expect(problems).toEqual([]);
  });
});
