/*
 * "Pending Imports" is reachable, in both navigation shells, on nested routes.
 *
 * Desktop puts it in the profile menu and mobile in the bottom bar — two
 * components with two lists, so a route added to one is missing on the platform
 * nobody tested. It is also the screen a user goes to when an import is waiting
 * for them, which means an unreachable entry leaves the import sitting there.
 *
 * The nested-route half is the active-state prefix match: an inbox *detail* page
 * must still mark Pending as current, or the user appears to have navigated out
 * of the section they are looking at.
 */
import { expect, test } from "@playwright/test";

import { useMockApi } from "./_setup";

useMockApi();

test.describe("app navigation", () => {
  test("keeps the desktop header focused on search", async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto("/");
    await expect(page.locator("header").getByRole("link", { name: "Similar models" })).toHaveCount(
      0,
    );
    await expect(page.getByRole("searchbox", { name: "Search models", exact: true })).toBeVisible();
  });

  test("desktop toolbar opens Similar models", async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto("/");
    const actions = page.getByRole("main");
    await actions.getByRole("button", { name: "More", exact: true }).click();
    const similar = page.getByRole("menuitem", { name: "Similar models", exact: true });
    await expect(similar).toBeVisible();
    await expect(similar).toHaveAttribute("href", "/library/similar");
    await expect(page.locator('[aria-modal="true"]')).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath("similar-entry-desktop.png") });
    await similar.click();
    await expect(page).toHaveURL(/\/library\/similar$/);
    await expect(page.getByRole("heading", { name: "Similar models", exact: true })).toBeVisible();
  });

  test("mobile tab opens Similar models", async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "All Models" })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("similar-entry-mobile.png") });

    await page
      .getByRole("navigation", { name: "Navigation" })
      .getByRole("button", { name: "More", exact: true })
      .click();
    await page.getByRole("menuitem", { name: "Similar models", exact: true }).click();

    await expect(page).toHaveURL(/\/library\/similar$/);
    await expect(page.getByRole("heading", { name: "Similar models" })).toBeVisible();
  });

  test("desktop Pending panel stays reachable on nested routes", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    for (const route of ["/", "/inbox/41"]) {
      await page.goto(route);
      const pending = page
        .getByRole("navigation", { name: "Navigation" })
        .getByRole("link", { name: "Pending", exact: true });
      await expect(pending).toHaveAttribute("href", "/inbox");
      await pending.click();
      await expect(pending).toHaveAttribute("aria-expanded", "true");
      await expect(page.getByRole("complementary", { name: "Pending Imports" })).toBeVisible();
    }
  });

  test("mobile navigation reaches Pending Imports and stays active on detail routes", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");

    const pending = page.getByRole("link", { name: "Pending" });
    await expect(pending).toHaveAttribute("href", "/inbox");
    await pending.click();
    await expect(page).toHaveURL(/\/inbox$/);
    await expect(page.getByRole("heading", { name: "Pending Imports" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Pending" })).toHaveAttribute(
      "aria-current",
      "page",
    );

    await page.goto("/inbox/41");
    await expect(page.getByRole("link", { name: "Pending" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });
});

for (const width of [1280, 390]) {
  test(`${width === 1280 ? "desktop" : "mobile"} navigation omits Builds`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "All Models" })).toBeVisible();
    if (width === 1280) {
      await page.getByRole("button", { name: "My workspace" }).click();
      await expect(page.getByRole("menuitem", { name: "Builds", exact: true })).toHaveCount(0);
      await page.keyboard.press("Escape");
      await page
        .getByRole("navigation", { name: "Navigation" })
        .getByRole("link", { name: "Profiles", exact: true })
        .click();
    } else {
      await page.locator("nav").getByRole("button", { name: "More", exact: true }).click();
      const more = page.getByRole("menu");
      await expect(more.getByRole("menuitem", { name: "Builds", exact: true })).toHaveCount(0);
      await more.getByRole("menuitem", { name: "Profiles", exact: true }).click();
      await expect(more).toHaveCount(0);
    }
    await expect(page).toHaveURL(/\/profiles$/);
    await expect(page.getByRole("heading", { name: "Profiles", exact: true })).toBeVisible();
  });
}

test.describe("desktop workspace quick access", () => {
  test("keeps workspace choices within the viewport beside the rail", async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto("/");
    const collections = page.getByRole("complementary", { name: "Collections", exact: true });
    await expect(collections.getByRole("treeitem", { name: "maraio", exact: true })).toBeVisible();
    const trigger = page.getByRole("button", { name: "My workspace", exact: true });
    await trigger.click();
    const menu = page.getByRole("menu");
    await expect(menu.getByRole("menuitem", { name: "Statistics", exact: true })).toBeVisible();
    await expect(menu.getByRole("menuitem", { name: "Printers", exact: true })).toHaveCount(0);
    const box = await menu.boundingBox();
    const anchor = await trigger.boundingBox();
    expect(box).not.toBeNull();
    expect(anchor).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(anchor!.x + anchor!.width);
    expect(box!.x + box!.width).toBeLessThanOrEqual(1920);
    expect(box!.y).toBeGreaterThanOrEqual(0);
    expect(box!.y + box!.height).toBeLessThanOrEqual(1080);
    await page.keyboard.press("Escape");
    await expect(trigger).toBeFocused();
    await page
      .getByRole("navigation", { name: "Navigation" })
      .getByRole("link", { name: "Printers", exact: true })
      .click();
    await expect(page).toHaveURL(/\/printers$/);
    await expect(
      page
        .getByRole("navigation", { name: "Navigation" })
        .getByRole("link", { name: "Printers", exact: true }),
    ).toHaveAttribute("aria-current", "page");
  });
});

test.describe("desktop Saved views quick access", () => {
  test("opens saved views in the sidebar without covering the Library", async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto("/");
    const trigger = page
      .getByRole("navigation", { name: "Navigation" })
      .getByRole("button", { name: "Saved views", exact: true });
    await trigger.click();
    const panel = page.getByRole("complementary", { name: "Saved views", exact: true });
    await expect(panel).toBeVisible();
    await expect(panel.getByRole("button", { name: "Manage saved views" })).toBeVisible();
    const box = await panel.boundingBox();
    const content = await page.getByRole("main").boundingBox();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.y).toBeGreaterThanOrEqual(0);
    expect(box!.y + box!.height).toBeLessThanOrEqual(1080);
    expect(box!.x + box!.width).toBeLessThanOrEqual(content!.x);
    await page
      .getByRole("navigation", { name: "Navigation" })
      .getByRole("link", { name: "Collections", exact: true })
      .click();
    await expect(
      page.getByRole("complementary", { name: "Collections", exact: true }),
    ).toBeVisible();
  });
});

test.describe("language choices", () => {
  test("changes language from the workspace menu using the keyboard", async ({ page }) => {
    await page.goto("/");
    const trigger = page.getByRole("button", { name: "My workspace", exact: true });
    await trigger.click();
    const menu = page.getByRole("menu");
    const english = menu.getByRole("menuitemradio", { name: "English", exact: true });
    await expect(english).toHaveAttribute("aria-checked", "true");
    await english.focus();
    await page.keyboard.press("ArrowDown");
    const spanish = menu.getByRole("menuitemradio", { name: "Español", exact: true });
    await expect(spanish).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(spanish).toHaveAttribute("aria-checked", "true");
    await page.keyboard.press("Escape");
    await expect(menu).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "Mi espacio de trabajo", exact: true }),
    ).toBeFocused();
    await page.reload();
    await expect(
      page.getByRole("heading", { name: "Todos los modelos", exact: true }),
    ).toBeVisible();
  });
});
