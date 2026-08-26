import { expect, installMockApiHooks, test } from "./helpers";

installMockApiHooks();

test("settings sections are deep-linkable and preserve navigation state", async ({ page }) => {
  await page.goto("/settings?section=trash");
  await expect(page.getByRole("heading", { name: "Trash retention" })).toBeVisible();
  await expect(page.getByLabel("Trash size")).toHaveText("1.5 MB reclaimable");
  await expect(page.getByText("2 files")).toBeVisible();
  await page.getByRole("button", { name: "About" }).click();
  await expect(page).toHaveURL(/\/settings\?section=about$/);
  await expect(page.getByRole("heading", { name: "Latest changes" })).toBeVisible();
});

test("settings prepares a one-time browser extension setup", async ({ page }) => {
  await page.goto("/settings?section=access");

  await page.getByRole("button", { name: "Set up extension" }).click();

  const apiKeys = page.getByRole("group", { name: "API keys" });
  await expect(apiKeys.getByRole("status")).toHaveText("Setup prepared");
  await expect(page.getByRole("button", { name: "Set up extension" })).toHaveCount(0);
  await expect(
    page.getByText("Open the PrintStash extension on this tab to finish the verified connection."),
  ).toBeVisible();
  const setup = await page.evaluate(() =>
    sessionStorage.getItem("printstash.browser-extension-setup:v1"),
  );
  expect(setup).not.toBeNull();
  expect(setup).toContain('"version":1');
  expect(setup).toContain(`"vault":"${new URL(page.url()).origin}"`);
  expect(setup).toContain('"username":"tester"');
  expect(setup).toContain('"apiKey":"psk_browser_setup_secret"');
});

test("settings creates a temporary browser pairing code", async ({ page }) => {
  await page.goto("/settings?section=imports");

  await expect(page.getByRole("heading", { name: "Provider connections" })).toBeVisible();
  await page.getByRole("button", { name: "Create pairing code" }).click();
  await expect(page.getByText("PAIR-1234")).toBeVisible();
  await expect(page.getByText(/Expires at/)).toBeVisible();
});

test("preview settings persist quality choices and queue image recreation", async ({ page }) => {
  await page.goto("/settings?section=previews");

  await page.getByLabel("Preview quality").selectOption("detail");
  await page.getByLabel("Screenshot resolution").selectOption("3");
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem("printstash.preview.preferences:v1")))
    .toContain('"previewQuality":"detail"');

  await Promise.all([
    page.waitForRequest(
      (request) => request.url().includes("/api/v1/config") && request.method() === "PUT",
    ),
    page.getByLabel("Model image quality").selectOption("1280"),
  ]);

  await Promise.all([
    page.waitForResponse(
      (response) =>
        response.url().includes("/api/v1/files/thumbnails/rebuild?force=true") &&
        response.request().method() === "POST",
    ),
    page.getByRole("button", { name: "Recreate all images" }).click(),
  ]);
  await expect(page.getByText("Model preview recreation queued.")).toBeVisible();
});

test("settings warns administrators when a newer release is available", async ({ page }) => {
  await page.goto("/settings");

  await expect(page.getByText("PrintStash v0.10.1 is available")).toBeVisible();
  await expect(page.getByRole("link", { name: "View release" })).toHaveAttribute(
    "href",
    "https://github.com/xiao-villamor/PrintStash/releases/tag/v0.10.1",
  );
});
