/* A fresh installation reaches its first Models using only browser controls. */
import { mkdir, writeFile } from "node:fs/promises";
import { test, expect } from "@playwright/test";
import { gcodeFor } from "../util";

const loseResponse = process.env.PLAYWRIGHT_ONBOARDING_LOST_RESPONSE === "1";

test.describe("Browser onboarding", () => {
  test(
    loseResponse
      ? "recovers a lost account response without creating another administrator"
      : "creates the first account and Model entirely in the browser",
    async ({ page }, testInfo) => {
      const sourcePath = `/tmp/printstash-onboarding-${process.env.PLAYWRIGHT_ONBOARDING_API_PORT ?? "8431"}/existing-models`;
      await mkdir(sourcePath, { recursive: true });
      await writeFile(
        `${sourcePath}/Connected model.stl`,
        "solid connected\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid connected\n",
      );
      await page.setViewportSize({ width: 390, height: 844 });
      await page.goto("/setup");
      await page.getByLabel("Username").fill("first-owner");
      await page.screenshot({ path: testInfo.outputPath("account-mobile.png"), fullPage: true });
      await page.getByLabel("Password", { exact: true }).fill("BrowserPassword123");
      await page.getByLabel("Confirm password").fill("BrowserPassword123");
      await page.getByLabel("Confirm password").press("Enter");
      await expect(page.getByRole("heading", { name: "Your files", exact: true })).toBeFocused();
      await page.setViewportSize({ width: 1280, height: 900 });
      await page.getByRole("button", { name: "Check storage" }).click();
      await expect(page.getByText("Storage ready", { exact: true })).toBeVisible();
      await page.screenshot({ path: testInfo.outputPath("storage-desktop.png"), fullPage: true });
      if (loseResponse) {
        await page.route(
          "**/api/v1/setup",
          async (route) => {
            const response = await route.fetch();
            expect(response.status()).toBe(201);
            await page.context().clearCookies();
            await route.abort("connectionreset");
          },
          { times: 1 },
        );
      }
      await page.getByRole("button", { name: "Create my account and continue" }).click();
      if (loseResponse) {
        await page.getByRole("button", { name: "Sign in", exact: true }).click();
        await page.getByLabel("Username").fill("first-owner");
        await page.getByLabel("Password", { exact: true }).fill("BrowserPassword123");
        await page.getByRole("button", { name: "Sign in", exact: true }).click();
        await expect(page).toHaveURL(/\/$/);
        await page.goto("/getting-started");
      }
      await expect(page).toHaveURL(/\/getting-started$/);
      await page.getByRole("button", { name: "Upload my first files" }).click();
      await page.locator('input[accept=".gcode,.g,.gco,.bgcode"]').setInputFiles({
        name: "first-model.gcode",
        mimeType: "text/plain",
        buffer: Buffer.from(gcodeFor("My first model")),
      });
      await page.getByPlaceholder("e.g. Bracket v2").fill("My first model");
      await page.getByRole("button", { name: /upload to vault/i }).click();
      await page.getByRole("link", { name: "My first model", exact: true }).click();
      await expect(page).toHaveURL(/\/models\/\d+$/);
      await expect(
        page.getByRole("heading", { name: "My first model", exact: true }),
      ).toBeVisible();
      await page.goto("/getting-started");
      await page.getByRole("button", { name: "Connect an existing folder" }).click();
      await page.getByRole("switch", { name: "Library sources enabled" }).click();
      await page.getByLabel("Source name").fill("My existing folder");
      await page.getByLabel("Mounted folder path").fill(sourcePath);
      await page.getByRole("button", { name: "Add source", exact: true }).click();
      await page.getByRole("button", { name: "Scan now", exact: true }).click();
      await expect(page.getByRole("link", { name: "Connected model", exact: true })).toBeVisible();
    },
  );
});
