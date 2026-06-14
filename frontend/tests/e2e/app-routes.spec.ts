import { test, expect, type Page } from "@playwright/test";
import type { Server } from "node:http";

import { startMockApi } from "./mock-api";

const apiPort = Number(process.env.PLAYWRIGHT_API_PORT ?? 4210);

let api: Server;

test.beforeAll(async () => {
  api = await startMockApi(apiPort);
});

test.afterAll(async () => {
  await new Promise<void>((resolve, reject) => {
    api.close((error) => (error ? reject(error) : resolve()));
  });
});

// The app shell redirects unauthenticated users to /login for every non-public
// route, so seed a token + user before each navigation. The mock /auth/me
// returns this same superuser, so the auth bootstrap resolves and the app
// renders the requested route instead of the login screen.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("printstash.token", "test-token");
    localStorage.setItem(
      "printstash.user",
      JSON.stringify({
        id: 1,
        username: "tester",
        email: null,
        is_superuser: true,
      }),
    );
  });
});

async function collectPageProblems(page: Page): Promise<string[]> {
  const problems: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      if (message.text().includes("/api/v1/printers/3/ws")) return;
      problems.push(`console error: ${message.text()}`);
    }
  });
  page.on("pageerror", (error) => {
    problems.push(`page error: ${error.message}`);
  });
  page.on("requestfailed", (request) => {
    const url = request.url();
    if (url.includes("_rsc=")) return;
    problems.push(`request failed: ${url} ${request.failure()?.errorText ?? ""}`);
  });
  page.on("response", (response) => {
    const url = response.url();
    if (response.status() >= 400 && !url.includes("_rsc=")) {
      problems.push(`bad response: ${response.status()} ${url}`);
    }
  });
  return problems;
}

test("model detail route renders data and hydrates printer integrations", async ({ page }) => {
  const problems = await collectPageProblems(page);

  await page.goto("/models/1");

  await expect(page.getByRole("heading", { name: "skadis_kitchen-roll_screw" })).toBeVisible();
  await expect(page.getByText("Creality Ender-3 V3 SE").first()).toBeVisible();
  await expect(page.getByRole("link", { name: /source model/i })).toHaveAttribute(
    "href",
    /printables\.com\/model\/123-skadis-kitchen-roll-screw/,
  );
  await expect(page.getByText("Printed OK").first()).toBeVisible();
  await expect(page.getByText("1/1 online")).toBeVisible();
  await expect(page.getByText("This page could not be found")).toHaveCount(0);

  const html = await page.content();
  expect(html).not.toContain("NEXT_HTTP_ERROR_FALLBACK;404");
  expect(html).not.toContain("printerId\":\"$NaN");
  expect(problems).toEqual([]);
});

test("printer list route loads configured printers through the frontend proxy", async ({ page }) => {
  const problems = await collectPageProblems(page);

  await page.goto("/printers");

  await expect(page.getByRole("heading", { name: "Printers" })).toBeVisible();
  await expect(page.getByRole("link", { name: /ender/i })).toBeVisible();
  await expect(page.getByText("Moonraker", { exact: true })).toBeVisible();
  await expect(page.getByText("ready", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("No printers configured yet.")).toHaveCount(0);
  await expect(page.getByText("Failed to fetch")).toHaveCount(0);
  await expect(page.getByText("This page could not be found")).toHaveCount(0);
  expect(problems).toEqual([]);
});

test("profiles route renders detected filament and printer presets", async ({ page }) => {
  const problems = await collectPageProblems(page);

  await page.goto("/profiles");

  await expect(page.getByRole("heading", { name: "Profiles" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Filament presets" })).toBeVisible();
  await expect(page.getByLabel("Filament preset name 1")).toHaveValue("Generic PLA");
  await expect(page.getByLabel("Filament brand 1")).toHaveValue("Generic");
  await expect(page.getByRole("heading", { name: "Printer presets" })).toBeVisible();
  await expect(page.getByLabel("Printer preset name 1")).toHaveValue("Creality Ender-3 V3 SE");
  await expect(page.getByText("Failed to fetch")).toHaveCount(0);
  await expect(page.getByText("This page could not be found")).toHaveCount(0);
  expect(problems).toEqual([]);
});

test("printer detail route preserves the dynamic id and renders live status", async ({ page }) => {
  const problems = await collectPageProblems(page);

  await page.goto("/printers/3");

  await expect(page.getByRole("heading", { name: "ender" })).toBeVisible();
  await expect(page.getByText("Moonraker", { exact: true })).toBeVisible();
  await expect(page.getByText("ready", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Temperatures")).toBeVisible();
  await page.getByRole("button", { name: "Files" }).click();
  await expect(page.getByText("skadis_kitchen-roll_screw_PLA_30m12s.gcode")).toBeVisible();
  await expect(page.getByText("Failed to fetch")).toHaveCount(0);
  await expect(page.getByText("This page could not be found")).toHaveCount(0);
  await expect(page).toHaveURL(/\/printers\/3$/);

  const html = await page.content();
  expect(html).not.toContain("printerId\":\"$NaN");
  expect(problems).toEqual([]);
});

test("gallery upload queues a task and tracks it to completion", async ({ page }) => {
  const problems = await collectPageProblems(page);

  // Auth is seeded in beforeEach.
  await page.goto("/");

  await expect(page.getByRole("link", { name: /upload/i })).toHaveCount(0);
  await page.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByRole("dialog", { name: "Upload model" })).toBeVisible();

  await page.locator('input[accept=".gcode,.g,.gco"]').setInputFiles({
    name: "cube.gcode",
    mimeType: "text/plain",
    buffer: Buffer.from("; generated by test\n"),
  });
  await page.getByPlaceholder("e.g. Bracket v2").fill("Cube");
  await page.getByRole("button", { name: /upload to vault/i }).click();

  await expect(page.getByRole("dialog", { name: "Upload model" })).toHaveCount(0);
  await page.getByRole("button", { name: "Notifications" }).click();
  await expect(page.getByText("Upload Cube")).toBeVisible();
  await expect(page.getByText("completed", { exact: true })).toBeVisible();
  await expect(page.getByText("running", { exact: true })).toHaveCount(0);

  expect(problems).toEqual([]);
});
