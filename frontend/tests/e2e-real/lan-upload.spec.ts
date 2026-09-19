/** LAN HTTP has no SubtleCrypto; uploads must still pass real server verification. */
import { test, expect } from "./helpers";
import { modelCard, stlFor } from "./util";

test.describe("LAN uploads", () => {
  test("persists a 112 KiB STL without SubtleCrypto", async ({ page }) => {
    await page.addInitScript(() => {
      Object.defineProperty(window.crypto, "subtle", { value: undefined });
    });
    const name = `lan-stl-${Date.now()}`;
    await page.goto("/");
    await page.getByRole("button", { name: "Upload", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Upload model" });
    await dialog.locator('input[accept=".stl,.3mf,.obj,.step,.stp"]').setInputFiles({
      name: `${name}.stl`,
      mimeType: "model/stl",
      buffer: Buffer.from(stlFor(name).padEnd(112 * 1024, " ")),
    });
    await page.getByPlaceholder("e.g. Bracket v2").fill(name);
    await dialog.getByRole("button", { name: /upload to vault/i }).click();
    await page.getByRole("button", { name: "Notifications" }).click();
    const task = page.getByText(`Upload ${name}`, { exact: true }).locator("..");
    await expect(task.getByText("completed", { exact: true })).toBeVisible({ timeout: 60_000 });
    await page.reload();
    await expect(modelCard(page, name)).toBeVisible();
  });
});
