/** Locale selection reaches React, persisted settings and the offline shell. */
import { test, expect } from "@playwright/test";
import { useMockApi } from "./_setup";
useMockApi();

declare global {
  interface Window {
    __initialLocaleHeadings: string[];
  }
}

test.describe("localization", () => {
  for (const { locale, expected, forbidden } of [
    { locale: "en", expected: "Settings", forbidden: "Ajustes" },
    { locale: "es", expected: "Ajustes", forbidden: "Settings" },
  ]) {
    test(`uses the saved locale from the first heading: ${locale}`, async ({ page }) => {
      await page.addInitScript(
        ({ locale }) => {
          localStorage.setItem("printstash.locale", locale);
          const headings: string[] = [];
          Object.defineProperty(window, "__initialLocaleHeadings", { value: headings });
          new MutationObserver(() => {
            document.querySelectorAll("h1").forEach((heading) => {
              headings.push(heading.textContent ?? "");
            });
          }).observe(document, { subtree: true, childList: true, characterData: true });
        },
        { locale },
      );
      await page.goto("/settings");
      await expect(page.getByRole("heading", { name: expected, exact: true })).toBeVisible();
      const headings = await page.evaluate(() => window.__initialLocaleHeadings);
      expect(headings).toContain(expected);
      expect(headings).not.toContain(forbidden);
      await expect(page.locator("html")).toHaveAttribute("lang", locale);
    });
  }

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
    await expect(page.getByRole("searchbox", { name: "Buscar en la biblioteca" })).toBeFocused();
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
