import { test, expect, type Page } from "@playwright/test";
import type { Server } from "node:http";

import { setExternalLibrariesEnabled, startMockApi } from "./mock-api";

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

test("model detail uses focused send dialog and compact actions", async ({ page }) => {
  await page.goto("/models/1");

  await page.getByRole("button", { name: "Model actions" }).click();
  await expect(page.getByRole("menuitem", { name: "Share" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "Edit details" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "Delete model" })).toBeVisible();
  await page.keyboard.press("Escape");

  await page.getByRole("button", { name: "Send to printer" }).last().click();
  const dialog = page.getByRole("dialog", { name: "Send to printer" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("checkbox", { name: "Select ender" })).toBeChecked();
  await expect(dialog.getByLabel("G-code revision")).toBeVisible();
  await expect(dialog.getByRole("checkbox", { name: "Start print immediately" })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Send to printer" })).toBeEnabled();
  const scrollRegion = dialog.getByTestId("send-dialog-scroll-region");
  const [scrollBox, revisionBox] = await Promise.all([
    scrollRegion.boundingBox(),
    dialog.getByLabel("G-code revision").boundingBox(),
  ]);
  expect(scrollBox).not.toBeNull();
  expect(revisionBox).not.toBeNull();
  expect(revisionBox!.x - scrollBox!.x).toBeGreaterThanOrEqual(2);
});

test("model detail tabs fit and details sidebar width persists", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/models/1");

  const sidebar = page.getByTestId("model-detail-sidebar");
  const tablist = sidebar.getByRole("tablist");
  const lastTab = tablist.getByRole("tab", { name: /History/ });
  const [tablistBox, lastTabBox] = await Promise.all([
    tablist.boundingBox(),
    lastTab.boundingBox(),
  ]);
  expect(tablistBox).not.toBeNull();
  expect(lastTabBox).not.toBeNull();
  expect(lastTabBox!.x + lastTabBox!.width).toBeLessThanOrEqual(
    tablistBox!.x + tablistBox!.width + 1,
  );

  const initialWidth = (await sidebar.boundingBox())!.width;
  const resizeHandle = page.getByRole("separator", { name: "Resize details panel" });
  const handleBox = await resizeHandle.boundingBox();
  expect(handleBox).not.toBeNull();
  await page.mouse.move(handleBox!.x + handleBox!.width / 2, handleBox!.y + 100);
  await page.mouse.down();
  await page.mouse.move(handleBox!.x - 120, handleBox!.y + 100, { steps: 5 });
  await page.mouse.up();

  await expect.poll(async () => (await sidebar.boundingBox())!.width).toBeGreaterThan(initialWidth + 100);
  const resizedWidth = (await sidebar.boundingBox())!.width;
  await page.reload();
  await expect.poll(async () => (await sidebar.boundingBox())!.width).toBeCloseTo(resizedWidth, 0);
});

test("add revision modal uses designed file picker and labeled fields", async ({ page }) => {
  await page.goto("/models/1");
  await page.getByRole("tab", { name: /Revisions/ }).click();
  await page.getByRole("button", { name: "Add", exact: true }).click();

  const dialog = page.getByRole("dialog", { name: "Add G-code revision" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Choose G-code or drop it here" })).toBeVisible();
  await expect(dialog.getByLabel(/Revision label/)).toBeVisible();
  await expect(dialog.getByLabel(/Notes/)).toBeVisible();
  await expect(dialog.getByRole("checkbox", { name: "Mark as recommended" })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Add revision" })).toBeDisabled();

  await dialog.locator(`input[accept=".gcode,.g,.gco"]`).setInputFiles({
    name: "stronger-walls.gcode",
    mimeType: "text/plain",
    buffer: Buffer.from("; generated by OrcaSlicer\nG28\n"),
  });
  await expect(dialog.getByText("stronger-walls.gcode")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Add revision" })).toBeEnabled();
});

test("printer list route loads configured printers through the frontend proxy", async ({ page }) => {
  const problems = await collectPageProblems(page);

  await page.goto("/printers");

  await expect(page.getByRole("heading", { name: "Printers" })).toBeVisible();
  await expect(page.getByRole("link", { name: /ender/i })).toBeVisible();
  await expect(page.getByText("Moonraker", { exact: true })).toBeVisible();
  await expect(page.getByText("ready", { exact: true }).first()).toBeVisible();
  await expect(page.getByLabel("Fleet summary").getByText("Ready")).toBeVisible();
  await expect(page.getByText("No printers configured yet.")).toHaveCount(0);
  await expect(page.getByText("Failed to fetch")).toHaveCount(0);
  await expect(page.getByText("This page could not be found")).toHaveCount(0);
  expect(problems).toEqual([]);
});

test("vault display choice survives reload", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Display" }).click();
  await page.getByRole("menuitem", { name: "List View" }).click();
  await expect(page.getByText("Thumb", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText("Thumb", { exact: true })).toBeVisible();
});

test("vault sort requests one globally sorted cursor page", async ({ page }) => {
  const pageRequests: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/models/page") pageRequests.push(url.search);
  });
  await page.goto("/");
  await expect(page.getByText("skadis_kitchen-roll_screw").first()).toBeVisible();

  await page.getByRole("button", { name: "Sort models" }).click();
  await Promise.all([
    page.waitForRequest((request) => {
      const url = new URL(request.url());
      return (
        url.pathname === "/api/v1/models/page" &&
        url.searchParams.get("sort") === "success-desc"
      );
    }),
    page.getByRole("menuitem", { name: "Best success rate" }).click(),
  ]);
  await expect(page.getByText("skadis_kitchen-roll_screw").first()).toBeVisible();
  await page.waitForTimeout(200);

  expect(
    pageRequests.filter((query) => new URLSearchParams(query).get("sort") === "success-desc"),
  ).toHaveLength(1);
});

test("mobile vault skips the desktop outliner request", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const outlinerRequests: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/v1/models/outliner") {
      outlinerRequests.push(request.url());
    }
  });

  await page.goto("/");
  await expect(page.getByText("skadis_kitchen-roll_screw").first()).toBeVisible();
  await page.waitForTimeout(200);
  expect(outlinerRequests).toEqual([]);
});

