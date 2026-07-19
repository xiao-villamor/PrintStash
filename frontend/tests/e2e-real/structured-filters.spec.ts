import { test, expect } from "./helpers";
import { modelCard, uploadGcodeModel, uploadModel } from "./util";

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
