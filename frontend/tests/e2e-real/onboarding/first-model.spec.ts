/* A fresh installation reaches its first Models using only browser controls. */
import { mkdir, writeFile } from "node:fs/promises";
import { test, expect } from "@playwright/test";
import { gcodeFor } from "../util";

const loseResponse = process.env.PLAYWRIGHT_ONBOARDING_LOST_RESPONSE === "1";

test.describe("Browser onboarding", () => {
  test("centers setup at responsive viewport sizes", async ({ page }, testInfo) => {
    await page.addInitScript(() => {
      localStorage.setItem("printstash.locale", "es");
      localStorage.setItem("printstash.theme", "dark");
    });
    await page.goto("/setup");
    await page.getByLabel("Usuario", { exact: true }).fill("first-owner");
    for (const viewport of [
      { width: 1440, height: 1100 },
      { width: 390, height: 844 },
    ]) {
      await page.setViewportSize(viewport);
      const frame = page.getByRole("region", { name: "Bienvenido a PrintStash" });
      const bounds = await frame.boundingBox();
      expect(bounds).not.toBeNull();
      expect(Math.abs(bounds!.x + bounds!.width / 2 - viewport.width / 2)).toBeLessThan(10);
      expect(bounds!.height).toBeLessThan(viewport.height);
      if (viewport.width > 1000) {
        expect(Math.abs(bounds!.y + bounds!.height / 2 - viewport.height / 2)).toBeLessThan(2);
        const passwordRow = await page.getByLabel("Contraseña", { exact: true }).boundingBox();
        const confirmationRow = await page
          .getByLabel("Confirmar contraseña", { exact: true })
          .boundingBox();
        expect(passwordRow!.y).toBe(confirmationRow!.y);
      }
      const progress = await page
        .getByRole("list", { name: "Progreso de configuración" })
        .boundingBox();
      expect(progress!.height).toBeLessThan(60);
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      ).toBe(true);
      const password = await page.getByLabel("Contraseña", { exact: true }).boundingBox();
      const eye = await page
        .getByRole("button", { name: "Mostrar Contraseña", exact: true })
        .boundingBox();
      expect(eye!.x).toBeGreaterThan(password!.x);
      expect(eye!.x + eye!.width).toBeLessThanOrEqual(password!.x + password!.width + 1);
      await page.screenshot({
        path: testInfo.outputPath(`account-es-${viewport.width}.png`),
        fullPage: true,
      });
    }
    await page.getByLabel("Contraseña", { exact: true }).fill("BrowserPassword123");
    await page.getByLabel("Confirmar contraseña", { exact: true }).fill("BrowserPassword123");
    await page.getByRole("button", { name: "Continuar", exact: true }).click();
    for (const viewport of [
      { width: 1440, height: 1100 },
      { width: 390, height: 844 },
    ]) {
      await page.setViewportSize(viewport);
      await expect(page.getByLabel("Directorio de modelos", { exact: true })).toBeVisible();
      const local = await page
        .getByRole("button", { name: "En este servidor (recomendado)" })
        .boundingBox();
      const remote = await page
        .getByRole("button", { name: "Almacenamiento de objetos compatible con S3" })
        .boundingBox();
      expect(local!.width).toBeGreaterThan(remote!.width * 2);
      expect(local!.y).toBeLessThan(remote!.y);
      const frame = await page
        .getByRole("region", { name: "Bienvenido a PrintStash" })
        .boundingBox();
      expect(frame!.height).toBeLessThan(viewport.height * 1.5);
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      ).toBe(true);
      await page.screenshot({
        path: testInfo.outputPath(`storage-es-${viewport.width}.png`),
        fullPage: true,
      });
    }
    await page.getByRole("button", { name: "Cambiar tema" }).click();
    await expect(page.locator("html")).not.toHaveClass(/dark/);
    await page.screenshot({
      path: testInfo.outputPath("storage-es-light-mobile.png"),
      fullPage: true,
    });
  });

  test(
    loseResponse
      ? "recovers a lost account response without creating another administrator"
      : "reaches its first Model entirely through browser controls",
    async ({ page }, testInfo) => {
      const sourcePath = `/tmp/printstash-onboarding-${process.env.PLAYWRIGHT_ONBOARDING_API_PORT ?? "8431"}/existing-models`;
      await mkdir(sourcePath, { recursive: true });
      await writeFile(
        `${sourcePath}/Connected model.stl`,
        "solid connected\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid connected\n",
      );
      await page.addInitScript(() => localStorage.setItem("printstash.theme", "dark"));
      await page.setViewportSize({ width: 390, height: 844 });
      await page.goto("/setup");
      await page.getByLabel("Username").fill("first-owner");
      await expect(
        page.getByRole("list", { name: "Setup progress" }).locator('[aria-current="step"]'),
      ).toHaveText(/Your account/);
      await page.screenshot({ path: testInfo.outputPath("account-mobile.png"), fullPage: true });
      await page.getByLabel("Password", { exact: true }).fill("BrowserPassword123");
      await page.getByRole("button", { name: "Show Password", exact: true }).click();
      await expect(page.getByLabel("Password", { exact: true })).toHaveAttribute("type", "text");
      await page.getByRole("button", { name: "Hide Password", exact: true }).click();
      await page.getByLabel("Confirm password", { exact: true }).fill("BrowserPassword123");
      await page.getByLabel("Confirm password", { exact: true }).press("Enter");
      await expect(page.getByRole("heading", { name: "Your files", exact: true })).toBeFocused();
      await page.keyboard.press("Tab");
      await expect(
        page.getByRole("button", { name: "On this server (recommended)" }),
      ).toBeFocused();
      await expect(page.getByLabel("Models directory", { exact: true })).toBeVisible();
      await expect(page.getByLabel("Thumbnail directory", { exact: true })).toBeVisible();
      await expect(page.getByLabel("Root", { exact: true })).toHaveCount(0);
      await page.screenshot({ path: testInfo.outputPath("storage-mobile.png"), fullPage: true });
      await page.setViewportSize({ width: 1280, height: 900 });
      await page.getByRole("button", { name: "Check storage" }).press("Enter");
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
      await page.getByRole("button", { name: "Create my account and continue" }).press("Enter");
      if (loseResponse) {
        await page.getByRole("button", { name: "Sign in", exact: true }).press("Enter");
        await page.getByLabel("Username").fill("first-owner");
        await page.getByLabel("Password", { exact: true }).fill("BrowserPassword123");
        await page.getByRole("button", { name: "Sign in", exact: true }).press("Enter");
        await expect(page).toHaveURL(/\/$/);
        await page.goto("/getting-started");
      }
      await expect(page).toHaveURL(/\/getting-started$/);
      await expect(page.getByRole("button", { name: "Upload my first files" })).toBeVisible();
      await page.getByRole("button", { name: /Language:/ }).click();
      await page.getByRole("menuitemradio", { name: "Español" }).click();
      await expect(
        page.getByRole("heading", { name: "Todo listo para tu primer modelo" }),
      ).toBeVisible();
      for (const viewport of [
        { width: 1440, height: 1100 },
        { width: 390, height: 844 },
      ]) {
        await page.setViewportSize(viewport);
        await expect(
          page.getByRole("button", { name: "Subir mis primeros archivos" }),
        ).toBeVisible();
        expect(
          await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
        ).toBe(true);
        await page.screenshot({
          path: testInfo.outputPath(`start-es-${viewport.width}.png`),
          fullPage: true,
        });
      }
      await page.getByRole("button", { name: /Idioma:/ }).click();
      await page.screenshot({
        path: testInfo.outputPath("language-menu-es-mobile.png"),
        fullPage: true,
      });
      await page.keyboard.press("Escape");
      await page.getByRole("button", { name: "Cambiar tema" }).click();
      await expect(page.locator("html")).not.toHaveClass(/dark/);
      await page.screenshot({
        path: testInfo.outputPath("start-es-light-mobile.png"),
        fullPage: true,
      });
      await page.getByRole("button", { name: "Cambiar tema" }).click();
      await page.getByRole("button", { name: /^Conectar una carpeta existente/ }).click();
      await expect(page.getByRole("button", { name: "/boot", exact: true })).toHaveCount(0);
      await expect(page.getByRole("button", { name: "/run", exact: true })).toHaveCount(0);
      await page.getByLabel("Nombre de la carpeta").fill("Mis modelos");
      await page.getByLabel("Ruta de la carpeta en el servidor").fill("/libraries/models");
      await page.screenshot({ path: testInfo.outputPath("folder-es-mobile.png"), fullPage: true });
      await page.getByText("¿No aparece tu carpeta?", { exact: true }).click();
      await page.screenshot({
        path: testInfo.outputPath("folder-help-es-mobile.png"),
        fullPage: true,
      });
      await page.getByRole("button", { name: "Volver al primer modelo" }).click();
      await page.getByRole("button", { name: "Subir mis primeros archivos" }).click();
      await page.screenshot({ path: testInfo.outputPath("upload-es-mobile.png"), fullPage: true });
      await page.setViewportSize({ width: 1440, height: 1100 });
      await page.screenshot({ path: testInfo.outputPath("upload-es-desktop.png"), fullPage: true });
      await page.getByRole("button", { name: "Cancelar", exact: true }).click();
      await page.getByRole("button", { name: /Idioma:/ }).click();
      await page.getByRole("menuitemradio", { name: "English", exact: true }).click();
      await page.getByRole("button", { name: "I'll do this later" }).press("Enter");
      await expect(page).toHaveURL(/\/$/);
      await page.getByRole("button", { name: "Resume the getting-started guide" }).press("Enter");
      await expect(page).toHaveURL(/\/getting-started$/);
      await page.getByRole("button", { name: "Upload my first files" }).press("Enter");
      await page.getByLabel("Model or G-code file").setInputFiles({
        name: "first-model.gcode",
        mimeType: "text/plain",
        buffer: Buffer.from(gcodeFor("My first model")),
      });
      await page.getByPlaceholder("e.g. Bracket v2").fill("My first model");
      await page.getByRole("button", { name: "Add to my library" }).press("Enter");
      await expect(
        page.getByRole("heading", { name: "Your models are ready to explore" }),
      ).toBeVisible();
      await page.screenshot({
        path: testInfo.outputPath("first-model-success.png"),
        fullPage: true,
      });
      await page.getByRole("link", { name: "My first model", exact: true }).press("Enter");
      await expect(page).toHaveURL(/\/models\/\d+$/);
      await expect(
        page.getByRole("heading", { name: "My first model", exact: true }),
      ).toBeVisible();
      await page.goto("/settings");
      await page.getByRole("button", { name: "Resume the getting-started guide" }).press("Enter");
      await expect(page).toHaveURL(/\/getting-started$/);
      await page.getByRole("button", { name: "Connect an existing folder" }).press("Enter");
      await page.getByLabel("Folder name").fill("My existing folder");
      await page.getByLabel("Folder path on the server").fill(sourcePath);
      await page
        .getByRole("button", { name: "Connect and find models", exact: true })
        .press("Enter");
      await expect(page.getByRole("link", { name: "Connected model", exact: true })).toBeVisible();
    },
  );
});
