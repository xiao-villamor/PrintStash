/* A fresh installation reaches its first Models using only browser controls. */
import { mkdir, writeFile } from "node:fs/promises";
import { test, expect } from "@playwright/test";
import { brandMarkOutline } from "../../../src/components/brand-geometry";
import { gcodeFor } from "../util";

const loseResponse = process.env.PLAYWRIGHT_ONBOARDING_LOST_RESPONSE === "1";

test.describe("Browser onboarding", () => {
  test("keeps the localized setup steps visible on desktop", async ({ page }, testInfo) => {
    await page.addInitScript(() => {
      localStorage.setItem("printstash.locale", "es");
      localStorage.setItem("printstash.theme", "dark");
    });
    await page.goto("/setup");
    await page.getByLabel("Usuario", { exact: true }).fill("first-owner");
    const mark = page.getByRole("banner").locator(`path[d="${brandMarkOutline}"]`);
    await expect(mark).toHaveCSS("fill", "rgb(241, 244, 250)");

    const frame = page.getByRole("region", { name: "Bienvenido a PrintStash" });
    const progress = page.getByRole("list", { name: "Progreso de configuración" });
    const next = page.getByRole("button", { name: "Continuar", exact: true });
    const password = page.getByLabel("Contraseña", { exact: true });
    const confirmation = page.getByLabel("Confirmar contraseña", { exact: true });

    await expect(progress.getByRole("listitem")).toHaveCount(3);
    await expect(progress.locator('[aria-current="step"]')).toHaveText(/Cuenta/);
    const account = (await frame.boundingBox())!;
    const passwordBounds = (await password.boundingBox())!;
    const confirmBounds = (await confirmation.boundingBox())!;
    const nextBounds = (await next.boundingBox())!;
    expect(confirmBounds.y).toBe(passwordBounds.y);
    expect(confirmBounds.width).toBe(passwordBounds.width);
    expect(nextBounds.y - passwordBounds.y - passwordBounds.height).toBeLessThan(100);
    await expect(next).toBeInViewport();
    await page.screenshot({ path: testInfo.outputPath("account-es-dark.png") });

    await password.fill("BrowserPassword123");
    await confirmation.fill("BrowserPassword123");
    await confirmation.press("Enter");
    await expect(progress.locator('[aria-current="step"]')).toHaveText(/Almacenamiento/);
    await expect(page.getByRole("heading", { name: "Tus archivos", exact: true })).toBeFocused();
    expect((await frame.boundingBox())!.y).toBe(account.y);
    await page.getByRole("button", { name: "Cambiar tema" }).click();
    await expect(page.locator("html")).not.toHaveClass(/dark/);
    await expect(mark).toHaveCSS("fill", "rgb(20, 25, 35)");
    await expect(next).toBeInViewport();
    await page.screenshot({ path: testInfo.outputPath("storage-es-light.png") });
  });

  test(
    loseResponse
      ? "recovers a lost account response without creating another administrator"
      : "reaches its first Model entirely through browser controls",
    async ({ page }, testInfo) => {
      test.setTimeout(240_000);
      const sourcePath = `/tmp/printstash-onboarding-${process.env.PLAYWRIGHT_ONBOARDING_API_PORT ?? "8431"}/existing-models`;
      await mkdir(sourcePath, { recursive: true });
      await writeFile(
        `${sourcePath}/Connected model.stl`,
        "solid connected\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid connected\n",
      );
      await page.addInitScript(() => {
        if (!localStorage.getItem("printstash.theme"))
          localStorage.setItem("printstash.theme", "dark");
      });
      await page.goto("/setup");
      await page.getByLabel("Username").fill("first-owner");
      await expect(
        page.getByRole("list", { name: "Setup progress" }).locator('[aria-current="step"]'),
      ).toHaveText(/Account/);
      await page.screenshot({ path: testInfo.outputPath("account-desktop.png"), fullPage: true });
      await page.getByLabel("Password", { exact: true }).fill("BrowserPassword123");
      await page.getByRole("button", { name: "Show Password", exact: true }).click();
      await expect(page.getByLabel("Password", { exact: true })).toHaveAttribute("type", "text");
      await page.getByRole("button", { name: "Hide Password", exact: true }).click();
      await page.getByLabel("Confirm password", { exact: true }).fill("BrowserPassword123");
      await page.getByLabel("Confirm password", { exact: true }).press("Enter");
      await expect(page.getByRole("heading", { name: "Your files", exact: true })).toBeFocused();
      await page.keyboard.press("Tab");
      await expect(page.getByRole("button", { name: "Local folder" })).toBeFocused();
      await expect(page.getByRole("textbox", { name: "Your files", exact: true })).toBeVisible();
      await expect(page.getByLabel("Previews", { exact: true })).toBeVisible();
      await expect(page.getByLabel("Root", { exact: true })).toHaveCount(0);
      await page.screenshot({ path: testInfo.outputPath("storage-desktop.png"), fullPage: true });
      await page.getByRole("button", { name: "Continue", exact: true }).press("Enter");
      await expect(
        page.getByRole("heading", { name: "Review before creating your account" }),
      ).toBeVisible();
      await page.screenshot({ path: testInfo.outputPath("review-desktop.png"), fullPage: true });
      let accountRequests = 0;
      if (loseResponse) {
        // Keep routing enabled until the context closes. Removing a one-use
        // handler while fulfilling can stall the immediate recovery request.
        await page.route("**/api/v1/setup", async (route) => {
          accountRequests += 1;
          const response = await route.fetch();
          expect(response.status()).toBe(201);
          await route.fulfill({
            status: 502,
            contentType: "application/json",
            body: JSON.stringify({ detail: "setup_response_lost" }),
          });
        });
      }
      await page.getByRole("button", { name: "Create account" }).press("Enter");
      if (loseResponse) {
        await page.getByRole("button", { name: "Sign in", exact: true }).press("Enter");
        await page.getByLabel("Username").fill("first-owner");
        await page.getByLabel("Password", { exact: true }).fill("BrowserPassword123");
        await page.getByRole("button", { name: "Sign in", exact: true }).press("Enter");
        await expect(page).toHaveURL(
          (url) =>
            url.pathname === "/" &&
            url.searchParams.get("type") === "all" &&
            url.searchParams.get("sort") === "date-desc",
        );
        // This owner-only control appears after login confirmation completes.
        await page.goto("/settings");
        await page.getByRole("button", { name: "Resume the getting-started guide" }).click();
      }
      await expect(page).toHaveURL(/\/getting-started$/);
      await expect(page.getByRole("button", { name: "Upload my first files" })).toBeVisible();
      await page.getByRole("link", { name: "Customize previews" }).click();
      await expect(page).toHaveURL(/\/setup-appearance$/);
      await expect(page.getByRole("heading", { name: "Customize previews" })).toBeVisible();
      await expect(
        page.getByRole("checkbox", { name: "Add the PrintStash sample collection" }),
      ).not.toBeChecked();
      await expect(page.getByRole("list", { name: "Setup progress" })).toHaveCount(0);
      const grid = page.getByRole("switch", { name: "Floor grid" });
      const wasChecked = await grid.getAttribute("aria-checked");
      await grid.click();
      await page.getByRole("button", { name: "Save appearance", exact: true }).click();
      await expect(page).toHaveURL(/\/getting-started$/);
      await page.getByRole("link", { name: "Customize previews" }).click();
      await expect(grid).toHaveAttribute("aria-checked", wasChecked === "true" ? "false" : "true");
      await page.getByRole("button", { name: "Back", exact: true }).click();
      await expect(page).toHaveURL(/\/getting-started$/);
      await page.reload();
      await expect(page.getByRole("button", { name: "Upload my first files" })).toBeVisible();
      await page.getByRole("button", { name: "I'll do this later" }).press("Enter");
      await expect(page).toHaveURL(
        (url) =>
          url.pathname === "/" &&
          url.searchParams.get("type") === "all" &&
          url.searchParams.get("sort") === "date-desc",
      );
      await page.goto("/settings");
      await page.getByRole("button", { name: "Resume the getting-started guide" }).press("Enter");
      await expect(page).toHaveURL(/\/getting-started$/);
      await page.goto("/settings");
      await page.getByRole("button", { name: "Don't show again", exact: true }).click();
      await page.reload();
      await expect(
        page.getByRole("heading", { name: "Settings", exact: true, level: 1 }),
      ).toBeVisible();
      await expect(
        page.getByRole("button", { name: "Resume the getting-started guide" }),
      ).toHaveCount(0);
      await page.goto("/");
      await expect(page.getByRole("button", { name: "Upload files", exact: true })).toBeVisible();
      await expect(
        page.getByRole("button", { name: "Resume the getting-started guide" }),
      ).toHaveCount(0);
      await page.goto("/getting-started");
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
      await expect(
        page.getByRole("heading", { name: "Settings", exact: true, level: 1 }),
      ).toBeVisible();
      await expect(
        page.getByRole("button", { name: "Resume the getting-started guide" }),
      ).toHaveCount(0);
      await page.goto("/getting-started");
      await expect(page).toHaveURL(/\/getting-started$/);
      await expect(
        page.getByRole("heading", { name: "Your models are ready to explore" }),
      ).toBeVisible();
      await page.getByRole("button", { name: "Connect an existing folder" }).press("Enter");
      await expect(page.getByRole("heading", { name: "Connect an existing folder" })).toBeVisible();
      await page.getByLabel("Folder name").fill("My existing folder");
      await page.getByLabel("Folder path on the server").fill(sourcePath);
      const [activation] = await Promise.all([
        page.waitForResponse(
          (response) =>
            response.url().includes("/api/v1/config") && response.request().method() === "PUT",
        ),
        page.getByRole("button", { name: "Connect and find models", exact: true }).press("Enter"),
      ]);
      expect(activation.status()).toBe(200);
      expect(activation.request().headers()["x-printstash-edit-contract"]).toBe("conditional-v1");
      expect(activation.request().headers()["if-match"]).toMatch(
        /^"vault-config-e[0-9a-f]{32}-v[1-9][0-9]*"$/,
      );
      await expect(page.getByRole("link", { name: "Connected model", exact: true })).toBeVisible();
      if (loseResponse) expect(accountRequests).toBe(1);
    },
  );
});
