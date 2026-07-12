import { test, expect } from "./helpers";

// Adds a real Moonraker printer record (it will sit offline — no hardware — but
// the create/list/remove path is fully exercised against the backend).
test("add and remove a printer", async ({ page }) => {
  const name = `e2e-printer-${Date.now()}`;
  await page.goto("/printers");
  await expect(page.getByRole("heading", { name: "Printers" })).toBeVisible();

  await page.getByRole("button", { name: "Add printer" }).click();
  await page.getByPlaceholder("Voron 2.4").fill(name);
  await page.getByPlaceholder("http://printer.local:7125").fill("http://127.0.0.1:7125");
  await page.getByRole("button", { name: "Add printer" }).last().click();

  const card = page.getByRole("link", { name: new RegExp(name) });
  await expect(card).toBeVisible();

  // Persisted.
  await page.reload();
  await expect(page.getByRole("link", { name: new RegExp(name) })).toBeVisible();

  // Remove through the card action and shared confirmation dialog.
  await card.locator("xpath=ancestor::article").getByRole("button", { name: "Remove" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Remove" }).click();
  await expect(page.getByRole("link", { name: new RegExp(name) })).toHaveCount(0);
});
