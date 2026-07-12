import { test, expect } from "./helpers";
import { modelCard, uploadGcodeModel } from "./util";

// A revision row scoped by its "Rev N" label + the per-row Edit control.
function revRow(page: import("@playwright/test").Page, n: number) {
  return page
    .locator("div")
    .filter({ has: page.getByText(`Rev ${n}`, { exact: true }) })
    .filter({ has: page.getByTitle("Edit revision") })
    .last();
}

test("g-code revision workflow: auto-recommend, re-recommend, status, compare", async ({ page }) => {
  const name = `e2e-rev-${Date.now()}`;
  await uploadGcodeModel(page, name);
  await modelCard(page, name).click();
  await expect(page.getByRole("heading", { name })).toBeVisible();

  // The first uploaded G-code is auto-marked recommended.
  await page.getByRole("tab", { name: /Revisions/ }).click();
  await expect(page.getByText("Rev 1", { exact: true }).first()).toBeVisible();
  await expect(revRow(page, 1).getByText("Recommended")).toBeVisible();

  // Add a second revision (unique bytes so the content-hash dedupe keeps it).
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Add G-code revision" })).toBeVisible();
  await page.locator('input[accept=".gcode,.g,.gco"]').setInputFiles({
    name: "rev2.gcode",
    mimeType: "text/plain",
    buffer: Buffer.from(`; second revision for ${name}\nG28\nG1 Z0.2\n`),
  });
  await page.getByRole("button", { name: "Add revision" }).click();
  await expect(page.getByText("Rev 2", { exact: true }).first()).toBeVisible();

  // Promote Rev 2 to recommended + known-good; Rev 1 loses the marker. Only one
  // revision edits at a time, so the form's controls are unambiguous page-wide.
  await revRow(page, 2).getByTitle("Edit revision").click();
  await page.getByRole("checkbox").check();
  await page
    .getByRole("combobox")
    .filter({ has: page.getByRole("option", { name: "Known good" }) })
    .selectOption("known_good");
  await page.getByRole("button", { name: "Save" }).click();

  await expect(revRow(page, 2).getByText("Recommended")).toBeVisible();
  await expect(revRow(page, 1).getByText("Recommended")).toHaveCount(0);

  // With two revisions, the compare panel appears.
  await expect(page.getByRole("heading", { name: /Compare Revisions/ })).toBeVisible();
});
