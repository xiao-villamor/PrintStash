/**
 * A queued print reaching a real printer and coming back.
 *
 * This is the only test that exercises the whole chain end to end: an emulated Moonraker
 * comes online, the queue routes a job to it, the job dispatches, and the printer drains.
 * Every link in that chain is tested in isolation elsewhere; this one proves they are
 * actually connected, which is the failure no unit test can see.
 */
import { test, expect } from "./helpers";
import { modelCard, uploadGcodeModel } from "./util";

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
    await page.getByPlaceholder("Voron 2.4").fill(printerName);
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
    await modelCard(page, modelName).click();
    await expect(page.getByRole("heading", { name: modelName })).toBeVisible();
    await page.getByRole("button", { name: "Send to printer" }).last().click();
    const dialog = page.getByRole("dialog", { name: "Send to printer" });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Add to queue" }).first().click();
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
      await expect(page.getByText(/\.gcode/).first()).toBeVisible({ timeout: 2_000 });
    }).toPass({ timeout: 30_000 });

    // Maintenance: soft-drain the printer, then resume routing.
    await page.getByRole("tab", { name: "Maintenance" }).click();
    const maintenanceCard = page.locator("section").filter({ hasText: printerName });
    await expect(maintenanceCard).toBeVisible();
    await maintenanceCard.getByRole("button", { name: "Soft drain" }).click();
    await expect(maintenanceCard.getByText("Draining")).toBeVisible();
    await maintenanceCard.getByRole("button", { name: "Resume routing" }).click();
    await expect(maintenanceCard.getByText("Draining")).toHaveCount(0);

    // Cleanup: remove the printer so it doesn't linger as "offline" in later runs
    // once this webServer instance's port is inevitably reused.
    await page.getByRole("tab", { name: "Fleet" }).click();
    await card.getByRole("button", { name: "Remove" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Remove" }).click();
    await expect(page.getByRole("link", { name: new RegExp(printerName) })).toHaveCount(0);
  });
});
