import { test, expect, ADMIN } from "./helpers";

test("sign in through the real login form", async ({ page }) => {
  // The fixture seeds a token; clear it so we exercise the actual /login flow.
  await page.addInitScript(() => localStorage.clear());
  await page.goto("/login");

  await page.getByLabel("Username").fill(ADMIN.username);
  await page.getByLabel("Password").fill(ADMIN.password);
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page).toHaveURL(/\/(\?.*)?$/);
  await expect(page.getByRole("button", { name: "Upload" })).toBeVisible();
});

test("the login form rejects a wrong password", async ({ page }) => {
  await page.addInitScript(() => localStorage.clear());
  await page.goto("/login");

  await page.getByLabel("Username").fill(ADMIN.username);
  await page.getByLabel("Password").fill("definitely-wrong");
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page.getByText("Invalid username or password.")).toBeVisible();
});

test("a username + API key authenticates, and stops once revoked", async ({ page }) => {
  const keyName = `e2e-apikey-${Date.now()}`;
  await page.goto("/settings");
  await page.getByRole("button", { name: "Users & Access" }).click();

  await page.getByLabel("Key name").fill(keyName);
  await page.getByRole("button", { name: "Generate" }).click();
  const secret = (await page.locator("code").first().innerText()).trim();
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
