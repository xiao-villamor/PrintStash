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
import { aModel, aModelListItem, aMultipartModel } from "../../src/test-support/factories";

useMockApi();

test.describe("vault route", () => {
  test("preserves range selection across a page append", async ({ page }) => {
    const models = Array.from({ length: 4 }, (_, index) =>
      aModelListItem({ id: 100 + index, name: `Selection ${index + 1}` }),
    );
    await page.route("**/api/v1/models/browse?**", (route) => {
      const next = new URL(route.request().url()).searchParams.has("cursor");
      return route.fulfill({
        json: {
          items: (next ? models.slice(2) : models.slice(0, 2)).map((model) => ({
            kind: "model",
            model,
          })),
          total: 4,
          next_cursor: next ? null : "second",
          browse_revision: "r1",
          authorization_revision: "a1",
        },
      });
    });
    await page.goto("/?type=all&sort=name-asc");
    await page.getByRole("button", { name: "Library tools" }).click();
    await page.getByRole("button", { name: "Select", exact: true }).click();
    await page.getByRole("checkbox", { name: "Select Selection 2", exact: true }).click();
    await page.getByRole("button", { name: "Load more", exact: true }).click();

    await page.locator('main [data-library-entry="/models/103"]').click({ modifiers: ["Shift"] });

    await expect(
      page.getByRole("checkbox", { name: "Select Selection 1", exact: true }),
    ).not.toBeChecked();
    await expect(
      page.getByRole("checkbox", { name: "Select Selection 2", exact: true }),
    ).toBeChecked();
    await expect(
      page.getByRole("checkbox", { name: "Select Selection 3", exact: true }),
    ).toBeChecked();
    await expect(
      page.getByRole("checkbox", { name: "Select Selection 4", exact: true }),
    ).toBeChecked();
  });

  test("reviews an interrupted batch in the browser", async ({ page }) => {
    let writes = 0;
    await page.route("**/api/v1/models/browse?**", (route) =>
      route.fulfill({
        json: {
          items: [{ kind: "model", model: aModelListItem({ id: 1, name: "Review bracket" }) }],
          total: 1,
          next_cursor: null,
          browse_revision: "r1",
          authorization_revision: "a1",
        },
      }),
    );
    await page.route("**/api/v1/models/batch/tags", (route) => {
      writes += 1;
      return route.abort("failed");
    });
    await page.route("**/api/v1/models/1", (route) =>
      route.fulfill({
        json: aModel({ id: 1, name: "Current bracket", tags: ["functional"], edit_version: 9 }),
      }),
    );
    await page.goto("/");
    await page.getByRole("button", { name: "Library tools" }).click();
    await page.getByRole("button", { name: "Select", exact: true }).click();
    await page.getByRole("checkbox", { name: "Select Review bracket" }).click();
    await page.getByRole("button", { name: "Tag", exact: true }).click();
    const editor = page.getByRole("dialog");
    await editor.getByRole("combobox").first().fill("functional");
    await editor.getByRole("combobox").first().press("Enter");

    await editor.getByRole("button", { name: /Apply/ }).click();

    const recovery = page.getByRole("dialog", { name: "Batch needs review" });
    await expect(recovery.getByText("Add tags: functional", { exact: true })).toBeVisible();
    await expect(recovery.getByText("1 unconfirmed", { exact: true })).toBeVisible();
    await expect(recovery.getByRole("button", { name: "Undo confirmed changes" })).toHaveCount(0);
    await recovery.getByRole("link", { name: "Review bracket" }).click();
    await expect(page).toHaveURL((url) => url.pathname === "/models/1");
    await expect(page.getByRole("heading", { name: "Current bracket", exact: true })).toBeVisible();
    expect(writes).toBe(1);
  });

  test("refreshes a changed library deliberately", async ({ page }) => {
    const browseRequests: string[] = [];
    let changed = false;
    const thumbnailRead = page.waitForResponse(
      (response) => new URL(response.url()).pathname === "/api/v1/models/browse/thumbnails",
    );
    await page.route("**/api/v1/models/browse/revision", (route) =>
      route.fulfill({
        json: { browse_revision: changed ? "r2" : "r1", authorization_revision: "a1" },
      }),
    );
    await page.route("**/api/v1/models/browse?**", (route) => {
      browseRequests.push(route.request().url());
      return route.fulfill({
        json: {
          items: [
            {
              kind: "model",
              model: aModelListItem({
                name: changed ? "Current bracket" : "Original bracket",
                thumbnail_url: null,
              }),
            },
          ],
          total: changed ? 1 : 2,
          next_cursor: changed ? null : "old",
          browse_revision: changed ? "r2" : "r1",
          authorization_revision: "a1",
        },
      });
    });
    await page.goto("/");
    await expect(page.getByText("Original bracket", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Load more", exact: true })).toBeVisible();
    const initialReads = browseRequests.length;
    changed = true;
    await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
    await thumbnailRead;
    await expect(
      page.getByText("The library has changed. Refresh before loading more results."),
    ).toBeVisible();
    await expect(page.getByText("Original bracket", { exact: true })).toBeVisible();
    expect(browseRequests).toHaveLength(initialReads);
    await expect(page.getByRole("button", { name: "Load more", exact: true })).toHaveCount(0);

    await page.getByRole("button", { name: "Refresh library" }).click();

    await expect(page.getByText("Current bracket", { exact: true })).toBeVisible();
    await expect(page.getByText("Original bracket", { exact: true })).toHaveCount(0);
    expect(browseRequests).toHaveLength(initialReads + 1);
    expect(browseRequests.every((url) => !new URL(url).searchParams.has("cursor"))).toBe(true);
  });

  test("reports a conflicting batch undo", async ({ page }) => {
    await page.route("**/api/v1/models/browse?**", (route) =>
      route.fulfill({
        json: {
          items: [
            {
              kind: "model",
              model: aModelListItem({ id: 1, name: "Undo bracket", edit_version: 3 }),
            },
          ],
          total: 1,
          next_cursor: null,
          browse_revision: "r1",
          authorization_revision: "a1",
        },
      }),
    );
    await page.route("**/api/v1/models/batch/tags", (route) => {
      expect(route.request().postDataJSON().expected_versions).toEqual({
        1: { edit_epoch: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", edit_version: 3 },
      });
      return route.fulfill({
        json: {
          succeeded_ids: [1],
          succeeded_count: 1,
          succeeded_versions: {
            1: { edit_epoch: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", edit_version: 9 },
          },
          failed: [],
          failed_count: 0,
        },
      });
    });
    const undoVersions: (string | undefined)[] = [];
    await page.route("**/api/v1/models/1", (route) => {
      if (route.request().method() !== "PATCH") return route.continue();
      undoVersions.push(route.request().headers()["if-match"]);
      return route.fulfill({ status: 412, json: { detail: "edit_conflict" } });
    });
    await page.goto("/");
    await page.getByRole("button", { name: "Library tools" }).click();
    await page.getByRole("button", { name: "Select", exact: true }).click();
    await page.getByRole("checkbox", { name: "Select Undo bracket" }).click();
    await page.getByRole("button", { name: "Tag", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("combobox").first().fill("functional");
    await dialog.getByRole("combobox").first().press("Enter");
    await dialog.getByRole("button", { name: /Apply/ }).click();
    await page.getByRole("button", { name: "Undo", exact: true }).click();

    await expect(page.getByText("1 skipped", { exact: true })).toBeVisible();
    await expect(page.getByText("Tags restored", { exact: true })).toHaveCount(0);
    expect(undoVersions).toEqual(['"model-1-eaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-v9"']);
  });

  test("removes a favorite after browser confirmation", async ({ page }) => {
    const confirmation = Promise.withResolvers<void>();
    await page.route("**/api/v1/models/browse?**", (route) =>
      route.fulfill({
        json: {
          items: [
            {
              kind: "model",
              model: aModelListItem({ id: 1, name: "Favorite bracket", starred: true }),
            },
          ],
          total: 1,
          next_cursor: null,
          browse_revision: "r1",
          authorization_revision: "a1",
        },
      }),
    );
    await page.route("**/api/v1/models/1/star", async (route) => {
      await confirmation.promise;
      await route.fulfill({ json: { model_id: 1, starred: false } });
    });
    await page.goto("/?favorites=true");

    await page.getByRole("button", { name: "Remove Favorite bracket from favorites" }).click();

    await expect(page.getByText("Favorite bracket", { exact: true })).toBeVisible();
    confirmation.resolve();
    await expect(page.getByText("Favorite bracket", { exact: true })).toHaveCount(0);
  });

  test("preserves mixed order through browser pagination", async ({ page }) => {
    await page.route("**/api/v1/models/browse?**", async (route) => {
      const continued = new URL(route.request().url()).searchParams.has("cursor");
      await route.fulfill({
        json: {
          items: continued
            ? [{ kind: "model", model: aModelListItem({ name: "Älpha", thumbnail_url: null }) }]
            : [
                {
                  kind: "multipart",
                  multipart: aMultipartModel({ name: "Zeta", cover_thumbnail_url: null }),
                },
              ],
          total: 2,
          next_cursor: continued ? null : "next",
          browse_revision: "r1",
          authorization_revision: "a1",
        },
      });
    });
    await page.goto("/?type=all&sort=name-asc");
    await expect(page.getByRole("link", { name: /Zeta/ })).toBeVisible();

    await page.getByRole("button", { name: "Load more" }).click();

    const cards = page
      .getByRole("main")
      .locator("article")
      .filter({ hasText: /Zeta|Älpha/ });
    await expect(cards).toHaveCount(2);
    await expect(cards.nth(0)).toContainText("Zeta");
    await expect(cards.nth(1)).toContainText("Älpha");
  });

  test("continues an empty browse page in the browser", async ({ page }) => {
    await page.route("**/api/v1/models/browse?**", async (route) => {
      const continued = new URL(route.request().url()).searchParams.has("cursor");
      await route.fulfill({
        json: {
          items: continued
            ? [
                {
                  kind: "model",
                  model: aModelListItem({ name: "Reached match", thumbnail_url: null }),
                },
              ]
            : [],
          total: 1,
          next_cursor: continued ? null : "next",
          browse_revision: "r1",
          authorization_revision: "a1",
        },
      });
    });
    await page.goto("/?file_type=stl");

    await page.getByRole("button", { name: "Load more" }).click();

    await expect(page.getByText("Reached match", { exact: true })).toBeVisible();
  });

  test("restores library mode after collection history", async ({ page }) => {
    await page.goto("/?type=multipart&sort=name-asc");
    await expect(
      page.getByRole("button", { name: "Multipart sets only", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByRole("button", { name: "Organized", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Parts only", exact: true })).toHaveCount(0);
    await page
      .getByRole("main")
      .getByRole("button", { name: /maraio/ })
      .click();
    await expect(page.getByRole("heading", { name: "maraio" })).toBeVisible();
    await page.getByRole("button", { name: "Everything", exact: true }).click();
    await expect(page).toHaveURL(/[?&]type=all(?:&|$)/);

    await page.goBack();

    await expect(page.getByRole("heading", { name: "All Models" })).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Multipart sets only", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByRole("button", { name: "Sort models" })).toContainText("Name A–Z");
  });

  test("browser Back returns through collection navigation", async ({ page }) => {
    await page.goto("/settings");
    await page.getByRole("link", { name: "PrintStash" }).click();
    await expect(page.getByRole("heading", { name: "All Models" })).toBeVisible();

    await page
      .getByRole("main")
      .getByRole("button", { name: /maraio/ })
      .click();
    await expect(page).toHaveURL(/[?&]c=maraio(?:&|$)/);
    await expect(page.getByRole("heading", { name: "maraio" })).toBeVisible();

    await page.goBack();
    await expect(page).toHaveURL(/\/\?type=all&sort=date-desc$/);
    await expect(page.getByRole("heading", { name: "All Models" })).toBeVisible();

    await page.goBack();
    await expect(page).toHaveURL(/\/settings$/);
  });

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
      if (url.pathname === "/api/v1/models/browse") pageRequests.push(url.search);
    });
    await page.goto("/");
    await expect(page.getByText("skadis_kitchen-roll_screw").first()).toBeVisible();

    await page.getByRole("button", { name: "Sort models" }).click();
    await Promise.all([
      page.waitForRequest((request) => {
        const url = new URL(request.url());
        return (
          url.pathname === "/api/v1/models/browse" &&
          url.searchParams.get("sort") === "success-desc"
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

  test("mobile vault prepares bounded tree roots before opening the drawer", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const roots: URL[] = [];
    page.on("response", (response) => {
      const url = new URL(response.url());
      if (url.pathname === "/api/v1/outliner/collections" && response.ok()) roots.push(url);
    });

    await page.goto("/");
    await expect(page.getByText("skadis_kitchen-roll_screw").first()).toBeVisible();
    await expect.poll(() => roots.length).toBeGreaterThan(0);
    expect(roots.every((url) => url.searchParams.get("limit") === "50")).toBe(true);
    await expect(page.getByRole("dialog", { name: "Filters", exact: true })).toBeHidden();
    const restoredReads = roots.length;
    await page.getByRole("button", { name: "Filters", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "Filters", exact: true })).toBeVisible();
    await page.waitForTimeout(200);
    expect(roots).toHaveLength(restoredReads);
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
    await expect(page.getByText(/^\d+ items? shown$/)).toBeVisible();
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
                library_view: "all",
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
