/**
 * The bottom navigation a phone gets instead of the sidebar.
 *
 * Mobile has a different navigation tree, not a narrower one, so its destinations have to
 * be reachable and its overflow sheet has to expose the rest. A desktop test cannot see
 * any of this, because the components are not rendered at all above the breakpoint.
 */
import { test, expect } from "./helpers";

// Mobile rework (new in 0.8.0): the bottom navigation bar + "More" sheet, shown
// only below the md breakpoint. Run this whole file at a phone viewport.
test.use({ viewport: { width: 390, height: 844 } });

// Scope to the bottom nav — the desktop sidebar has same-named links but is
// display:none at this width, which would otherwise make role queries ambiguous.
const bottomNav = (page: import("@playwright/test").Page) => page.locator("nav.fixed.bottom-0");

// The TanStack Query devtools overlay sits bottom-right in the dev build and
// overlaps the bottom nav at phone width, intercepting taps. Hide it.
async function hideDevtools(page: import("@playwright/test").Page) {
  await page.addStyleTag({ content: ".tsqd-parent-container{display:none!important}" });
}

test.describe("mobile layout", () => {
  test("opens Similar models from mobile navigation", async ({ page }) => {
    await page.goto("/");
    await hideDevtools(page);
    const nav = bottomNav(page);
    await expect(nav).toBeVisible();
    await expect(nav.getByRole("link", { name: "Library" })).toBeVisible();
    await nav.getByRole("button", { name: "More" }).click();
    await page.getByRole("menuitem", { name: "Similar models", exact: true }).click();
    await expect(page).toHaveURL(/\/library\/similar$/);
  });

  test("'More' sheet exposes overflow destinations and navigates", async ({ page }) => {
    await page.goto("/");
    await hideDevtools(page);
    await bottomNav(page).getByRole("button", { name: "More" }).click();

    // The bottom sheet holds overflow destinations and account actions.
    const sheet = page.getByRole("menu");
    await expect(sheet.getByRole("menuitem", { name: "Settings" })).toBeVisible();

    await sheet.getByRole("menuitem", { name: "Settings" }).click();
    await expect(page).toHaveURL(/\/settings$/);
    await expect(page.getByRole("heading", { name: "Settings", level: 1 })).toBeVisible();
  });
});
