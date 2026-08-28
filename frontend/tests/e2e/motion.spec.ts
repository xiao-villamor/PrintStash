/*
 * The design rules from DESIGN.md that only exist once a page is assembled.
 *
 * The grid stagger is capped, and the cap is the point: a full 60-card page has
 * to land inside the 300ms UI budget rather than marching in for two seconds. A
 * per-card delay with no ceiling looks correct on a page of six.
 *
 * `prefers-reduced-motion` drops the stagger entirely. That is an accessibility
 * setting a user set for a reason, and honouring it partially is not honouring it.
 *
 * The layering row is here because z-index bugs are invisible until two surfaces
 * are open at once: the header menu and the recent-folder menu each work alone
 * and one renders under the other.
 */
import { expect, test } from "@playwright/test";

import { gridDelays, useMockApi } from "./_setup";

useMockApi();

test.describe("motion and layering", () => {
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
