/**
 * A filter that lives in the URL rather than in component state.
 *
 * Filters are URL-restorable so a user can share or bookmark a view, which means the
 * round trip is the contract: applying a filter writes the URL, loading that URL restores
 * the filter, and clearing it removes both. A filter that only lives in state looks
 * identical until somebody reloads.
 */
import { test, expect } from "./helpers";
import { modelCard, uploadGcodeModel, uploadModel } from "./util";

test.describe("structured filters", () => {
  test("artifact filter is URL-restorable and clearable", async ({ page }) => {
    const stamp = Date.now();
    const gcode = `e2e-filter-gcode-${stamp}`;
    const mesh = `e2e-filter-mesh-${stamp}`;

    await uploadGcodeModel(page, gcode);
    await uploadModel(page, mesh, { mesh: true, gcode: false });

    await page.goto("/");
    const sidebar = page.locator("aside");
    await expect(sidebar.getByText("Artifact", { exact: true })).toBeVisible();
    await sidebar.getByText("gcode", { exact: true }).click();
    await expect(page).toHaveURL(/file_type=gcode/);
    await expect(modelCard(page, gcode)).toBeVisible();
    await expect(modelCard(page, mesh)).toHaveCount(0);

    // A full reload preserves the canonical repeated-filter query and results.
    await page.reload();
    await expect(page).toHaveURL(/file_type=gcode/);
    await expect(modelCard(page, gcode)).toBeVisible();
    await expect(modelCard(page, mesh)).toHaveCount(0);

    await sidebar.getByRole("button", { name: /Clear 1/ }).click();
    await expect(page).not.toHaveURL(/file_type=/);
    await expect(modelCard(page, gcode)).toBeVisible();
    await expect(modelCard(page, mesh)).toBeVisible();
  });
});
