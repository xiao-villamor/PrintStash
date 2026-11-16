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

test.describe("authentication", () => {
  test.describe("without a browser session", () => {
    test.use({ authenticated: false });

    for (const theme of ["light", "dark"] as const) {
      test(`centers the login form at responsive sizes in ${theme} mode`, async ({
        page,
      }, testInfo) => {
        await page.addInitScript((value) => localStorage.setItem("printstash.theme", value), theme);
        await page.goto("/login");
        await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
        const card = page
          .getByRole("main")
          .locator(":scope > div")
          .filter({
            has: page.getByRole("button", { name: "Sign in", exact: true }),
          });
        for (const viewport of [
          { width: 1440, height: 1100 },
          { width: 390, height: 844 },
        ]) {
          await page.setViewportSize(viewport);
          await expect(async () => {
            const bounds = await card.boundingBox();
            expect(bounds).not.toBeNull();
            expect(Math.abs(bounds!.x + bounds!.width / 2 - viewport.width / 2)).toBeLessThan(2);
            expect(Math.abs(bounds!.y + bounds!.height / 2 - viewport.height / 2)).toBeLessThan(2);
          }).toPass({ timeout: 5_000 });
          await page.screenshot({
            path: testInfo.outputPath(`login-${theme}-${viewport.width}.png`),
          });
        }
      });
    }

    test("keeps the login controls reachable on a short screen", async ({ page }) => {
      await page.setViewportSize({ width: 390, height: 360 });
      await page.goto("/login");
      await page.getByLabel("Username").fill(ADMIN.username);
      await page.getByLabel("Password").fill("definitely-wrong");
      await page.getByRole("button", { name: "Sign in", exact: true }).click();
      await expect(page.getByRole("alert")).toHaveText("Invalid username or password.");
      await page.getByRole("heading", { name: "Welcome back" }).scrollIntoViewIfNeeded();
      await expect(page.getByRole("heading", { name: "Welcome back" })).toBeInViewport();
      await page.getByRole("link", { name: "How recovery works" }).scrollIntoViewIfNeeded();
      await expect(page.getByRole("link", { name: "How recovery works" })).toBeInViewport();
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
        390,
      );
    });

    test("sign in through the real login form", async ({ page }) => {
      await page.goto("/login");

      await page.getByLabel("Username").fill(ADMIN.username);
      await page.getByLabel("Password").fill(ADMIN.password);
      await page.getByRole("button", { name: "Sign in" }).click();

      await expect(page).toHaveURL(/\/(\?.*)?$/);
      await expect(page.getByRole("button", { name: "Upload", exact: true })).toBeVisible();
    });

    test("the login form rejects a wrong password", async ({ page }) => {
      await page.goto("/login");

      await page.getByLabel("Username").fill(ADMIN.username);
      await page.getByLabel("Password").fill("definitely-wrong");
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
    await page.getByTitle("Revoke API key").click();
    await expect(page.getByText(keyName)).toHaveCount(0);
    const denied = await page.request.post("/api/v1/auth/login", {
      data: { username: ADMIN.username, api_key: secret },
    });
    expect(denied.status()).toBe(401);
  });
});
