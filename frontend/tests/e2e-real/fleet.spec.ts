/**
 * A queued print reaching a real printer and coming back.
 *
 * The fleet and printer Status flows share a live Moonraker emulator to verify
 * routing, telemetry, persisted job history, and operator controls.
 */
import { test, expect } from "./helpers";
import { collapseNavigationSidebar, modelCard, openModelPage, uploadGcodeModel } from "./util";

// Fleet (0.11.0): a real, live Moonraker emulator (backend/tests/fakes/
// mock_printer.py, booted standalone by playwright.real.config.ts's webServer
// on :7530) comes online after being added, accepts a queued print through the
// send dialog, and shows up in the Queue / Maintenance views. Drain is toggled
// on and off from Maintenance.

const MOCK_PRINTER_PORT = Number(process.env.PLAYWRIGHT_MOCK_PRINTER_PORT ?? 7530);

test.describe("fleet queue", () => {
  test("an emulated Moonraker printer comes online, accepts a queued print, and drains", async ({
    page,
  }) => {
    const stamp = Date.now();
    const printerName = `e2e-fleet-${stamp}`;
    const modelName = `e2e-fleet-model-${stamp}`;

    // Add the printer pointed at the live emulator.
    await page.goto("/printers");
    await page.getByRole("button", { name: "Add printer" }).click();
    await page
      .getByRole("dialog", { name: "Add printer", exact: true })
      .getByLabel("Name", { exact: true })
      .fill(printerName);
    await page
      .getByPlaceholder("http://printer.local:7125")
      .fill(`http://127.0.0.1:${MOCK_PRINTER_PORT}`);
    await page.getByRole("button", { name: "Add printer" }).last().click();

    const card = page
      .getByRole("link", { name: new RegExp(printerName) })
      .locator("xpath=ancestor::article");
    await expect(card).toBeVisible();

    // The hub connects on printer creation — status flips from unknown/offline
    // to ready once the emulator's websocket handshake completes.
    await expect(async () => {
      await page.reload();
      await expect(card.getByText("ready", { exact: true })).toBeVisible({ timeout: 2_000 });
    }).toPass({ timeout: 30_000 });

    // Queue a print from a model's send dialog against the (now sole, least-busy)
    // fleet printer.
    await uploadGcodeModel(page, modelName);
    await openModelPage(page, modelName);
    await expect(page.getByRole("heading", { name: modelName })).toBeVisible();
    await page.getByRole("button", { name: "Send to printer" }).last().click();
    const dialog = page.getByRole("dialog", { name: "Send Rev 1 to printer" });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("radio", { name: "Add to queue" }).click();
    await dialog.getByRole("button", { name: "Add to queue" }).last().click();
    await expect(dialog).toHaveCount(0);

    // The fleet scheduler dispatches within a couple of seconds and the mock
    // print finishes fast (--print-seconds 3) — poll the Queue tab until the job
    // shows up anywhere in its lifecycle (queued, active, or already recent).
    await page.goto("/printers");
    await page.getByRole("tab", { name: "Queue" }).click();
    await expect(async () => {
      await page.reload();
      await page.getByRole("tab", { name: "Queue" }).click();
      await page.getByRole("button", { name: "Recent", exact: true }).click();
      await expect(page.getByText(/\.gcode/).first()).toBeVisible({ timeout: 2_000 });
    }).toPass({ timeout: 30_000 });

    // Maintenance: soft-drain the printer, then resume routing.
    await page.getByRole("tab", { name: "Maintenance" }).click();
    const maintenanceCard = page.locator("section").filter({ hasText: printerName });
    await expect(maintenanceCard).toBeVisible();
    await maintenanceCard.getByRole("button", { name: "Soft drain" }).click();
    await expect(maintenanceCard.getByText("Manual soft drain", { exact: true })).toBeVisible();
    await maintenanceCard.getByRole("button", { name: "Resume routing" }).click();
    await expect(maintenanceCard.getByText("Manual soft drain", { exact: true })).toHaveCount(0);

    // Cleanup: remove the printer so it doesn't linger as "offline" in later runs
    // once this webServer instance's port is inevitably reused.
    await page.getByRole("tab", { name: "Fleet" }).click();
    await card.getByRole("button", { name: "Settings" }).click();
    await page
      .getByRole("dialog", { name: "Edit printer connection" })
      .getByRole("button", { name: "Remove printer", exact: true })
      .click();
    await page.getByRole("dialog").getByRole("button", { name: "Remove" }).click();
    await expect(page.getByRole("link", { name: new RegExp(printerName) })).toHaveCount(0);
  });

  test("queues a saved Revision from the printer dashboard", async ({ page }) => {
    const stamp = Date.now();
    const printerName = `e2e-cockpit-${stamp}`;
    const modelName = `e2e-cockpit-model-${stamp}`;

    // ── Set up a Model and register the isolated emulator ──
    await page.goto("/");
    await collapseNavigationSidebar(page);
    await uploadGcodeModel(page, modelName);
    const modelHref = await modelCard(page, modelName).getAttribute("href");
    const modelPath = new URL(modelHref!, page.url()).pathname;
    expect(modelPath).toMatch(/^\/models\/\d+$/);

    await page.goto("/printers");
    await page.getByRole("button", { name: "Add printer" }).click();
    const setup = page.getByRole("dialog", { name: "Add printer" });
    await setup.getByLabel("Name", { exact: true }).fill(printerName);
    await setup
      .getByPlaceholder("http://printer.local:7125")
      .fill(`http://127.0.0.1:${MOCK_PRINTER_PORT}`);
    await setup.getByRole("button", { name: "Add printer" }).click();
    await page.getByRole("link", { name: printerName, exact: true }).click();
    await expect(page).toHaveURL(/\/printers\/\d+$/);
    const printerPath = new URL(page.url()).pathname;
    const printerId = Number(printerPath.split("/").at(-1));

    // ── Observe idle, queue onto this printer, then see heat and cooling ──
    const current = page.getByRole("region", { name: "Current job" });
    const history = page.getByRole("figure", { name: "Heater history" });
    await expect(current.getByText("No active print")).toBeVisible();
    await expect(history).toContainText("25 °C");
    await expect(page.getByText("0%", { exact: true })).toBeVisible();
    await current.getByRole("button", { name: "Add to queue" }).click();
    const addJob = page.getByRole("dialog", { name: "Add to queue" });
    await expect(addJob.getByText(printerName, { exact: true })).toBeVisible();
    await addJob.getByRole("button", { name: "Revision" }).click();
    const picker = page.getByRole("dialog", { name: "Revision", exact: true });
    await picker.getByRole("button", { name: modelName }).click();
    await picker.getByRole("button", { name: /Revision 1/ }).click();
    const enqueue = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/v1/fleet/queue") && response.request().method() === "POST",
    );
    await addJob.getByRole("button", { name: "Save job" }).click();
    const receipt = await enqueue;
    expect(receipt.ok()).toBe(true);
    const job = await receipt.json();
    expect(job.printer_id).toBe(printerId);

    await expect(current.getByRole("button", { name: "Pause" })).toBeEnabled();
    await expect(page.getByText("210 °C", { exact: true })).toBeVisible();
    await expect(page.getByText("60%", { exact: true })).toBeVisible();
    await expect(current.getByText("No active print")).toBeVisible();
    await expect(history).toContainText("25 °C to 210 °C");
    await expect(page.getByText("0%", { exact: true })).toBeVisible();

    // ── Reload and verify persisted history ──
    await page.reload();
    await expect(current.getByText("No active print")).toBeVisible();
    await current.getByRole("button", { name: "Print history" }).click();
    const completed = page
      .getByRole("table", { name: "Print history" })
      .getByRole("row")
      .filter({
        has: page.getByText(new RegExp(`^Run #${job.id}(?: ·|$)`)),
      });
    await expect(completed.getByRole("button", { name: modelName, exact: true })).toBeVisible();
    await expect(completed).toContainText("Completed");

    // ── Use the native Queue and Maintenance pickers with the keyboard ──
    await page.goto("/printers");
    await page.getByRole("button", { name: /^Active jobs/ }).click();
    await expect(page.getByRole("button", { name: "Active", exact: true })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    const recent = page.getByRole("button", { name: "Recent", exact: true });
    await recent.focus();
    await recent.press("Enter");
    await expect(recent).toHaveAttribute("aria-pressed", "true");
    await expect(
      page
        .getByRole("region", { name: "Recent print jobs" })
        .getByText(job.remote_filename, { exact: true }),
    ).toBeVisible();

    await page.getByRole("tab", { name: "Maintenance" }).click();
    const windows = page.getByRole("combobox", { name: "Windows" });
    await expect(windows).toHaveCSS("display", /^(?:inline-)?flex$/);
    await expect(windows).toHaveCSS("align-items", "center");
    await windows.press("Space");
    await expect.poll(() => windows.evaluate((select) => select.matches(":open"))).toBe(true);
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Enter");
    await expect(windows).toHaveValue("all");

    // ── Clean up the fixture records ──
    await page.getByRole("tab", { name: "Fleet" }).click();
    const card = page
      .getByRole("article")
      .filter({ has: page.getByRole("link", { name: printerName, exact: true }) });
    await card.getByRole("button", { name: "Settings" }).click();
    await page
      .getByRole("dialog", { name: "Edit printer connection" })
      .getByRole("button", { name: "Remove printer", exact: true })
      .click();
    await page.getByRole("dialog").getByRole("button", { name: "Remove", exact: true }).click();
    await expect(card).toHaveCount(0);
    expect((await page.request.delete(`/api/v1${modelPath}`)).ok()).toBe(true);
    expect((await page.request.delete(`/api/v1${modelPath}/purge`)).ok()).toBe(true);
  });
});

