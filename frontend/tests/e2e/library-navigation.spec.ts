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
