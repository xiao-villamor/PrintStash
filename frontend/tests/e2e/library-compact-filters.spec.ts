/**
 * The shared filter picker keeps URL selections, keyboard return, and the
 * browsing surface stable while choices load or the viewport changes.
 */
import { expect, test, type Page } from "@playwright/test";
import { aPrinter } from "../../src/test-support/factories";
import { collectPageProblems, useMockApi } from "./_setup";

useMockApi();

async function openFilters(page: Page) {
  await page
    .getByRole("button", { name: "Filters", exact: true })
    .filter({ visible: true })
    .click();
  const picker = page.getByRole("dialog", { name: "Filter Models", exact: true });
  await expect(picker).toBeVisible();
  return picker;
}

test.describe("compact Library filters", () => {
  test.use({ viewport: { width: 1920, height: 1080 } });

  test("uses mutually exclusive checkbox choices for Printer", async ({ page }, testInfo) => {
    await page.addInitScript(() => localStorage.setItem("printstash.theme", "dark"));
    await page.route("**/api/v1/printers", (route) =>
      route.fulfill({ json: [aPrinter({ id: 7, name: "Workshop printer" })] }),
    );
    await page.goto("/");
    const menu = await openFilters(page);
    await menu.getByRole("button", { name: "Printer", exact: true }).click();
    await menu.getByRole("checkbox", { name: "Vault only", exact: true }).check();
    await expect(page).toHaveURL(/printer_presence=none/);
    await expect(menu.getByRole("checkbox", { name: "Vault only", exact: true })).toBeChecked();
    await menu.getByRole("checkbox", { name: "Workshop printer", exact: true }).check();
    await expect(page).toHaveURL(/printer_id=7/);
    await expect(page).not.toHaveURL(/printer_presence=/);
    await expect(menu.getByRole("checkbox", { checked: true })).toHaveCount(1);
    await menu.screenshot({
      path: testInfo.outputPath("printer-shared-choices.png"),
      animations: "disabled",
    });
    await menu.getByRole("checkbox", { name: "On a printer", exact: true }).check();
    await expect(page).toHaveURL(/printer_presence=any/);
    await expect(page).not.toHaveURL(/printer_id=/);
    await menu.getByRole("checkbox", { name: "On a printer", exact: true }).uncheck();
    await expect(page).not.toHaveURL(/printer_presence=/);
    await expect(menu.getByRole("checkbox", { checked: true })).toHaveCount(0);
  });

  test("uses mutually exclusive quick choices for printed status", async ({ page }) => {
    await page.goto("/?printed=no");
    const menu = await openFilters(page);
    const printed = menu.getByRole("group", { name: "Printed", exact: true });
    await expect(printed.getByRole("button", { name: "Not printed", exact: true })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await printed.getByRole("button", { name: "Printed", exact: true }).click();
    await expect(page).toHaveURL(/printed=yes/);
    await expect(printed.getByRole("button", { name: "Not printed", exact: true })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    await printed.getByRole("button", { name: "Any", exact: true }).click();
    await expect(page).not.toHaveURL(/printed=/);
  });

  test("keeps printed counts on searched choices", async ({ page }) => {
    await page.route("**/api/v1/models/facets**", (route) =>
      route.fulfill({
        json: {
          file_type: [],
          material_type: [],
          slicer_name: [],
          printer_model: [],
          revision_status: [],
          print_outcome: [],
          storage: [],
          printed: [
            { value: "yes", count: 0 },
            { value: "no", count: 3 },
          ],
        },
      }),
    );
    await page.goto("/?printed=no");
    const menu = await openFilters(page);
    await menu.getByRole("searchbox").fill("printed");
    await expect(menu.getByRole("checkbox", { name: "Not printed", exact: true })).toBeChecked();
    await expect(menu.getByText("0", { exact: true })).toBeVisible();
  });

  test("preserves selected filters while their values load", async ({ page }) => {
    const pending = Promise.withResolvers<void>();
    await page.route("**/api/v1/models/facets**", async (route) => {
      await pending.promise;
      await route.fulfill({
        json: {
          file_type: [{ value: "3mf", count: 7 }],
          material_type: [],
          slicer_name: [],
          printer_model: [],
          revision_status: [],
          print_outcome: [],
          storage: [],
          printed: [],
        },
      });
    });
    try {
      await page.goto("/?file_type=3mf");
      const menu = await openFilters(page);
      await expect(menu.getByRole("status")).toHaveText("Loading filter values…");
      await menu.getByRole("button", { name: /^File type/ }).click();
      const choice = menu.getByRole("checkbox", { name: "3MF", exact: true });
      await expect(choice).toBeChecked();
      await expect(choice).toBeEnabled();
      pending.resolve();
      await expect(menu.getByText("Loading filter values…")).toHaveCount(0);
      await expect(choice).toBeChecked();
      await expect(menu.getByText("7", { exact: true })).toBeVisible();
      await choice.uncheck();
      await expect(page).not.toHaveURL(/file_type=/);
    } finally {
      pending.resolve();
    }
  });

  test("keeps date controls above a stable browsing surface on 1080p", async ({ page }) => {
    const problems = await collectPageProblems(page);
    await page.goto(
      "/?file_type=3mf&printed=no&storage=vault&uploaded_after=2026-01-01&uploaded_before=2026-12-31",
    );
    const model = page.getByRole("main").locator("[data-library-entry]").first();
    await expect(model).toBeVisible();
    const before = await model.boundingBox();
    const menu = await openFilters(page);
    await menu.getByRole("button", { name: /^Uploaded/ }).click();
    await expect(menu.getByLabel("After", { exact: true })).toHaveValue("2026-01-01");
    await expect(menu.getByLabel("Before", { exact: true })).toHaveValue("2026-12-31");
    expect(await model.boundingBox()).toEqual(before);
    await page.keyboard.press("Escape");
    await expect(menu).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "Filters", exact: true }).filter({ visible: true }),
    ).toBeFocused();
    await expect(page.getByTitle("Remove File type: 3MF", { exact: true })).toBeVisible();
    expect(await model.boundingBox()).toEqual(before);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    expect(problems).toEqual([]);
  });

  test("keeps active chips after closing the picker", async ({ page }) => {
    await page.goto("/?file_type=3mf&uploaded_after=2026-01-01");
    const menu = await openFilters(page);
    await menu.getByRole("button", { name: "Close", exact: true }).click();
    await expect(menu).toHaveCount(0);
    await expect(page.getByTitle("Remove File type: 3MF", { exact: true })).toBeVisible();
    await expect(page).toHaveURL(/file_type=3mf/);
  });

  test("fits date controls within a narrow desktop surface", async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 768 });
    await page.goto("/?file_type=3mf&uploaded_after=2026-01-01&printed_after=2026-01-01");
    const menu = await openFilters(page);
    await menu.getByRole("button", { name: /^Uploaded/ }).click();
    await expect(menu.getByLabel("After", { exact: true })).toBeVisible();
    await menu.getByRole("button", { name: "Back", exact: true }).click();
    await menu.getByRole("button", { name: /^Print history/ }).click();
    await expect(menu.getByLabel(/Finished on or after/)).toBeVisible();
    await expect(menu.getByLabel("After", { exact: true })).toHaveCount(0);
    const box = (await menu.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(1024);
    expect(await menu.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  });

  test("keeps filter choices reachable on a phone", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    const menu = await openFilters(page);
    await menu.getByRole("button", { name: "Uploaded", exact: true }).click();
    await expect(menu.getByLabel("After", { exact: true })).toBeInViewport();
    const box = (await menu.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(390);
  });
});
