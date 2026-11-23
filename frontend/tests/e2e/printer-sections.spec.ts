/** Bambu exposes its own feeds, capabilities and credential fields throughout the printer workspace. */
import { expect, test } from "@playwright/test";
import { useMockApi } from "./_setup";

useMockApi();

test.describe("Bambu workspace", () => {
  test("shows the operator workspace for a Bambu printer", async ({ page }, testInfo) => {
    await page.addInitScript(() => localStorage.setItem("printstash.theme", "system"));
    await page.emulateMedia({ colorScheme: "dark" });
    await page.route("**/api/v1/printers/3", async (route) => {
      const response = await route.fetch();
      const data = await response.json();
      await route.fulfill({
        response,
        json: {
          ...data,
          provider: "bambu_lan",
          name: "Workshop Bambu",
          model_name: "Bambu Lab P1S",
          bambu_host: "printer.example.test",
          bambu_serial: "TEST-SERIAL",
          has_bambu_access_code: true,
          capabilities: {
            ...data.capabilities,
            can_list_files: false,
            can_send_gcode: false,
            support_level: "beta",
          },
        },
      });
    });
    await page.route("**/api/v1/printers/3/material-state", (route) =>
      route.fulfill({
        json: {
          printer_id: 3,
          updated_at: "2026-01-01T00:00:00Z",
          provider_sync_enabled: true,
          tools: [
            {
              tool_key: "tool0",
              label: "Tool 0",
              nozzle_diameter_mm: 0.4,
              source: "bambu_ams",
              stale: false,
            },
          ],
          slots: ["PLA", "PETG", null, null].map((material, index) => ({
            slot_key: `ams:0:${index}`,
            label: `AMS 0 tray ${index}`,
            source: "bambu_ams",
            confidence: "provider_reported",
            state: material ? "loaded" : "empty",
            material_type: material,
            color_hex: material ? "#4169E1" : null,
            stale: false,
          })),
        },
      }),
    );
    await page.route("**/api/v1/fleet/printers/3/maintenance-*", (route) =>
      route.fulfill({ json: [] }),
    );
    await page.route("**/api/v1/printers/3/diagnostics", (route) =>
      route.fulfill({
        json: {
          printer_id: 3,
          provider: "bambu_lan",
          support_level: "beta",
          ok: false,
          checks: [
            { name: "Provider connection", ok: true, detail: "LAN host reachable" },
            { name: "Bambu state", ok: false, detail: "No status report received" },
          ],
          capabilities: {
            can_start: true,
            can_upload: true,
            can_pause: true,
            can_resume: true,
            can_cancel: true,
            can_live_status: true,
            can_list_files: false,
            can_send_gcode: false,
            can_measure_consumption: false,
          },
          notes: ["Bambu LAN file inventory is unavailable."],
          unsupported_actions: [],
        },
      }),
    );
    await page.goto("/printers/3?tab=materials");
    await expect(page.getByRole("heading", { name: "Workshop Bambu" })).toBeVisible({
      timeout: 45_000,
    });
    const sidebar = page.getByRole("button", { name: "Toggle sidebar" });
    if ((await sidebar.getAttribute("aria-expanded")) === "true") await sidebar.click();
    await expect(sidebar).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByRole("region", { name: "AMS 0" }).getByRole("listitem")).toHaveCount(4);
    await expect(
      page
        .getByRole("region", { name: "Maintenance", exact: true })
        .getByRole("button", { name: "Schedule window" }),
    ).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("bambu-materials-maintenance-dark.png") });

    await page.getByRole("tab", { name: "Settings", exact: true }).click();
    const secret = page.getByLabel("LAN access code", { exact: false });
    await expect(secret).toHaveAttribute("type", "password");
    await expect(secret).toHaveValue("");
    await expect(page.getByLabel("Host or IP")).toHaveValue("printer.example.test");
    await page.screenshot({ path: testInfo.outputPath("bambu-settings-dark.png") });

    await page.emulateMedia({ colorScheme: "light" });
    await page.getByRole("button", { name: "Connection checks", exact: true }).click();
    const row = page.getByRole("row", { name: /Bambu state/ });
    await expect(row).toContainText("Failed");
    await expect(row).toContainText("No status report received");
    await page.screenshot({ path: testInfo.outputPath("bambu-diagnostics-light.png") });
    await page.goBack();
    await expect(secret).toHaveValue("");
  });
});
