/** Locale selection reaches React, persisted settings and the offline shell. */
import { test, expect } from "@playwright/test";
import { useMockApi } from "./_setup";
useMockApi();

test.describe("localization", () => {
  for (const width of [1280, 390]) {
    test(`language selection survives reload at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 844 });
      await page.goto("/settings");
      await page.getByRole("button", { name: /^Language:/ }).click();
      await page.getByRole("menuitemradio", { name: "Español" }).click();
      await expect(page.getByRole("heading", { name: "Ajustes", exact: true })).toBeVisible();
      await expect(page.locator("html")).toHaveAttribute("lang", "es");
      await page.reload();
      await expect(page.getByRole("heading", { name: "Ajustes", exact: true })).toBeVisible();
      await expect(page.getByRole("button", { name: /^Idioma:/ })).toBeVisible();
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      ).toBe(true);
    });
  }

  test("search shortcut works with localized accessible labels", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: /^Language:/ }).click();
    await page.getByRole("menuitemradio", { name: "Español" }).click();
    await page.getByRole("button", { name: /^Idioma:/ }).blur();
    await page.keyboard.press("/");
    await expect(page.getByRole("textbox", { name: "Buscar modelos" })).toBeFocused();
  });

  test("localizes the offline shell without an API or network", async ({ page, context }) => {
    await page.goto("/offline.html");
    await page.evaluate(async () => {
      localStorage.setItem("printstash.locale", "es");
      await navigator.serviceWorker.register("/sw.js");
      await navigator.serviceWorker.ready;
    });
    await page.reload();
    await expect(page.getByRole("heading", { name: "PrintStash está sin conexión" })).toBeVisible();
    // A first offline navigation has no cached application document to render.
    await page.evaluate(async () => {
      for (const name of await caches.keys()) await (await caches.open(name)).delete("/");
    });
    await context.setOffline(true);
    await page.goto("/not-cached-yet");
    await expect(page.getByRole("heading", { name: "PrintStash está sin conexión" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Volver a intentar" })).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("lang", "es");
    await expect(page).toHaveTitle("PrintStash · Sin conexión");
  });
});
