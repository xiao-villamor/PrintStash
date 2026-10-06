/** Library detail return belongs to the exact displayed history entry. */
import { expect, test } from "@playwright/test";
import { useMockApi } from "./_setup";
import { aModelListItem, aMultipartModel } from "../../src/test-support/factories";

useMockApi();

for (const kind of ["Model", "Multipart set"] as const) {
  test(`restores the originating Library history entry for a ${kind}`, async ({ page }) => {
    const model = aModelListItem({ id: 1, name: "skadis_kitchen-roll_screw", starred: true });
    const multipart = aMultipartModel({ id: 90, name: "Navigation set", starred: true });
    await page.route("**/api/v1/models/browse?**", (route) =>
      route.fulfill({
        json: {
          items: kind === "Model" ? [{ kind: "model", model }] : [{ kind: "multipart", multipart }],
          total: 1,
          next_cursor: null,
          browse_revision: "r1",
          authorization_revision: "a1",
        },
      }),
    );
    await page.route("**/api/v1/multipart-models/90", (route) =>
      route.fulfill({ json: multipart }),
    );
    await page.goto("/?c=maraio&type=all&sort=name-asc&favorites=true");
    const label = kind === "Model" ? model.name : multipart.name;
    const link = page
      .getByRole("main")
      .getByRole("link", { name: new RegExp(label) })
      .first();
    await expect(link).toBeVisible();
    const origin = page.url();
    const key = await page.evaluate(() => window.history.state.key);
    await link.click();
    await expect(page.getByRole("heading", { name: label, exact: true })).toBeVisible();
    await page
      .getByRole("link", { name: kind === "Model" ? "Back" : "Multipart sets", exact: true })
      .click();

    await expect(page).toHaveURL(origin);
    await expect(link).toBeVisible();
    expect(await page.evaluate(() => window.history.state.key)).toBe(key);
    await page.goForward();
    await expect(page.getByRole("heading", { name: label, exact: true })).toBeVisible();
  });
}

for (const { layout, viewport } of [
  { layout: "grid", viewport: "desktop" },
  { layout: "list", viewport: "desktop" },
  { layout: "grid", viewport: "mobile" },
  { layout: "list", viewport: "mobile" },
] as const) {
  test(`restores the nested ${layout} reading position on ${viewport}`, async ({ page }) => {
    if (viewport === "mobile") await page.setViewportSize({ width: 390, height: 844 });
    await page.addInitScript((value) => localStorage.setItem("ps-vault-view", value), layout);
    const models = Array.from({ length: 40 }, (_, index) =>
      aModelListItem({ id: index + 100, name: `Reading position ${index}` }),
    );
    models[20] = aModelListItem({ id: 1, name: "skadis_kitchen-roll_screw" });
    await page.route("**/api/v1/models/browse?**", (route) =>
      route.fulfill({
        json: {
          items: models.map((model) => ({ kind: "model", model })),
          total: models.length,
          next_cursor: null,
          browse_revision: "r1",
          authorization_revision: "a1",
        },
      }),
    );
    await page.goto("/?c=maraio&type=all&sort=name-asc");
    const link = page
      .getByRole("main")
      .getByRole("link", { name: /skadis_kitchen-roll_screw/ })
      .first();
    await link.scrollIntoViewIfNeeded();
    const before = await link.evaluate((element) => ({
      top: element.getBoundingClientRect().top,
      offsets: Array.from(document.querySelectorAll("main, main .overflow-y-auto")).map(
        (node) => node.scrollTop,
      ),
    }));
    expect(Math.max(...before.offsets)).toBeGreaterThan(100);
    await link.click();
    await expect(
      page.getByRole("heading", { name: "skadis_kitchen-roll_screw", exact: true }),
    ).toBeVisible();
    await page.getByRole("link", { name: "Back", exact: true }).click();
    await expect(link).toBeVisible();
    await expect
      .poll(async () => link.evaluate((element) => element.getBoundingClientRect().top))
      .toBeCloseTo(before.top, 0);
    const offsets = await page
      .locator("main, main .overflow-y-auto")
      .evaluateAll((nodes) => nodes.map((node) => node.scrollTop));
    expect(offsets).toEqual(before.offsets);
    await page.goForward();
    await expect(
      page.getByRole("heading", { name: "skadis_kitchen-roll_screw", exact: true }),
    ).toBeVisible();
    await page.goBack();
    await expect
      .poll(async () => link.evaluate((element) => element.getBoundingClientRect().top))
      .toBeCloseTo(before.top, 0);
  });
}
