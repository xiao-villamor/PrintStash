/*
 * The shared navigation must remain usable while the shell changes its geometry.
 * The rail keeps native route links and a keyboard-operated account menu; phones
 * retain access through the bottom navigation. The approved theme also separates
 * the blue command fill from neutral selection in both color schemes.
 */
import { expect, test } from "@playwright/test";

import { useMockApi } from "./_setup";
import { collapseNavigationSidebar } from "../e2e-real/util";

useMockApi();

test.describe("OpenDesign shell", () => {
  test("the desktop rail opens its search context", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    const rail = page.getByRole("navigation", { name: "Navigation" });
    await expect(page.getByRole("heading", { name: "All Models" })).toBeVisible();

    await rail.getByRole("link", { name: "Search" }).click();

    await expect(page.getByRole("complementary", { name: "Search panel" })).toBeVisible();
    await expect(rail.getByRole("link", { name: "Search" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  test("the rail account menu returns focus on Escape", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    const account = page
      .getByRole("navigation", { name: "Navigation" })
      .getByRole("button", { name: "My workspace" });

    await account.click();
    await expect(page.getByRole("menuitem", { name: "Log out" })).toBeVisible();
    await page.keyboard.press("Escape");

    await expect(account).toBeFocused();
  });

  for (const theme of ["light", "dark"] as const) {
    test(`${theme} keeps primary actions distinct from selection`, async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.addInitScript(
        (selectedTheme) => localStorage.setItem("printstash.theme", selectedTheme),
        theme,
      );
      await page.goto("/");
      const upload = page.getByRole("button", { name: "Upload", exact: true }).first();
      const current = page
        .getByRole("navigation", { name: "Navigation" })
        .getByRole("link", { name: "Collections" });

      await expect(upload).toHaveCSS("background-color", "rgb(77, 111, 155)");
      await expect(upload).toHaveCSS("border-radius", "6px");
      await expect(current).not.toHaveCSS("background-color", "rgb(77, 111, 155)");
    });
  }

  test("rounds the hovered navigation rail highlight", async ({ page }) => {
    await page.goto("/");
    await collapseNavigationSidebar(page);
    const link = page.getByRole("navigation", { name: "Navigation" }).getByRole("link", {
      name: "Printers",
    });

    await link.hover();

    await expect(link).toHaveCSS("border-radius", "6px");
  });

  test("rounds the focused workspace menu highlight", async ({ page }) => {
    await page.goto("/");
    await collapseNavigationSidebar(page);
    const account = page.getByRole("button", { name: "My workspace" });

    await account.focus();
    await account.press("Enter");
    const row = page.getByRole("menuitem", { name: "Statistics" });
    await row.focus();

    await expect(row).toHaveCSS("border-radius", "6px");
  });

  test("uses the floating radius on the workspace menu", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "My workspace" }).click();
    await expect(page.getByRole("menu")).toHaveCSS("border-radius", "8px");
  });

  for (const theme of ["light", "dark"] as const) {
    test(`uses a neutral toolbar surface for sorting in the ${theme} theme`, async ({ page }) => {
      await page.addInitScript(
        (selectedTheme) => localStorage.setItem("printstash.theme", selectedTheme),
        theme,
      );
      await page.goto("/");
      await collapseNavigationSidebar(page);
      const sort = page.getByRole("combobox", { name: "Sort models" });

      await expect(sort).toHaveCSS("border-radius", "6px");
      await expect(sort).toHaveCSS("border-top-color", "rgba(0, 0, 0, 0)");
      await expect(sort).not.toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
    });
  }

  test("applies a keyboard sort after reopening the Library", async ({ page }) => {
    await page.goto("/?sort=date-desc");
    const sort = page.getByRole("combobox", { name: "Sort models" });

    await sort.press("Space");
    await expect.poll(() => sort.evaluate((element) => element.matches(":open"))).toBe(true);
    await page.getByRole("option", { name: "Oldest", exact: true }).focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/sort=date-asc/);

    await page.goto("/");
    await expect(sort).toHaveValue("date-asc");
  });

  test("returns focus to sorting after dismissing the picker", async ({ page }) => {
    await page.goto("/?sort=date-desc");
    const sort = page.getByRole("combobox", { name: "Sort models" });

    await sort.click();
    await expect.poll(() => sort.evaluate((element) => element.matches(":open"))).toBe(true);
    await page.keyboard.press("Escape");

    await expect.poll(() => sort.evaluate((element) => element.matches(":open"))).toBe(false);
    await expect(sort).toBeFocused();
    await expect(sort).toHaveValue("date-desc");
  });

  test("uses the floating radius on the native select picker", async ({ page }) => {
    await page.goto("/");
    const sort = page.getByRole("combobox", { name: "Sort models" });
    await expect(sort).toBeVisible();
    expect(
      await sort.evaluate((element) => getComputedStyle(element, "::picker(select)").borderRadius),
    ).toBe("8px");
  });

  test("anchors the sort picker below when it fits", async ({ page }) => {
    await page.goto("/");
    const sort = page.getByRole("combobox", { name: "Sort models" });
    await sort.evaluate((element) => {
      element.style.position = "fixed";
      element.style.insetBlockStart = "60vh";
      element.style.insetInlineStart = "50vw";
    });
    await sort.press("Space");
    await expect.poll(() => sort.evaluate((element) => element.matches(":open"))).toBe(true);
    const trigger = await sort.boundingBox();
    const first = await sort.getByRole("option").first().boundingBox();
    expect(first!.y).toBeGreaterThanOrEqual(trigger!.y + trigger!.height);
  });

  test("opens the sort picker upward at the viewport edge", async ({ page }) => {
    await page.goto("/");
    const sort = page.getByRole("combobox", { name: "Sort models" });
    await sort.evaluate((element) => {
      element.style.position = "fixed";
      element.style.insetBlockEnd = "4px";
      element.style.insetInlineStart = "50vw";
    });
    await sort.press("Space");
    await expect.poll(() => sort.evaluate((element) => element.matches(":open"))).toBe(true);
    const trigger = await sort.boundingBox();
    const first = await sort.getByRole("option").first().boundingBox();
    expect(first!.y + first!.height).toBeLessThanOrEqual(trigger!.y);
  });

  test("the phone navigation remains usable at 320 pixels", async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 700 });
    await page.goto("/");
    // The development inspector launcher is absent from the shipping build.
    // Keep the pointer path equal to production rather than clicking through it.
    await page.addStyleTag({ content: ".tsqd-parent-container { display: none !important; }" });
    await expect(page.locator(".tsqd-parent-container")).toBeHidden();
    const more = page.getByRole("navigation").getByRole("button", { name: "More", exact: true });

    await more.click();

    await expect(page.getByRole("menu")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(320);
  });
});
