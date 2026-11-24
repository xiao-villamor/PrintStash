/**
 * Registering a machine and taking it out of the fleet.
 *
 * The smallest possible round trip, and the one every other printer test depends on: if
 * adding a printer through the form does not produce a row the app can then remove, no
 * amount of control-endpoint coverage matters.
 */
import { test, expect, authBundleFor, ADMIN } from "./helpers";
import { collapseNavigationSidebar } from "./util";

// Adds a real Moonraker printer record (it will sit offline — no hardware — but
// the create/list/remove path is fully exercised against the backend).
test.describe("printers", () => {
  test("shows idle printer telemetry in Fleet", async ({ page }) => {
    const name = `e2e-fleet-readings-${Date.now()}`;
    const port = Number(process.env.PLAYWRIGHT_MOCK_PRINTER_PORT ?? 7530);
    await page.goto("/printers");
    await collapseNavigationSidebar(page);
    await page.getByRole("button", { name: "Add printer" }).click();
    const setup = page.getByRole("dialog", { name: "Add printer", exact: true });
    await setup.getByLabel("Name", { exact: true }).fill(name);
    await setup.getByPlaceholder("http://printer.local:7125").fill(`http://127.0.0.1:${port}`);
    await setup.getByRole("button", { name: "Add printer" }).click();

    const link = page.getByRole("link", { name, exact: true });
    const path = await link.getAttribute("href");
    const card = page.getByRole("article").filter({ has: link });
    try {
      await expect(card.getByText("No active print")).toBeVisible();
      await expect(card.getByText("Nozzle", { exact: true })).toBeVisible();
      await expect(card.getByText("Bed", { exact: true })).toBeVisible();
      await expect(card.getByText("25 °C", { exact: true })).toHaveCount(2);
      await expect(card.getByText("Part cooling fan", { exact: true })).toBeVisible();
      await expect(card.getByText("0%", { exact: true })).toBeVisible();
      await link.press("Enter");
      await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
    } finally {
      expect((await page.request.delete(`/api/v1${path}`)).ok()).toBe(true);
    }
  });

  test("add and remove a printer", async ({ page }) => {
    const name = `e2e-printer-${Date.now()}`;
    await page.goto("/printers");
    await collapseNavigationSidebar(page);
    await expect(page.getByRole("heading", { name: "Printers" })).toBeVisible();

    await page.getByRole("button", { name: "Add printer" }).click();
    await page
      .getByRole("dialog", { name: "Add printer", exact: true })
      .getByLabel("Name", { exact: true })
      .fill(name);
    await page.getByPlaceholder("http://printer.local:7125").fill("http://127.0.0.1:13");
    await page.getByRole("button", { name: "Add printer" }).last().click();

    const card = page.getByRole("link", { name: new RegExp(name) });
    await expect(card).toBeVisible();

    // Persisted.
    await page.reload();
    await expect(page.getByRole("link", { name: new RegExp(name) })).toBeVisible();

    await card
      .locator("xpath=ancestor::article")
      .getByRole("button", { name: "Remove printer", exact: true })
      .click();
    const confirmation = page.getByRole("dialog", { name: "Remove printer?" });
    await expect(confirmation).toHaveAccessibleDescription(
      `"${name}" will be removed from PrintStash.`,
    );
    const path = await card.getAttribute("href");
    const removed = page.waitForResponse(
      (reply) =>
        new URL(reply.url()).pathname === `/api/v1${path}` && reply.request().method() === "DELETE",
    );
    await confirmation.getByRole("button", { name: "Remove", exact: true }).click();
    expect((await removed).status()).toBe(204);
    await expect(card).toHaveCount(0);
    const refreshed = page.waitForResponse(
      (reply) =>
        new URL(reply.url()).pathname === "/api/v1/printers" && reply.request().method() === "GET",
    );
    await page.reload();
    expect((await refreshed).ok()).toBe(true);
    await expect(card).toHaveCount(0);
  });
});

