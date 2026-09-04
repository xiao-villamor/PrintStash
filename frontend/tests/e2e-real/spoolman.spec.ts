/**
 * Connecting the deployment to a Spoolman instance.
 *
 * An integration toggle with a URL behind it has a specific failure mode: it saves,
 * looks right, and comes back empty or enabled-with-no-address after a reload. This runs
 * the full enable → save → reload → disable cycle for exactly that reason.
 */
import { test, expect } from "./helpers";

// Spoolman integration settings (new in 0.8.0). Drives the real config endpoints
// — no external Spoolman is needed: we assert the enable/persist/disable UI flow
// and that a saved base URL survives a reload.

test.describe("spoolman integration", () => {
  test("enable Spoolman, save a base URL, persist across reload, then disable", async ({
    page,
  }) => {
    await page.goto("/settings");
    await page.getByRole("button", { name: "Spoolman" }).click();

    await expect(page.getByRole("heading", { name: "Spoolman" })).toBeVisible();
    await expect(page.getByText("Disabled", { exact: true })).toBeVisible();

    // Master switch on → status flips to "Not connected" and the write-back option
    // (only rendered when enabled) appears. The checkbox is server-controlled (its
    // checked state only flips after the save round-trips), so click + assert the
    // resulting UI rather than check(), whose built-in state assertion would race.
    const enable = page.getByRole("checkbox", { name: "Enable Spoolman integration" });
    await enable.click();
    await expect(page.getByText("Not connected", { exact: true })).toBeVisible();
    await expect(page.getByText("Write consumption back to Spoolman")).toBeVisible();

    // Save a base URL (no external call — updateSpoolman just persists it).
    await page.getByPlaceholder("http://spoolman.local:7912").fill("http://spoolman.example:7912");
    await page.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("Saved.")).toBeVisible();

    // Reload + reopen the section: enabled stays on and the URL persisted.
    await page.reload();
    await page.getByRole("button", { name: "Spoolman" }).click();
    await expect(page.getByRole("checkbox", { name: "Enable Spoolman integration" })).toBeChecked();
    await expect(page.getByPlaceholder("http://spoolman.local:7912")).toHaveValue(
      "http://spoolman.example:7912",
    );

    // Cleanup: turn it back off so the shared backend returns to default.
    await page.getByRole("checkbox", { name: "Enable Spoolman integration" }).click();
    await expect(page.getByText("Disabled", { exact: true })).toBeVisible();
  });
});
