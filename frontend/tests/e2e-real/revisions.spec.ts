/**
 * The recommended G-code revision, through the UI that sets it.
 *
 * A model with G-code has exactly one recommended revision, and this is the flow where
 * that invariant is most easily broken: the first upload is auto-recommended, marking
 * another demotes it, and a status change must not quietly move it. All four steps run in
 * one pass because it is the transitions, not the states, that go wrong.
 */
import { test, expect } from "./helpers";
import { openModelPage, uploadGcodeModel } from "./util";

// The compact table keeps one row group per Revision; its selected actionbar owns editing.
function revRow(page: import("@playwright/test").Page, n: number) {
  return page
    .locator("tbody")
    .filter({ has: page.getByRole("button", { name: new RegExp(`^Rev ${n}\\b`) }) });
}

test.describe("revisions", () => {
  test("g-code revision workflow: auto-recommend, re-recommend, status, compare", async ({
    page,
  }) => {
    const name = `e2e-rev-${Date.now()}`;
    await uploadGcodeModel(page, name);
    await openModelPage(page, name);
    await expect(page.getByRole("heading", { name })).toBeVisible();

    // The first uploaded G-code is auto-marked recommended.
    await page.getByRole("button", { name: /Recommended revision.*Rev 1/ }).press("Enter");
    const revisions = page.getByRole("tabpanel", { name: "Revisions" });
    await expect(revisions).toBeFocused();
    await expect(revisions.getByRole("heading", { name: /Rev 1.*Recommended/ })).toBeVisible();

    // Add a second revision (unique bytes so the content-hash dedupe keeps it).
    await page.getByRole("button", { name: "Add revision", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Add G-code revision" })).toBeVisible();
    await page.locator('input[accept=".gcode,.g,.gco,.bgcode,.gcode.3mf"]').setInputFiles({
      name: "rev2.gcode",
      mimeType: "text/plain",
      buffer: Buffer.from(`; second revision for ${name}\nG28\nG1 Z0.2\n`),
    });
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Add revision", exact: true })
      .click();
    await expect(page.getByText("Rev 2", { exact: true }).first()).toBeVisible();

    // Promote Rev 2 to recommended + known-good; Rev 1 loses the marker. Only one
    // revision edits at a time, so the form's controls are unambiguous page-wide.
    await revRow(page, 2)
      .getByRole("button", { name: /^Rev 2/ })
      .click();
    await page.getByRole("button", { name: "Edit revision", exact: true }).click();
    const editor = page.getByRole("dialog", { name: "Edit revision", exact: true });
    await editor
      .getByRole("radio", { name: "Make this Revision recommended", exact: true })
      .check();
    await editor.getByRole("radio", { name: "Known good", exact: true }).check();
    await editor.getByRole("button", { name: "Save Revision", exact: true }).click();

    await expect(revRow(page, 2).getByText("Recommended")).toBeVisible();
    await expect(revRow(page, 1).getByText("Recommended")).toHaveCount(0);

    await expect(revisions.getByRole("heading", { name: /Rev 2.*Known good/ })).toBeVisible();

    await page.getByRole("tab", { name: "Overview", exact: true }).click();
    await page.getByRole("button", { name: /Recommended revision.*Rev 2/ }).click();
    await expect(revisions.getByRole("heading", { name: /^Rev 2/ })).toBeVisible();

    // Comparison is an explicit command in the compact Revisions toolbar.
    await page.getByRole("button", { name: "Compare", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "Compare revisions", exact: true }),
    ).toBeVisible();
  });
});
