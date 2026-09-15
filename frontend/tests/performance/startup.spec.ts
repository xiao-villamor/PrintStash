/** Production bundling must preserve translations and load only the requested route. */
import { expect, test } from "@playwright/test";
import type { Server } from "node:http";
import { startMockApi } from "../e2e/mock-api";
import { localeDefinitions } from "../../src/locales/catalogs";

const apiPort = Number(process.env.PERF_API_PORT ?? 4220);
let api: Server;

test.beforeAll(async () => {
  api = await startMockApi(apiPort);
});

test.afterAll(async () => {
  await new Promise<void>((resolve, reject) => {
    api.close((error) => (error ? reject(error) : resolve()));
  });
});

test.describe("production startup bundles", () => {
  for (const [locale, definition] of Object.entries(localeDefinitions)) {
    test(`renders localized production content: ${locale}`, async ({ page }) => {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.addInitScript((value) => localStorage.setItem("printstash.locale", value), locale);

      await page.goto("/login");

      await expect(
        page.getByRole("heading", { name: definition.messages["auth.welcome"], exact: true }),
      ).toBeVisible();
      await expect(page.locator("html")).toHaveAttribute("lang", locale);
    });
  }

  test("changes language with the compact production catalog", async ({ page }) => {
    await page.goto("/login");

    await page.getByRole("button", { name: "Language: English" }).click();
    await page.getByRole("menuitemradio", { name: "Español" }).click();
    await expect(page.getByRole("heading", { name: "Te damos la bienvenida" })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("heading", { name: "Te damos la bienvenida" })).toBeVisible();
    await page.getByRole("button", { name: "Idioma: Español" }).click();
    await page.getByRole("menuitemradio", { name: "English" }).click();

    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  });

  test("keeps library code out of the login startup", async ({ page }) => {
    const homeRequests: string[] = [];
    page.on("request", (request) => {
      if (/\/assets\/home-[^/]+\.js$/.test(request.url())) homeRequests.push(request.url());
    });

    await page.goto("/login");
    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();

    expect(homeRequests).toEqual([]);
  });
});
