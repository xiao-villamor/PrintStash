/* A fresh installation reaches its first Models using only browser controls. */
import { mkdir, writeFile } from "node:fs/promises";
import { test, expect, type Locator, type Page } from "@playwright/test";
import { brandMarkOutline } from "../../../src/components/brand-geometry";
import { collapseNavigationSidebar, gcodeFor } from "../util";

const loseResponse = process.env.PLAYWRIGHT_ONBOARDING_LOST_RESPONSE === "1";

async function expectCenteredSetupCard(card: Locator) {
  const frame = (await card.boundingBox())!;
  const column = (await card.locator("..").boundingBox())!;
  expect(frame.x + frame.width / 2).toBeCloseTo(column.x + column.width / 2, 0);
  expect(frame.y + frame.height / 2).toBeCloseTo(column.y + column.height / 2, 0);
  await expect(card).toBeInViewport({ ratio: 1 });
}

async function expectExamplePreviews(page: Page) {
  const previews = page.locator('[data-preview-theme] [data-library-thumbnail="ready"] img');
  await expect(previews).toHaveCount(2);
  for (const preview of await previews.all()) await expect(preview).toHaveCSS("opacity", "1");
}

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

    await expect(progress.getByRole("listitem")).toHaveCount(4);
    await expect(progress.locator('[aria-current="step"]')).toHaveText(/Cuenta/);
    await expectCenteredSetupCard(frame);
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
    await expectCenteredSetupCard(frame);
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
      const frame = page.getByRole("region", { name: "Welcome to PrintStash" });
      await page.getByLabel("Username").fill("first-owner");
      await expect(
        page.getByRole("list", { name: "Setup progress" }).locator('[aria-current="step"]'),
      ).toHaveText(/Account/);
      await expectCenteredSetupCard(frame);
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
      await expectCenteredSetupCard(frame);
      await page.screenshot({ path: testInfo.outputPath("storage-desktop.png"), fullPage: true });
      await page.getByRole("button", { name: "Continue", exact: true }).press("Enter");
      await expect(page.getByRole("heading", { name: "Customize previews" })).toBeFocused();
      await expectExamplePreviews(page);
      await page.getByRole("radio", { name: "Sage" }).press("Space");
      const setupGrid = page.getByRole("switch", { name: "Floor grid" });
      await setupGrid.click();
      await expect(setupGrid).toHaveAttribute("aria-checked", "false");
      await expectExamplePreviews(page);
      const views = page.getByRole("combobox", { name: "Preview views" });
      await expect(views).toHaveValue("single");
      await views.press("Space");
      await expect(page.locator("select:open")).toHaveCount(1);
      await page.screenshot({
        path: testInfo.outputPath("appearance-views-menu.png"),
        fullPage: true,
      });
      await page.keyboard.press("ArrowDown");
      await page.keyboard.press("ArrowDown");
      await page.keyboard.press("Enter");
      await expect(views).toHaveValue("oblique-poles");
      await expect(views).toBeFocused();
      await expectCenteredSetupCard(frame);
      await page.screenshot({
        path: testInfo.outputPath("appearance-desktop.png"),
        fullPage: true,
      });
      await page.getByRole("button", { name: "Continue", exact: true }).press("Enter");
      await expect(
        page.getByRole("heading", { name: "Review before creating your account" }),
      ).toBeVisible();
      const summary = page.getByRole("group", { name: "Review before creating your account" });
      await expect(summary.getByText("Sage", { exact: true })).toBeVisible();
      await expect(summary.getByText("Off", { exact: true })).toBeVisible();
      await expect(summary.getByText("6 views · top and bottom")).toBeVisible();
      await expectCenteredSetupCard(frame);
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
      const config = await page.request.get("/api/v1/config");
      expect(config.ok()).toBe(true);
      expect(await config.json()).toMatchObject({
        preview_look: "sage",
        preview_floor_grid: false,
        derivatives_previews_enabled: true,
        derivatives_previews_include_poles: true,
      });
      await collapseNavigationSidebar(page);
      await page.screenshot({
        path: testInfo.outputPath("getting-started-desktop.png"),
        fullPage: true,
      });
      await page.getByRole("link", { name: "Customize previews" }).click();
      await expect(page).toHaveURL(/\/setup-appearance$/);
      await expect(page.getByRole("heading", { name: "Customize previews" })).toBeVisible();
      await expect(
        page.getByRole("checkbox", { name: "Add the PrintStash sample collection" }),
      ).not.toBeChecked();
      await expect(page.getByRole("list", { name: "Setup progress" })).toHaveCount(0);
      await expectExamplePreviews(page);
      await page.screenshot({
        path: testInfo.outputPath("preview-appearance-desktop.png"),
        fullPage: true,
      });
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