test.describe("Klipper printer dashboard", () => {
  test("operates a ready Klipper toolhead on the printer dashboard", async ({ page }) => {
    const printerName = `e2e-controls-${Date.now()}`;
    await page.goto("/printers");
    await collapseNavigationSidebar(page);
    await page.getByRole("button", { name: "Add printer" }).click();
    const setup = page.getByRole("dialog", { name: "Add printer" });
    await setup.getByLabel("Name", { exact: true }).fill(printerName);
    await setup
      .getByPlaceholder("http://printer.local:7125")
      .fill(`http://127.0.0.1:${MOCK_PRINTER_PORT}`);
    await setup.getByRole("button", { name: "Add printer" }).click();
    const printerLink = page.getByRole("link", { name: printerName, exact: true });
    const printerPath = await printerLink.getAttribute("href");
    await printerLink.click();

    try {
      // ── Home the axis and observe the reported position after a jog ──
      await expect(page).toHaveURL(/\/printers\/\d+$/);
      await expect(page.getByRole("figure", { name: "Heater history" })).toBeInViewport();
      const toolhead = page.getByRole("region", { name: "Toolhead" });
      await expect(toolhead.getByRole("button", { name: "Home X", exact: true })).toBeEnabled();
      await page.screenshot({ path: test.info().outputPath("klipper-idle.png") });
      await toolhead.getByRole("button", { name: "Home X", exact: true }).click();
      await expect(toolhead.getByRole("button", { name: "X+", exact: true })).toBeEnabled();
      await toolhead.getByRole("button", { name: "10 mm", exact: true }).click();
      await toolhead.getByRole("button", { name: "X+", exact: true }).click();
      await expect(toolhead.getByText("10 mm", { exact: true }).first()).toBeVisible();
      await expect(toolhead.getByText("10 mm", { exact: true })).toHaveCount(2);
      await expect(toolhead.getByText("Move sent", { exact: true })).toBeVisible();

      // ── Read the new override from the printer after the command ──
      await page.getByRole("spinbutton", { name: "Print speed target (%)" }).fill("115");
      await page.getByRole("button", { name: "Set Print speed", exact: true }).click();
      await expect(page.getByText("115%", { exact: true })).toBeVisible();
      await expect(
        page.getByRole("region", { name: "Print tuning" }).getByText("Tuning command sent"),
      ).toBeVisible();

      // ── Heat, move filament, then verify the cold interlock from fresh telemetry ──
      const feed = page.getByRole("button", { name: "Feed 5 mm", exact: true });
      await expect(feed).toBeDisabled();
      await page.getByRole("button", { name: "Preheat", exact: true }).click();
      await expect(feed).toBeEnabled();
      const receipt = page.waitForResponse(
        (response) => response.url().endsWith("/extrude") && response.request().method() === "POST",
      );
      await feed.click();
      expect((await receipt).ok()).toBe(true);
      await page.getByRole("button", { name: "Cooldown", exact: true }).click();
      await expect(feed).toBeDisabled();
      await expect(page.getByText("25 °C", { exact: true })).toHaveCount(2);
      await page.getByRole("spinbutton", { name: "Print speed target (%)" }).fill("100");
      await page.getByRole("button", { name: "Set Print speed", exact: true }).click();
      await expect(page.getByText("115%", { exact: true })).toHaveCount(0);
    } finally {
      if (printerPath) expect((await page.request.delete(`/api/v1${printerPath}`)).ok()).toBe(true);
    }
  });
});
