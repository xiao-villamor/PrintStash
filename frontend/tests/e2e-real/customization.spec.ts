import { test, expect } from "./helpers";

test("theme toggle flips and persists across reload", async ({ page }) => {
  await page.goto("/");
  const isDark = () => page.evaluate(() => document.documentElement.classList.contains("dark"));
  const before = await isDark();

  await page.getByRole("button", { name: "Toggle theme" }).first().click();
  await expect.poll(isDark).toBe(!before);

  await page.reload();
  await expect.poll(isDark).toBe(!before);
});

test("model metadata visibility toggle persists across reload", async ({ page }) => {
  await page.goto("/settings");
  await page.getByRole("button", { name: "Design" }).click();

  const chip = page.getByRole("button", { name: "Supports", exact: true });
  await expect(chip).toBeVisible();
  const before = await chip.getAttribute("aria-pressed");
  await chip.click();
  const after = await chip.getAttribute("aria-pressed");
  expect(after).not.toBe(before);

  await page.reload();
  await page.getByRole("button", { name: "Design" }).click();
  await expect(page.getByRole("button", { name: "Supports", exact: true })).toHaveAttribute(
    "aria-pressed",
    after!,
  );
});

test("model card metric slot selection persists, and reset restores defaults", async ({ page }) => {
  await page.goto("/settings");
  await page.getByRole("button", { name: "Design" }).click();

  // Option buttons are named "<label> <abbr>" (e.g. "Material MAT"); the first in
  // DOM is slot 1. Once selected there, only slot 1 keeps that label.
  const mat = () => page.getByRole("button", { name: "Material MAT", exact: true }).first();
  await mat().click();
  await expect(mat()).toHaveAttribute("aria-pressed", "true");

  await page.reload();
  await page.getByRole("button", { name: "Design" }).click();
  await expect(mat()).toHaveAttribute("aria-pressed", "true");

  // The card-metrics Reset (first Reset on the page) clears the selection.
  await page.getByRole("button", { name: "Reset" }).first().click();
  await expect(mat()).toHaveAttribute("aria-pressed", "false");
});
