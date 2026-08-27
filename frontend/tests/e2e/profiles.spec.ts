import { collectPageProblems, expect, installMockApiHooks, test } from "./helpers";

installMockApiHooks();

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
