/**
 * Saving a set of filters, and getting exactly those filters back.
 *
 * A saved view is a URL, so applying one has to restore the **canonical** URL rather than
 * an equivalent-looking one — otherwise the next save round-trips something different
 * from what the user is looking at. Favourites are here for the same reason: starring is
 * a filter, and it has to narrow the same grid.
 */
import { test, expect } from "./helpers";
import { modelCard, uploadModel } from "./util";

// Saved views (0.10.0): persist a filter combination, restore it from the URL,
// and manage it (rename/duplicate/delete). Favorites: star a model, then filter
// the grid down to starred models only.

// The trigger's accessible name is "Saved views" only while no view is active
// (it switches to the active view's name once one is applied) — target the
// stable `data-menu-trigger` + `aria-haspopup="dialog"` pair instead, scoped to
// `main` since the header's search-launcher button shares the same attributes.
function savedViewsTrigger(page: import("@playwright/test").Page) {
  return page.locator('main button[data-menu-trigger][aria-haspopup="dialog"]');
}

test.describe("saved views", () => {
  test("save current filters as a view, apply it restores the canonical URL", async ({ page }) => {
    const stamp = Date.now();
    const tag = `e2e-view-tag-${stamp}`;
    const tagged = `e2e-view-tagged-${stamp}`;
    const plain = `e2e-view-plain-${stamp}`;
    const viewName = `e2e-view-${stamp}`;
    const renamed = `${viewName}-renamed`;

    await uploadModel(page, tagged, { tag });
    await uploadModel(page, plain);

    // Filter to the tag, then save that as a view.
    await page.goto("/");
    await page.getByRole("button", { name: tag }).click();
    await expect(modelCard(page, tagged)).toBeVisible();
    await expect(modelCard(page, plain)).toHaveCount(0);
    await expect(page).toHaveURL(new RegExp(`tag=${encodeURIComponent(tag)}`));

    await savedViewsTrigger(page).click();
    await page.getByText("Save current view").click();
    await page.getByPlaceholder("Ready to print").fill(viewName);
    await page.getByRole("button", { name: "Save view" }).click();

    // Navigate away to the canonical, filter-less URL — a full reload, so no
    // saved view is active. Applying it from the menu must restore the exact
    // same result set + query string.
    await page.goto("/");
    await expect(modelCard(page, plain)).toBeVisible();
    await savedViewsTrigger(page).click();
    await page.getByRole("button", { name: viewName, exact: true }).click();

    await expect(page).toHaveURL(new RegExp(`tag=${encodeURIComponent(tag)}`));
    await expect(modelCard(page, tagged)).toBeVisible();
    await expect(modelCard(page, plain)).toHaveCount(0);

    // Rename — the trigger now shows the active view's name.
    await expect(savedViewsTrigger(page)).toContainText(viewName);
    await savedViewsTrigger(page).click();
    await page.getByLabel(`Rename ${viewName}`).click();
    await page
      .getByRole("dialog", { name: "Rename saved view" })
      .getByRole("textbox")
      .fill(renamed);
    await page.getByRole("button", { name: "Rename", exact: true }).click();
    await expect(savedViewsTrigger(page)).toContainText(renamed);

    // Duplicate — the duplicate button doesn't close the menu, so the new entry
    // and its own delete control are usable right away.
    await savedViewsTrigger(page).click();
    await page.getByLabel(`Duplicate ${renamed}`).click();
    await expect(page.getByText(`${renamed} copy`)).toBeVisible();
    await page.getByLabel(`Delete ${renamed} copy`).click();
    await page.getByRole("dialog").getByRole("button", { name: "Delete", exact: true }).click();
    await expect(page.getByText(`${renamed} copy`)).toHaveCount(0);

    // Delete the original.
    await savedViewsTrigger(page).click();
    await page.getByLabel(`Delete ${renamed}`).click();
    await page.getByRole("dialog").getByRole("button", { name: "Delete", exact: true }).click();
    await expect(page.getByText(renamed)).toHaveCount(0);
  });

  test("starring a model and filtering by favorites narrows the grid", async ({ page }) => {
    const stamp = Date.now();
    const starred = `e2e-fav-${stamp}`;
    const plain = `e2e-nofav-${stamp}`;

    await uploadModel(page, starred);
    await uploadModel(page, plain);

    await page.goto("/");
    // The star button is a sibling of the card's `<a>`, not a descendant of it
    // (both live under the same `<article>`) — scope through the article so
    // `getByLabel` can actually find it.
    const starredArticle = page.locator("article").filter({ has: modelCard(page, starred) });
    await starredArticle.getByLabel(`Add ${starred} to favorites`).click();
    await expect(starredArticle.getByLabel(`Remove ${starred} from favorites`)).toBeVisible();

    await page.getByRole("button", { name: "Favorites", exact: true }).click();
    await expect(page).toHaveURL(/favorites=true/);
    await expect(modelCard(page, starred)).toBeVisible();
    await expect(modelCard(page, plain)).toHaveCount(0);

    // Toggling off restores the plain model to the grid.
    await page.getByRole("button", { name: "Favorites", exact: true }).click();
    await expect(modelCard(page, plain)).toBeVisible();

    // Cleanup: unstar so the shared DB doesn't drift the favorites facet.
    await starredArticle.getByLabel(`Remove ${starred} from favorites`).click();
  });
});
