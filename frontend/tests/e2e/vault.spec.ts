/*
 * The vault, and the three requests it must get right.
 *
 * Sorting is server-owned: one cursor page, globally sorted. A client that
 * re-sorted the page it already has paginates a different order than the one it
 * displays, which surfaces as models appearing twice or not at all as the user
 * scrolls.
 *
 * The display choice survives a reload, because it is a preference and a
 * preference that resets is worse than no preference.
 *
 * Mobile skips the outliner request entirely. The outliner is not rendered on a
 * phone, so fetching its tree is a wasted round trip on the connection least able
 * to afford one.
 */
import { expect, test, type Locator, type Page } from "@playwright/test";

import { useMockApi } from "./_setup";

useMockApi();

test.describe("vault route", () => {
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
          url.pathname === "/api/v1/models/page" && url.searchParams.get("sort") === "success-desc"
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
});

/*
 * The same vault at phone width, where every control has to still be reachable.
 *
 * Below ~500px the toolbar collapses: Upload, Filters and sort stay on the bar,
 * and everything else moves behind one "More" menu. That menu is then the only
 * route to half the vault's controls, so a control that falls out of it is
 * unreachable on a phone with nothing to explain why — and the desktop tests
 * cannot see it, because at desktop width the menu does not exist.
 *
 * Two of these are geometry rather than behaviour, deliberately. A row whose
 * buttons differ in height or drift onto a second line is what a broken
 * breakpoint looks like, and a document wider than the viewport puts a
 * horizontal scrollbar over the controls to the right of it. Neither shows up in
 * any assertion about text.
 *
 * The saved-views picker is here because it is a dialog opened *from* a menu, and
 * that nesting is where focus goes wrong: arrow keys have to stay inside the
 * picker rather than moving the menu behind it, and dismissing it has to return
 * focus to the item that opened it or a keyboard user is left nowhere.
 */
