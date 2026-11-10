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
    await expect(page.getByRole("tab", { name: /Filaments/ })).toBeVisible();
    await expect(page.getByLabel("Filament preset name 1")).toHaveCount(0);
    await expect(page.getByRole("button", { name: /Generic PLA/ }).first()).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect(page.getByRole("region", { name: "Settings for Generic PLA" })).toBeVisible();
    await page.getByRole("button", { name: "Edit profile Generic PLA" }).click();
    await expect(page.getByLabel("Filament preset name 1")).toHaveValue("Generic PLA");
    await expect(page.getByLabel("Filament brand 1")).toHaveValue("Generic");
    await page.getByRole("dialog").getByRole("button", { name: "Close" }).click();
    await page.getByRole("tab", { name: /Printers/ }).click();
    await expect(page.getByLabel("Printer preset name 1")).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: /Creality Ender-3 V3 SE/ }).first(),
    ).toHaveAttribute("aria-pressed", "true");
    await expect(
      page.getByRole("region", { name: "Settings for Creality Ender-3 V3 SE" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Edit profile Creality Ender-3 V3 SE" }).click();
    await expect(page.getByLabel("Printer preset name 1")).toHaveValue("Creality Ender-3 V3 SE");
    await expect(page.getByText("Failed to fetch")).toHaveCount(0);
    await expect(page.getByText("This page could not be found")).toHaveCount(0);
    expect(problems).toEqual([]);
  });
});
