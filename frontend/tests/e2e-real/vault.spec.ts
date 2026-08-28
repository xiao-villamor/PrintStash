/**
 * Finding a model in a library that has grown too big to scroll.
 *
 * Search, filter, and the empty state — the three outcomes of looking for something. The
 * empty state is a real assertion rather than an afterthought: a search that silently
 * renders nothing looks identical to one that is still loading, and a user cannot tell
 * whether their model is missing or the page is broken.
 */
import { test, expect } from "./helpers";
import { createCollectionViaVault, modelCard, uploadGcodeModel, uploadModel } from "./util";

test.describe("vault", () => {
  test("search filters the library; list/grid toggle keeps the model visible", async ({ page }) => {
    const name = `e2e-vault-${Date.now()}`;
    await uploadGcodeModel(page, name);

    // Search narrows the grid to the matching model and reflects in the URL.
    await page.getByPlaceholder("Search PrintStash...").fill(name);
    await expect(page).toHaveURL(/[?&]q=/);
    await expect(modelCard(page, name)).toBeVisible();

    // List / grid toggle (title-labelled buttons) both keep the result.
    await page.getByRole("button", { name: "Display" }).click();
    await page.getByRole("menuitem", { name: "List View" }).click();
    await expect(modelCard(page, name)).toBeVisible();
    await page.getByRole("button", { name: "Display" }).click();
    await page.getByRole("menuitem", { name: "Grid View" }).click();
    await expect(modelCard(page, name)).toBeVisible();
  });

  test("the toolbar stays reachable on a phone-width viewport", async ({ page }) => {
    // Every vault control collapses behind one "More" menu below ~500px, and
    // that menu is the only way to reach half of them. A control that falls out
    // of it is unreachable on a phone with no error to explain it — and a long
    // collection name that overflows takes the whole page sideways with it.
    const collection = `e2e-mobile-${Date.now()}-long-title-readable`;
    await createCollectionViaVault(page, collection);
    await page.setViewportSize({ width: 482, height: 844 });
    await page.goto(`/?c=${encodeURIComponent(collection)}`);

    const heading = page.getByRole("heading", { name: collection, exact: true });
    await expect(heading).toBeVisible();
    await expect(page.getByRole("button", { name: "Filters", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Upload", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Sort models", exact: true })).toBeVisible();
    const more = page.getByRole("main").getByRole("button", { name: "More", exact: true });
    await expect(more).toBeVisible();
    if (process.env.PRINTSTASH_CAPTURE_UI) {
      await page.screenshot({ path: "/tmp/printstash-collection-482.png", fullPage: true });
    }

    await more.click();

    const menu = page.getByRole("menu").last();
    await expect(menu.getByRole("menuitemcheckbox", { name: "Favorites" })).toBeVisible();
    await expect(menu.getByRole("menuitem", { name: /Saved views/ })).toBeVisible();
    await expect(menu.getByRole("menuitemcheckbox", { name: "Select" })).toBeVisible();
    await expect(menu.getByRole("menuitem", { name: "Display" })).toBeVisible();
    await menu.getByRole("menuitem", { name: "Display" }).click();
    await page.getByRole("menuitem", { name: "List View" }).click();
    await expect(more).toBeVisible();
    await expect
      .poll(() => page.evaluate(() => localStorage.getItem("ps-vault-view")))
      .toBe("list");
    // Neither the heading nor the document may scroll sideways: a horizontal
    // scrollbar on a phone hides the controls to the right of it.
    expect(await heading.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(
      true,
    );
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
  });

  test("the tag filter narrows the grid to tagged models", async ({ page }) => {
    const stamp = Date.now();
    const tag = `e2e-filter-${stamp}`;
    const tagged = `e2e-tagged-${stamp}`;
    const plain = `e2e-plain-${stamp}`;

    await uploadModel(page, tagged, { tag });
    await uploadGcodeModel(page, plain);

    // Click the tag chip in the sidebar; the grid keeps the tagged model and
    // drops the untagged one.
    await page.goto("/");
    await page.getByRole("button", { name: tag }).click();
    await expect(modelCard(page, tagged)).toBeVisible();
    await expect(modelCard(page, plain)).toHaveCount(0);
  });

  test("a meshless search term yields the empty state", async ({ page }) => {
    await page.goto("/");
    await page.getByPlaceholder("Search PrintStash...").fill(`no-such-model-${Date.now()}`);
    await expect(page).toHaveURL(/[?&]q=/);
    await expect(page.locator('a[href^="/models/"]')).toHaveCount(0);
  });
});
