/** Printer setup and servicing decisions persist across reloads in the real printer workspace. */
import { test, expect } from "./helpers";
import { collapseNavigationSidebar } from "./util";

const port = Number(process.env.PLAYWRIGHT_MOCK_PRINTER_PORT ?? 7530);

test.describe("Printer service workspace", () => {
  test("persists the service workspace settings across reload", async ({ page }, testInfo) => {
    const name = `e2e-printer-sections-${Date.now()}`;
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto("/printers");
    await collapseNavigationSidebar(page);
    await page.getByRole("button", { name: "Add printer" }).click();
    const setup = page.getByRole("dialog", { name: "Add printer", exact: true });
    await setup.getByLabel("Name", { exact: true }).fill(name);
    await setup.getByPlaceholder("http://printer.local:7125").fill(`http://127.0.0.1:${port}`);
    await setup.getByRole("button", { name: "Add printer" }).click();
    const link = page.getByRole("link", { name, exact: true });
    await expect(link).toBeVisible();
    const href = await link.getAttribute("href");
    if (!href) throw new Error("The printer link has no URL");
    const pathname = new URL(href, page.url()).pathname;

    try {
      await page.goto(`${pathname}?tab=maintenance`);
      const maintenance = page.getByRole("region", { name: "Maintenance", exact: true });
      const materials = page.getByRole("region", { name: "Materials & tools", exact: true });
      await expect(page.getByRole("tab", { name: "Materials & maintenance" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
      await expect(materials.getByRole("button", { name: "Add feed" })).toBeVisible();
      await expect(maintenance.getByRole("button", { name: "Schedule window" })).toBeVisible();
      await maintenance.getByRole("button", { name: "Soft drain", exact: true }).click();
      await expect(
        maintenance.getByRole("button", { name: "Resume routing", exact: true }),
      ).toBeVisible();
      await page.reload();
      await expect(
        maintenance.getByRole("button", { name: "Resume routing", exact: true }),
      ).toBeVisible();
      await expect(materials.getByRole("button", { name: "Add feed" })).toBeVisible();
      await expect(maintenance.getByText("No service entries recorded.")).toBeVisible();
      await page.screenshot({
        path: testInfo.outputPath("moonraker-materials-maintenance-light.png"),
      });

      await page.getByRole("tab", { name: "Settings", exact: true }).click();
      const notes = page.getByRole("textbox", { name: "Notes", exact: true });
      await notes.fill("Draft only");
      await page.getByRole("button", { name: "Discard changes", exact: true }).click();
      await expect(notes).toHaveValue("");
      await expect(page.getByText("Changes discarded", { exact: true })).toBeVisible();
      await notes.fill("Service inspection passed");
      await page.getByRole("button", { name: "Save changes", exact: true }).click();
      await expect(page.getByText("Saved", { exact: true })).toBeVisible();
      await page.reload();
      await expect(notes).toHaveValue("Service inspection passed");
      await page.screenshot({ path: testInfo.outputPath("moonraker-settings-light.png") });

      await page.emulateMedia({ colorScheme: "dark" });
      await page.getByRole("button", { name: "Connection checks", exact: true }).click();
      const diagnostics = page.getByRole("region", { name: "Connection checks" });
      await expect(diagnostics.getByRole("table")).toBeVisible();
      await diagnostics.getByRole("button", { name: "Run checks" }).click();
      await expect(diagnostics.getByText(/Last checked/)).toBeVisible();
      await page.screenshot({ path: testInfo.outputPath("moonraker-diagnostics-dark.png") });
      await page.goBack();
      await expect(notes).toHaveValue("Service inspection passed");
      await page.getByRole("button", { name: "Manage maintenance", exact: true }).click();
      await expect(
        maintenance.getByRole("button", { name: "Resume routing", exact: true }),
      ).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
        1920,
      );
    } finally {
      expect((await page.request.delete(`/api/v1${pathname}`)).ok()).toBe(true);
    }
  });
});