test.describe("printer settings conflict recovery", () => {
  test("two printer editors resolve a conflict without replacing untouched settings", async ({
    page,
  }) => {
    const api = `http://127.0.0.1:${process.env.PLAYWRIGHT_REAL_API_PORT ?? 8410}`;
    const bundle = await authBundleFor(ADMIN.username, ADMIN.password);
    const headers = { Authorization: `Bearer ${bundle.token}` };
    const name = `e2e-printer-edit-${Date.now()}`;
    const response = await page.request.post(`${api}/api/v1/printers`, {
      headers,
      data: {
        name,
        moonraker_url: `http://127.0.0.1:${process.env.PLAYWRIGHT_MOCK_PRINTER_PORT ?? 7530}`,
      },
    });
    expect(response.status()).toBe(201);
    const printer: { id: number } = await response.json();
    const path = `/printers/${printer.id}`;
    const second = await page.context().newPage();
    try {
      await page.goto(path);
      await page.getByRole("tab", { name: "Settings", exact: true }).click();
      await second.goto(path);
      await second.getByRole("tab", { name: "Settings", exact: true }).click();
      await second.getByLabel("Name", { exact: true }).fill(`${name}-revised`);
      await page
        .getByRole("textbox", { name: "Notes", exact: true })
        .fill("Keep the other operator's notes");
      const firstSave = page.waitForResponse(
        (r) =>
          r.url().endsWith(`/api/v1/printers/${printer.id}`) && r.request().method() === "PATCH",
      );
      await page
        .locator("form")
        .filter({ has: page.getByLabel("Name", { exact: true }) })
        .getByRole("button", { name: "Save changes", exact: true })
        .click();
      expect((await firstSave).status()).toBe(200);
      const staleSave = second.waitForResponse(
        (r) =>
          r.url().endsWith(`/api/v1/printers/${printer.id}`) && r.request().method() === "PATCH",
      );
      await second
        .locator("form")
        .filter({ has: second.getByLabel("Name", { exact: true }) })
        .getByRole("button", { name: "Save changes", exact: true })
        .click();
      expect((await staleSave).status()).toBe(412);
      await expect(second.getByLabel("Name", { exact: true })).toHaveValue(`${name}-revised`);
      await second.getByRole("button", { name: "Review current settings" }).click();
      await expect(
        second
          .getByRole("status", { name: "Current printer settings" })
          .getByText("Keep the other operator's notes"),
      ).toBeVisible();
      const revisedSave = second.waitForResponse(
        (r) =>
          r.url().endsWith(`/api/v1/printers/${printer.id}`) && r.request().method() === "PATCH",
      );
      await second.getByRole("button", { name: "Save revised changes" }).click();
      expect((await revisedSave).status()).toBe(200);
      await second.reload();
      await second.getByRole("tab", { name: "Settings", exact: true }).click();
      await expect(second.getByLabel("Name", { exact: true })).toHaveValue(`${name}-revised`);
      await expect(second.getByRole("textbox", { name: "Notes", exact: true })).toHaveValue(
        "Keep the other operator's notes",
      );
    } finally {
      await second.close();
      await page.request.delete(`${api}/api/v1/printers/${printer.id}`, { headers });
    }
  });
});

test.describe("short-screen printer setup", () => {
  for (const viewport of [
    { width: 320, height: 568 },
    { width: 390, height: 600 },
    { width: 844, height: 390 },
  ]) {
    test(`keeps long printer setup usable at ${viewport.width}x${viewport.height}`, async ({
      page,
    }) => {
      await page.setViewportSize(viewport);
      await page.goto("/printers");
      const opener = page.getByRole("button", { name: "Add printer", exact: true });
      await opener.click();
      const dialog = page.getByRole("dialog", { name: "Add printer" });
      await dialog.getByLabel("Integration", { exact: true }).selectOption("prusalink");
      const bounds = await dialog.boundingBox();
      expect(bounds).not.toBeNull();
      expect(bounds!.y).toBeGreaterThanOrEqual(0);
      expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(viewport.height);

      const heading = dialog.getByRole("heading", { name: "Add printer" });
      await heading.scrollIntoViewIfNeeded();
      await expect(heading).toBeInViewport();
      const name = `cancelled-printer-${Date.now()}`;
      await dialog.getByLabel("Name", { exact: true }).fill(name);
      await dialog.getByLabel("PrusaLink URL", { exact: true }).fill("http://127.0.0.1:7125");
      await dialog.getByLabel("Password", { exact: true }).fill("unused-test-password");

      const submit = dialog.getByRole("button", { name: "Add printer", exact: true });
      await submit.scrollIntoViewIfNeeded();
      await expect(submit).toBeInViewport();
      await expect(submit).toBeEnabled();
      await expect(
        submit.evaluate((element) => {
          const rect = element.getBoundingClientRect();
          const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
          return hit !== null && element.contains(hit);
        }),
      ).resolves.toBe(true);
      await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
      await expect(dialog).toHaveCount(0);
      await expect(opener).toBeFocused();
      await page.reload();
      await expect(page.getByRole("link", { name: new RegExp(name) })).toHaveCount(0);
    });
  }

  test("restores keyboard focus after dismissing short-screen setup", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 600 });
    await page.goto("/printers");
    const opener = page.getByRole("button", { name: "Add printer", exact: true });
    await opener.focus();
    await page.keyboard.press("Enter");
    const dialog = page.getByRole("dialog", { name: "Add printer" });
    await dialog.getByLabel("Integration", { exact: true }).selectOption("prusalink");
    await dialog.getByLabel("Password", { exact: true }).fill("discarded-test-password");
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(opener).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByLabel("Integration", { exact: true })).toHaveValue("moonraker");
    await page.keyboard.press("Escape");
  });
});