test("settings sections are deep-linkable and preserve navigation state", async ({ page }) => {
  await page.goto("/settings?section=trash");
  await expect(page.getByRole("heading", { name: "Trash retention" })).toBeVisible();
  await page.getByRole("button", { name: "About" }).click();
  await expect(page).toHaveURL(/\/settings\?section=about$/);
  await expect(page.getByRole("heading", { name: "Latest changes" })).toBeVisible();
});

test("settings warns administrators when a newer release is available", async ({ page }) => {
  await page.goto("/settings");

  await expect(page.getByText("PrintStash v0.10.1 is available")).toBeVisible();
  await expect(page.getByRole("link", { name: "View release" })).toHaveAttribute(
    "href",
    "https://github.com/xiao-villamor/PrintStash/releases/tag/v0.10.1",
  );
});

test("profiles route renders detected filament and printer presets", async ({ page }) => {
  const problems = await collectPageProblems(page);

  await page.goto("/profiles");

  await expect(page.getByRole("heading", { name: "Profiles" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Filament presets" })).toBeVisible();
  await expect(page.getByLabel("Filament preset name 1")).toHaveValue("Generic PLA");
  await expect(page.getByLabel("Filament brand 1")).toHaveValue("Generic");
  await page.getByRole("tab", { name: /Printers/ }).click();
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
  await page.getByRole("tab", { name: "Settings" }).click();
  await expect(page.getByRole("heading", { name: "Printer settings" })).toBeVisible();
  const printerName = page.getByLabel("Name");
  await printerName.fill("Workshop printer");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByRole("heading", { name: "Workshop printer" })).toBeVisible();
  await page.getByRole("tab", { name: "Files" }).click();
  await expect(page.getByText("skadis_kitchen-roll_screw_PLA_30m12s.gcode")).toBeVisible();
  await expect(page.getByText("Failed to fetch")).toHaveCount(0);
  await expect(page.getByText("This page could not be found")).toHaveCount(0);
  await expect(page).toHaveURL(/\/printers\/3$/);

  const html = await page.content();
  expect(html).not.toContain("printerId\":\"$NaN");
  expect(problems).toEqual([]);
});

/** Computed animation-delay of every direct child of the staggered model grid. */
async function gridDelays(page: Page) {
  await page.goto("/");
  const grid = page.locator(".stagger-children").first();
  await expect(grid.locator("> *").first()).toBeAttached();
  return grid.evaluate((el) =>
    Array.from(el.children).map((c) => getComputedStyle(c).animationDelay),
  );
}

test("grid cards enter on a capped stagger", async ({ page }) => {
  const delays = await gridDelays(page);
  expect(delays.length).toBeGreaterThan(1);

  expect(delays[0]).toBe("0s");
  expect(delays[1]).toBe("0.03s");
  // The cap is the point: a full 60-card page must still land inside the 300ms
  // UI budget rather than marching in for two seconds.
  for (const delay of delays) {
    expect(Number.parseFloat(delay)).toBeLessThanOrEqual(0.27);
  }
});

test("reduced motion drops the grid stagger entirely", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });

  // The stagger rules are :nth-child (specificity 0,2,0); a naive
  // `.stagger-children > *` override loses to them and the grid keeps marching in.
  for (const delay of await gridDelays(page)) {
    expect(delay).toBe("0s");
  }
});

