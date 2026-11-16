/*
 * The printer list and one printer's detail page, through the frontend proxy.
 *
 * Both go through the proxy rather than straight at the API, and that is what is
 * under test: a proxy path that stops resolving gives an empty fleet with no
 * error, which reads as "no printers configured" to a user who has several.
 *
 * The detail route is parametrized, so the dynamic id has to survive the render.
 * An id that arrives as `NaN` produces a page that loads, looks right, and shows
 * live status for nothing.
 */
import { expect, test } from "@playwright/test";

import { collectPageProblems, useMockApi } from "./_setup";

useMockApi();

test.describe("printer routes", () => {
  test("printer list route loads configured printers through the frontend proxy", async ({
    page,
  }) => {
    const problems = await collectPageProblems(page);

    await page.goto("/printers");

    await expect(page.getByRole("heading", { name: "Printers" })).toBeVisible();
    await expect(page.getByRole("link", { name: /ender/i })).toBeVisible();
    await expect(page.getByText(/^Moonraker(?: ·.*)?$/)).toBeVisible();
    await expect(page.getByText("ready", { exact: true }).first()).toBeVisible();
    await expect(page.getByLabel("Fleet summary").getByText("Ready")).toBeVisible();
    await expect(page.getByText("No printers configured yet.")).toHaveCount(0);
    await expect(page.getByText("Failed to fetch")).toHaveCount(0);
    await expect(page.getByText("This page could not be found")).toHaveCount(0);
    expect(problems).toEqual([]);
  });

  test("printer detail route preserves the dynamic id and renders live status", async ({
    page,
  }) => {
    const problems = await collectPageProblems(page);

    await page.goto("/printers/3");

    await expect(page.getByRole("heading", { name: "ender" })).toBeVisible();
    await expect(page.getByText(/^Moonraker(?: ·.*)?$/)).toBeVisible();
    await expect(page.getByText("ready", { exact: true }).first()).toBeVisible();
    await expect(page.getByRole("heading", { name: "Temperatures", exact: true })).toBeVisible();
    await page.getByRole("tab", { name: "Settings" }).click();
    await expect(page.getByLabel("Name", { exact: true })).toBeVisible();
    const printerName = page.getByLabel("Name");
    await printerName.fill("Workshop printer");
    await page.locator("form").getByRole("button", { name: "Save changes", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Workshop printer" })).toBeVisible();
    await page.getByRole("tab", { name: "Files" }).click();
    await expect(page.getByText("skadis_kitchen-roll_screw_PLA_30m12s.gcode")).toBeVisible();
    await expect(page.getByText("Failed to fetch")).toHaveCount(0);
    await expect(page.getByText("This page could not be found")).toHaveCount(0);
    await expect(page).toHaveURL(/\/printers\/3$/);

    const html = await page.content();
    expect(html).not.toContain('printerId":"$NaN');
    expect(problems).toEqual([]);
  });

  test("an operator can resolve a stale active job from the queue", async ({ page }) => {
    const problems = await collectPageProblems(page);

    await page.goto("/printers");
    await page.getByRole("tab", { name: "Queue" }).click();
    await page
      .getByRole("combobox", { name: "View", exact: true })
      .selectOption({ label: "Active" });
    await expect(page.getByText("interrupted-part.gcode")).toBeVisible();
    await page.getByRole("button", { name: "Resolve" }).click();

    const dialog = page.getByRole("dialog", { name: "Resolve stale job" });
    await expect(dialog).toContainText("does not send a command to the printer");
    await dialog.getByRole("button", { name: "Mark failed" }).click();

    await page
      .getByRole("combobox", { name: "View", exact: true })
      .selectOption({ label: "Recent" });
    await expect(page.getByRole("region", { name: "Recent print jobs" })).toBeVisible();
    await expect(
      page.getByRole("region", { name: "Recent print jobs" }).getByText(/ · failed$/),
    ).toBeVisible();
    expect(problems).toEqual([]);
  });
});
