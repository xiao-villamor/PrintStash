/*
 * The shared navigation must remain usable while the shell changes its geometry.
 * The rail keeps native route links and a keyboard-operated account menu; phones
 * retain access through the bottom navigation. The approved theme also separates
 * the blue command fill from neutral selection in both color schemes.
 */
import { expect, test } from "@playwright/test";

import { useMockApi } from "./_setup";

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

  test("uses the floating radius on the workspace menu", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "My workspace" }).click();
    await expect(page.getByRole("menu")).toHaveCSS("border-radius", "8px");
  });

  test("uses the control radius on the native sort select", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("combobox", { name: "Sort models" })).toHaveCSS(
      "border-radius",
      "6px",
    );
  });

  test("uses the floating radius on the native select picker", async ({ page }) => {
    await page.goto("/");
    const sort = page.getByRole("combobox", { name: "Sort models" });
    await expect(sort).toBeVisible();
    expect(
      await sort.evaluate((element) => getComputedStyle(element, "::picker(select)").borderRadius),
    ).toBe("8px");
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