test.describe("vault route on a phone-width viewport", () => {
  /** A tap target below ~40px is one a thumb misses. */
  async function expectTouchTarget(locator: Locator): Promise<void> {
    const box = await locator.boundingBox();
    expect(box).not.toBeNull();
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(40);
  }

  async function gotoMobileVault(page: Page, width = 360): Promise<void> {
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/");
  }

  function mobileMore(page: Page): Locator {
    return page.getByRole("main").getByRole("button", { name: "More", exact: true });
  }

  async function openMobileMore(page: Page): Promise<Locator> {
    await mobileMore(page).click();
    const menu = page.getByRole("menu").last();
    await expect(menu).toBeVisible();
    return menu;
  }

  test("names the collection it is showing", async ({ page }) => {
    await gotoMobileVault(page);

    await expect(page.getByRole("heading", { name: "All Models" })).toBeVisible();
    await expect(page.getByText(/^\d+ models? total$/)).toBeVisible();
  });

  test("offers exactly one Upload button", async ({ page }) => {
    // The desktop bar and the mobile bar both render one; rendering both at once
    // is the breakpoint bug, and two identical primary actions is confusing
    // rather than merely redundant.
    await gotoMobileVault(page);

    const uploads = page.getByRole("button", { name: "Upload", exact: true });
    await expect(uploads).toBeVisible();
    expect(
      await uploads.evaluateAll(
        (elements) =>
          elements.filter((element) => {
            const box = element.getBoundingClientRect();
            return box.width > 0 && box.height > 0;
          }).length,
      ),
    ).toBe(1);
  });

  test("keeps the three bar controls on one row of equal height", async ({ page }) => {
    await gotoMobileVault(page);
    const filters = page.getByRole("button", { name: "Filters", exact: true });
    const sort = page.getByRole("button", { name: "Sort models", exact: true });
    const more = mobileMore(page);

    await expectTouchTarget(filters);
    await expectTouchTarget(sort);
    await expectTouchTarget(more);

    const boxes = await Promise.all([
      filters.boundingBox(),
      sort.boundingBox(),
      more.boundingBox(),
    ]);
    expect(boxes.every((box) => box !== null)).toBe(true);
    expect(new Set(boxes.map((box) => Math.round(box?.height ?? 0))).size).toBe(1);
    expect(new Set(boxes.map((box) => Math.round(box?.y ?? 0))).size).toBe(1);
    expect(boxes[0]?.x).toBeLessThan(boxes[1]?.x ?? 0);
    expect(boxes[1]?.x).toBeLessThan(boxes[2]?.x ?? 0);
    if (process.env.PRINTSTASH_CAPTURE_UI) {
      await page.screenshot({ path: "/tmp/printstash-all-models-360.png", fullPage: true });
    }
  });

  test("never scrolls sideways", async ({ page }) => {
    // A horizontal scrollbar on a phone hides the controls to the right of it.
    await gotoMobileVault(page);

    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
  });

  test("keeps the two most-used controls out of the More menu", async ({ page }) => {
    // They are the two a user reaches for most; one tap, not two.
    await gotoMobileVault(page);

    await expect(page.getByRole("button", { name: "Filters", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Sort models", exact: true })).toBeVisible();
  });

  test("reports the sort it applied from the toolbar", async ({ page }) => {
    await gotoMobileVault(page);
    const sort = page.getByRole("button", { name: "Sort models", exact: true });

    await sort.click();
    await page.getByRole("menuitem", { name: "Best success rate" }).click();

    await expect(sort).toContainText("Best success rate");
  });

  test("puts every secondary action in the More menu", async ({ page }) => {
    await gotoMobileVault(page);

    const menu = await openMobileMore(page);

    await expect(menu.getByRole("menuitemcheckbox", { name: "Favorites" })).toBeVisible();
    await expect(menu.getByRole("menuitem", { name: /Saved views/ })).toBeVisible();
    await expect(menu.getByRole("menuitemcheckbox", { name: "Select" })).toBeVisible();
    await expect(menu.getByRole("menuitem", { name: "Display" })).toBeVisible();
    if (process.env.PRINTSTASH_CAPTURE_UI) {
      await page.screenshot({ path: "/tmp/printstash-all-models-360-more.png", fullPage: true });
    }
  });

  test("reports a toggle as off before it is used", async ({ page }) => {
    // A checkbox with no checked state is one a screen reader cannot report, and
    // the menu closes after each use — so its state is the only memory of it.
    await gotoMobileVault(page);

    const menu = await openMobileMore(page);

    await expect(menu.getByRole("menuitemcheckbox", { name: "Favorites" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
    await expect(menu.getByRole("menuitemcheckbox", { name: "Select" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  test("filters to favourites from the menu", async ({ page }) => {
    await gotoMobileVault(page);
    const menu = await openMobileMore(page);

    await menu.getByRole("menuitemcheckbox", { name: "Favorites" }).click();

    await expect(page).toHaveURL(/[?&]favorites=true/);
  });

  test("reports the favourites toggle as on afterwards", async ({ page }) => {
    await gotoMobileVault(page);
    const menu = await openMobileMore(page);
    await menu.getByRole("menuitemcheckbox", { name: "Favorites" }).click();

    await openMobileMore(page);

    await expect(page.getByRole("menuitemcheckbox", { name: "Favorites" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  test("offers a way out of selection mode", async ({ page }) => {
    // Entering it renames the item to "Done"; without that the only escape is a
    // reload, and the user has a bar of batch actions they cannot dismiss.
    await gotoMobileVault(page);
    const menu = await openMobileMore(page);
    await menu.getByRole("menuitemcheckbox", { name: "Select" }).click();

    await openMobileMore(page);

    await expect(page.getByRole("menuitemcheckbox", { name: "Done" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  test("switches the display from the menu", async ({ page }) => {
    await gotoMobileVault(page);
    const menu = await openMobileMore(page);

    await menu.getByRole("menuitem", { name: "Display" }).click();
    await page.getByRole("menuitem", { name: "List View" }).click();

    await expect(page.getByText("Thumb", { exact: true })).toBeVisible();
  });

  test.describe("the saved-views picker opened from the menu", () => {
    test.beforeEach(async ({ page }) => {
      await page.route("**/api/v1/saved-views", async (route) => {
        await route.fulfill({
          contentType: "application/json",
          body: JSON.stringify([
            {
              id: 7,
              name: "Ready to print",
              filters: {
                collection: null,
                direct: true,
                tag: [],
                q: "skadis",
                printer_id: null,
                printer_presence: null,
                favorites: false,
              },
              created_at: "2026-06-04T00:24:22.000000",
              updated_at: "2026-06-04T00:24:22.000000",
            },
          ]),
        });
      });
    });

    test("opens focused on its search field", async ({ page }) => {
      await gotoMobileVault(page);
      const menu = await openMobileMore(page);

      await menu.getByRole("menuitem", { name: /Saved views/ }).click();

      await expect(page.getByRole("dialog")).toBeVisible();
      await expect(page.getByRole("textbox", { name: "Find a saved view" })).toBeFocused();
    });

    test("keeps the arrow keys inside itself", async ({ page }) => {
      // The menu behind it also answers to arrows; letting them through moves a
      // selection the user cannot see.
      await gotoMobileVault(page);
      const menu = await openMobileMore(page);
      await menu.getByRole("menuitem", { name: /Saved views/ }).click();
      const search = page.getByRole("textbox", { name: "Find a saved view" });

      await search.press("ArrowDown");

      await expect(search).toBeFocused();
    });

    test("returns focus to the item that opened it", async ({ page }) => {
      // Escape from a nested dialog otherwise drops focus to the document, and a
      // keyboard user has to tab in from the top of the page again.
      await gotoMobileVault(page);
      const menu = await openMobileMore(page);
      const savedViews = menu.getByRole("menuitem", { name: /Saved views/ });
      await savedViews.click();

      await page.getByRole("textbox", { name: "Find a saved view" }).press("Escape");

      await expect(page.getByRole("dialog")).toBeHidden();
      await expect(savedViews).toBeFocused();
    });

    test("applies the view the user chose", async ({ page }) => {
      await gotoMobileVault(page);
      const menu = await openMobileMore(page);
      await menu.getByRole("menuitem", { name: /Saved views/ }).click();
      const dialog = page.getByRole("dialog");

      await dialog.getByRole("button", { name: "Ready to print", exact: true }).click();

      await expect(page).toHaveURL(/[?&]q=skadis/);
      await expect(dialog).toBeHidden();
    });
  });
});