test("header and recent-folder menus stay above adjacent vault surfaces", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("ps-recent-folders", JSON.stringify(["maraio"])));
  await page.goto("/");

  const headerZ = await page.locator("header").evaluate((element) => Number(getComputedStyle(element).zIndex));
  const stickyZ = await page.locator(".sticky.top-0").evaluate((element) => Number(getComputedStyle(element).zIndex));
  expect(headerZ).toBeGreaterThan(stickyZ);

  await page.getByRole("button", { name: "Recent" }).click();
  const menuBox = await page.getByRole("menu").boundingBox();
  const sidebarBox = await page.locator("aside").boundingBox();
  expect(menuBox).not.toBeNull(); expect(sidebarBox).not.toBeNull();
  expect(menuBox!.x).toBeGreaterThanOrEqual(sidebarBox!.x + sidebarBox!.width);
});

test("gallery upload queues a task and tracks it to completion", async ({ page }) => {
  const problems = await collectPageProblems(page);

  // Auth is seeded in beforeEach.
  await page.goto("/");

  await expect(page.getByRole("link", { name: /upload/i })).toHaveCount(0);
  await page.getByRole("button", { name: "Upload", exact: true }).click();
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

test.describe("shared volumes enabled", () => {
  test.beforeEach(() => setExternalLibrariesEnabled(true));
  test.afterEach(() => setExternalLibrariesEnabled(false));

  test("upload modal surfaces the shared-volume write-back destination selector", async ({
    page,
  }) => {
    const problems = await collectPageProblems(page);

    await page.goto("/");
    await page.getByRole("button", { name: "Upload", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "Upload model" })).toBeVisible();

    // With mirroring on and a volume present, the "Store in" selector appears,
    // defaulting to vault and offering the shared volume as a write-back target.
    const destination = page.getByRole("combobox").filter({ hasText: "Vault storage" });
    await expect(destination).toBeVisible();
    await expect(
      page.getByRole("option", { name: /nas-main \(shared volume\)/ }),
    ).toBeAttached();

    expect(problems).toEqual([]);
  });
});
