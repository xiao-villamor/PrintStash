import { test, expect } from "./helpers";

// The public /health is liveness-only (no version, to limit disclosure); the
// version lives on /health/details, gated to admins.
test("health endpoint reports the app version", async ({ page }) => {
  const res = await page.request.get("/api/v1/health/details");
  expect(res.ok()).toBeTruthy();
  const body = await res.json();
  expect(body.status).toBe("ok");
  expect(body.version).toMatch(/^\d+\.\d+/);
});

test("core routes load without uncaught errors", async ({ page }) => {
  const crashes: string[] = [];
  page.on("pageerror", (e) => crashes.push(e.message));

  for (const route of ["/", "/profiles", "/printers", "/statistics", "/settings"]) {
    await page.goto(route);
    await page.waitForLoadState("networkidle");
  }
  expect(crashes).toEqual([]);
});
