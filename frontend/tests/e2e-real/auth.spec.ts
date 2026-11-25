/**
 * Getting into a real deployment, and being kept out of one.
 *
 * These three run against the real backend because auth is the one flow a mocked API
 * cannot prove anything about: the token has to be minted by the server, stored by the
 * browser, and honoured on the next request. A wrong password must fail *at the server*,
 * not at a client-side check, and a revoked API key must stop working immediately —
 * `revoked` in a list somewhere is not the same as `refused` on the wire.
 */
import { test, expect, ADMIN } from "./helpers";
import { brandMarkFold, brandMarkOutline } from "../../src/components/brand-geometry";
import { collapseNavigationSidebar } from "./util";

test.describe("authentication", () => {
  test.describe("without a browser session", () => {
    test.use({ authenticated: false });

    for (const theme of ["light", "dark"] as const) {
      test(`separates the login form from the print artwork in ${theme} mode`, async ({
        page,
      }, testInfo) => {
        await page.addInitScript((value) => localStorage.setItem("printstash.theme", value), theme);
        await page.goto("/login");
        await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
        const brand = page.getByRole("banner");
        await expect(brand.locator(`path[d="${brandMarkOutline}"]`)).toHaveCSS(
          "fill",
          theme === "light" ? "rgb(20, 25, 35)" : "rgb(241, 244, 250)",
        );
        await expect(brand.locator(`path[d="${brandMarkFold}"]`)).toHaveCSS(
          "fill",
          theme === "light" ? "rgb(79, 83, 91)" : "rgb(183, 187, 193)",
        );
        const form = page.getByRole("main").locator("form");
        const artwork = page.locator(".auth-split-art");
        await expect(artwork).toBeVisible();
        await expect(artwork).toHaveAttribute("aria-hidden", "true");
        const formBounds = (await form.boundingBox())!;
        const artBounds = (await artwork.boundingBox())!;
        expect(formBounds.x + formBounds.width).toBeLessThan(artBounds.x);
        expect(artBounds.width).toBeGreaterThan(formBounds.width);
        await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeInViewport();
        await page.screenshot({ path: testInfo.outputPath(`login-${theme}-desktop.png`) });
      });
    }

    test("the print panel uses a soft white background in dark mode", async ({ page }) => {
      await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
      await page.goto("/login");
      const artwork = page.locator(".auth-split-art");
      const outline = artwork.locator('path[fill-rule="evenodd"]').last().locator("..");
      const toggle = page.getByRole("button", { name: "Toggle theme" });
      await expect(artwork).toHaveCSS("background-color", "rgb(37, 44, 55)");
      await toggle.click();
      await expect(artwork).toHaveCSS("background-color", "rgb(237, 241, 247)");
      await expect(outline).toHaveCSS("stroke", "rgb(105, 123, 146)");
      await toggle.click();
      await expect(artwork).toHaveCSS("background-color", "rgb(37, 44, 55)");
      await expect(outline).toHaveCSS("stroke", "rgb(237, 241, 247)");
    });

    test("the favicon follows the saved theme", async ({ page }) => {
      await page.emulateMedia({ colorScheme: "light" });
      await page.goto("/login");
      const favicon = page.locator("#app-favicon");
      const toggle = page.getByRole("button", { name: "Toggle theme" });
      await expect(favicon).toHaveAttribute("href", "/icon-light.svg?v=6");
      await toggle.click();
      await expect(favicon).toHaveAttribute("href", "/icon-dark.svg?v=6");
      await page.reload();
      await expect(favicon).toHaveAttribute("href", "/icon-dark.svg?v=6");
      await toggle.click();
      await expect(favicon).toHaveAttribute("href", "/icon-light.svg?v=6");
      await page.reload();
      await expect(favicon).toHaveAttribute("href", "/icon-light.svg?v=6");
    });

    test("keeps the login controls reachable on a short screen", async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 600 });
      await page.goto("/login");
      await page.getByLabel("Username").fill(ADMIN.username);
      await page.getByLabel("Password", { exact: true }).fill("definitely-wrong");
      await page.getByRole("button", { name: "Sign in", exact: true }).click();
      await expect(page.getByRole("alert")).toHaveText("Invalid username or password.");
      await page.getByRole("heading", { name: "Welcome back" }).scrollIntoViewIfNeeded();
      await expect(page.getByRole("heading", { name: "Welcome back" })).toBeInViewport();
      await page.getByRole("button", { name: "Forgot password?" }).click();
      await page.getByRole("link", { name: "How recovery works" }).scrollIntoViewIfNeeded();
      await expect(page.getByRole("link", { name: "How recovery works" })).toBeInViewport();
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
        1280,
      );
    });

    test("sign in through the real login form", async ({ page }) => {
      await page.goto("/login");

      await page.getByLabel("Username").fill(ADMIN.username);
      await page.getByLabel("Password", { exact: true }).fill(ADMIN.password);
      await page.getByRole("button", { name: "Sign in" }).click();

      await expect(page).toHaveURL(/\/(\?.*)?$/);
      await expect(page.getByRole("button", { name: "Upload", exact: true })).toBeVisible();
      await collapseNavigationSidebar(page);
      const home = page.getByRole("link", { name: "PrintStash", exact: true });
      await expect(home.locator(`path[d="${brandMarkOutline}"]`)).toBeVisible();
    });

    test("the login form rejects a wrong password", async ({ page }) => {
      await page.goto("/login");

      await page.getByLabel("Username").fill(ADMIN.username);
      await page.getByLabel("Password", { exact: true }).fill("definitely-wrong");
      await page.getByRole("button", { name: "Sign in" }).click();

      await expect(page.getByText("Invalid username or password.")).toBeVisible();
    });
  });

  test("a username + API key authenticates, and stops once revoked", async ({ page }) => {
    const keyName = `e2e-apikey-${Date.now()}`;
    await page.goto("/settings");
    await page.getByRole("button", { name: "Users & Access" }).click();

    await page.getByLabel("Key name").fill(keyName);
    await page.getByRole("button", { name: "Generate" }).click();
    // The panel always contains documentation <code> elements. Wait for and
    // select the generated credential itself instead of racing those static
    // nodes while React renders the API response.
    const secret = (await page.locator("code").filter({ hasText: /^psk_/ }).innerText()).trim();
    expect(secret.length).toBeGreaterThan(10);

    // The key exchanges for a JWT at /auth/login (proxied through the frontend).
    const ok = await page.request.post("/api/v1/auth/login", {
      data: { username: ADMIN.username, api_key: secret },
    });
    expect(ok.status()).toBe(200);
    expect((await ok.json()).access_token).toBeTruthy();

    // Revoke it, and the same key is rejected.
    await page.getByRole("button", { name: "Revoke API key", exact: true }).click();
    await expect(page.getByText(keyName)).toHaveCount(0);
    const denied = await page.request.post("/api/v1/auth/login", {
      data: { username: ADMIN.username, api_key: secret },
    });
    expect(denied.status()).toBe(401);
  });
});
