/**
 * The storage setup headline flow crosses the browser, real API, process restart, and WebDAV.
 * It proves the provider selected during setup becomes the probed active provider afterward.
 */
import { writeFile } from "node:fs/promises";
import path from "node:path";

import { expect, test } from "@playwright/test";

const apiPort = Number(process.env.PLAYWRIGHT_STORAGE_API_PORT ?? 8420);
const webdavPort = Number(process.env.PLAYWRIGHT_STORAGE_WEBDAV_PORT ?? 8775);
const apiBase = `http://127.0.0.1:${apiPort}`;
const restartTrigger = path.resolve("tests/e2e-real/.storage-data/restart");

test.describe("storage provider setup", () => {
  test("configures WebDAV through restart to its probed tier", async ({ page }) => {
    await page.goto("/setup");
    await page.getByLabel("Setup token").fill("playwright-storage-token-123");
    await page.getByLabel("Username").fill("storage-admin");
    await page.getByLabel("Password", { exact: true }).fill("playwright-password");
    await page.getByLabel("Confirm password").fill("playwright-password");
    await page.getByRole("button", { name: "Next" }).click();

    await page.getByRole("button", { name: /Nextcloud and WebDAV/ }).click();
    await page.getByRole("button", { name: /^WebDAV/ }).click();
    await expect(page.getByText("Expected: unguarded")).toBeVisible();
    await page.getByLabel("Server URL").fill(`http://127.0.0.1:${webdavPort}`);
    await page.getByLabel("Username").fill("webdav-user");
    await page.getByLabel("Password").fill("webdav-password");
    await page.getByLabel("Root").fill("vault-data");
    await page.getByRole("button", { name: "Complete setup" }).click();
    await expect(page).toHaveURL(/\/$/);

    await writeFile(restartTrigger, "restart\n", "utf8");
    await expect
      .poll(async () => {
        try {
          const response = await page.request.get(`${apiBase}/api/v1/health`);
          if (!response.ok()) return null;
          const health = await response.json();
          return {
            provider: health.storage?.provider,
            tier: health.storage?.tier,
          };
        } catch {
          return null;
        }
      })
      .toEqual({ provider: "webdav", tier: "unguarded" });

    await page.goto("/settings?section=storage");
    await expect(page.getByRole("heading", { name: "Storage configuration" })).toBeVisible();
    await expect(page.getByText("Active: unguarded")).toBeVisible();
    await expect(page.getByPlaceholder("Stored — leave blank to keep")).toBeVisible();
  });
});
