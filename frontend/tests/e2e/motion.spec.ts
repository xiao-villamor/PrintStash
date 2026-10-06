/*
 * The design rules from DESIGN.md that only exist once a page is assembled.
 *
 * Library route results remain still in either motion preference. Entrance
 * transforms used to move semantic scroll anchors after restoration measured them.
 *
 * The layering row is here because z-index bugs are invisible until two surfaces
 * are open at once: the header menu and the recent-folder menu each work alone
 * and one renders under the other.
 */
import { expect, test } from "@playwright/test";

import { useMockApi } from "./_setup";
import { aModelListItem, aMultipartModel } from "../../src/test-support/factories";

useMockApi();

test.describe("motion and layering", () => {
  for (const reducedMotion of ["no-preference", "reduce"] as const) {
    test(`Library route results stay still with ${reducedMotion}`, async ({ page }) => {
      await page.emulateMedia({ reducedMotion });
      await page.route("**/api/v1/models/browse?**", (route) =>
        route.fulfill({
          json: {
            items: [
              { kind: "model", model: aModelListItem({ id: 1 }) },
              { kind: "model", model: aModelListItem({ id: 2 }) },
              { kind: "multipart", multipart: aMultipartModel({ id: 3 }) },
            ],
            total: 3,
            next_cursor: null,
            browse_revision: "r1",
            authorization_revision: "a1",
          },
        }),
      );
      await page.goto("/");
      const cards = page.getByRole("main").locator("article");
      await expect(cards.first()).toBeVisible();
      await expect(cards).toHaveCount(3);
      expect(
        await cards.evaluateAll((nodes) =>
          nodes.map((node) => getComputedStyle(node).animationName),
        ),
      ).toEqual(Array(await cards.count()).fill("none"));
    });
  }

  test("header and recent-folder menus stay above adjacent vault surfaces", async ({ page }) => {
    await page.addInitScript(() =>
      localStorage.setItem("ps-recent-folders", JSON.stringify(["maraio"])),
    );
    await page.goto("/");

    const headerZ = await page
      .locator("header")
      .evaluate((element) => Number(getComputedStyle(element).zIndex));
    const stickyZ = await page
      .locator(".sticky.top-0")
      .evaluate((element) => Number(getComputedStyle(element).zIndex));
    expect(headerZ).toBeGreaterThan(stickyZ);

    await page.getByRole("button", { name: "Recent" }).click();
    const menuBox = await page.getByRole("menu").boundingBox();
    const sidebarBox = await page.locator("aside").boundingBox();
    expect(menuBox).not.toBeNull();
    expect(sidebarBox).not.toBeNull();
    expect(menuBox!.x).toBeGreaterThanOrEqual(sidebarBox!.x + sidebarBox!.width);
  });
});
