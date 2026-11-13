/**
 * The display choices a user makes once and expects to keep.
 *
 * Metadata visibility and card metric slots are stored per user and read on every grid
 * render, so the failure mode is quiet: the setting appears to save, and comes back
 * wrong after a reload. Every case here therefore reloads the page before asserting.
 */
import { test, expect } from "./helpers";

test.describe("appearance customization", () => {
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

  test("model card metric slot selection persists, and reset restores defaults", async ({
    page,
  }) => {
    await page.goto("/settings?section=design");

    const first = () => page.getByRole("combobox", { name: "Metric 1", exact: true });
    await first().selectOption("material");
    await expect(first()).toHaveValue("material");

    await page.reload();
    await expect(first()).toHaveValue("material");

    await page.getByRole("button", { name: "Reset metrics", exact: true }).click();
    await expect(first()).not.toHaveValue("material");
  });
});
