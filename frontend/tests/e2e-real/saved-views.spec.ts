/**
 * Saving a set of filters, and getting exactly those filters back.
 *
 * A saved view is a URL, so applying one has to restore the **canonical** URL rather than
 * an equivalent-looking one — otherwise the next save round-trips something different
 * from what the user is looking at. Favourites are here for the same reason: starring is
 * a filter, and it has to narrow the same grid.
 */
import { openFilters } from "./util";
import { test, expect } from "./helpers";
import { modelCard, uploadModel } from "./util";

// Saved views (0.10.0): persist a filter combination, restore it from the URL,
// and manage it (rename/duplicate/delete). Favorites: star a model, then filter
// the grid down to starred models only.

async function manageSavedViews(page: import("@playwright/test").Page) {
  const pane = page.getByRole("complementary", { name: "Saved views", exact: true });
  if (!(await pane.isVisible()))
    await page
      .getByRole("navigation", { name: "Navigation", exact: true })
      .getByRole("button", { name: "Saved views", exact: true })
      .click();
  const trigger = pane.getByRole("button", { name: "Manage saved views" });
  await trigger.click();
  const picker = page.getByRole("dialog", { name: "Saved views", exact: true });
  await expect(picker).toBeVisible();
  const anchor = await trigger.boundingBox();
  const panel = await picker.boundingBox();
  if (!anchor || !panel) throw new Error("Saved views actions did not open at the sidebar");
  expect(Math.abs(panel.x + panel.width - (anchor.x + anchor.width))).toBeLessThan(2);
  expect(panel.y).toBeGreaterThanOrEqual(anchor.y + anchor.height);
  return picker;
}

test.describe("saved views", () => {
  test.use({ viewport: { width: 1920, height: 1080 } });
  test("save current filters as a view, apply it restores the canonical URL", async ({
    page,
  }, testInfo) => {
    await page.addInitScript(() => localStorage.setItem("printstash.theme", "dark"));
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
    await openFilters(page);
    const tags = page.getByRole("dialog", { name: "Filter Models", exact: true });
    await tags.getByRole("button", { name: "Tags", exact: true }).click();
    await tags.getByRole("checkbox", { name: tag, exact: true }).click();
    await expect(tags.getByRole("checkbox", { name: tag, exact: true })).toBeChecked();
    await page.keyboard.press("Escape");
    await expect(modelCard(page, tagged)).toBeVisible();
    await expect(modelCard(page, plain)).toHaveCount(0);
    await expect(page).toHaveURL(new RegExp(`tag=${encodeURIComponent(tag)}`));

    const actions = await manageSavedViews(page);
    await expect(actions.getByRole("textbox")).toHaveCount(0);
    await expect(actions.getByText("No saved views yet")).toHaveCount(0);
    const saveView = actions.getByRole("button", { name: "Save current view" });
    await expect(saveView).toBeFocused();
    await page.screenshot({ path: testInfo.outputPath("saved-views-empty-actions.png") });
    await saveView.press("Escape");
    await expect(page.getByRole("button", { name: "Manage saved views" })).toBeFocused();
    await expect(actions).toHaveCount(0);
    await manageSavedViews(page);
    await saveView.click();
    const createName = page.getByRole("textbox", { name: "View name", exact: true });
    await expect(createName).toBeFocused();
    await page.keyboard.type(viewName);
    await createName.press("Enter");
    // The dialog closes after the server acknowledges the save. Reloading
    // sooner can abort the request before the view is persisted.
    await expect(page.getByRole("dialog", { name: "Save current view" })).toBeHidden();
    await expect(page.getByText("View saved", { exact: true })).toBeVisible();

    // Navigate away to the canonical, filter-less URL — a full reload, so no
    // saved view is active. Applying it from the menu must restore the exact
    // same result set + query string.
    await page.goto("/");
    await expect(modelCard(page, plain)).toBeVisible();
    await manageSavedViews(page);
    await page
      .getByRole("dialog", { name: "Saved views", exact: true })
      .getByRole("button", { name: viewName, exact: true })
      .click();

    await expect(page).toHaveURL(new RegExp(`tag=${encodeURIComponent(tag)}`));
    await expect(modelCard(page, tagged)).toBeVisible();
    await expect(modelCard(page, plain)).toHaveCount(0);

    // Rename — the trigger now shows the active view's name.
    await expect(
      page
        .getByRole("complementary", { name: "Saved views" })
        .getByRole("button", { name: viewName, exact: true }),
    ).toBeVisible();
    await manageSavedViews(page);
    await page.getByLabel(`Rename ${viewName}`).click();
    const renameName = page.getByRole("textbox", { name: "View name", exact: true });
    await expect(renameName).toBeFocused();
    await expect(renameName).toHaveValue(viewName);
    // Typing replaces the selected name immediately, without a pointer click.
    await page.keyboard.type(renamed);
    await renameName.press("Enter");
    await expect(
      page
        .getByRole("complementary", { name: "Saved views" })
        .getByRole("button", { name: renamed, exact: true }),
    ).toBeVisible();
    await page.reload();
    await manageSavedViews(page);
    await page
      .getByRole("dialog", { name: "Saved views", exact: true })
      .getByRole("button", { name: `${renamed} Recent`, exact: true })
      .click();

    // Duplicate — the duplicate button doesn't close the menu, so the new entry
    // and its own delete control are usable right away.
    await manageSavedViews(page);
    await page.getByLabel(`Duplicate ${renamed}`).click();
    await expect(
      page
        .getByRole("dialog", { name: "Saved views", exact: true })
        .getByText(`${renamed} copy`, { exact: true }),
    ).toBeVisible();
    await page.getByLabel(`Delete ${renamed} copy`).click();
    await page
      .getByRole("dialog", { name: "Delete saved view?", exact: true })
      .getByRole("button", { name: "Delete", exact: true })
      .click();
    await expect(page.getByText(`${renamed} copy`)).toHaveCount(0);

    // Delete the original.
    await manageSavedViews(page);
    await page.getByLabel(`Delete ${renamed}`).click();
    await page
      .getByRole("dialog", { name: "Delete saved view?", exact: true })
      .getByRole("button", { name: "Delete", exact: true })
      .click();
    await expect(page.getByText(renamed)).toHaveCount(0);
  });

  test("starring a model and filtering by favorites narrows the grid", async ({ page }) => {
    const stamp = Date.now();
    const starred = `e2e-fav-${stamp}`;
    const plain = `e2e-nofav-${stamp}`;

    await uploadModel(page, starred);
    await uploadModel(page, plain);

    await page.goto("/");
    await modelCard(page, starred).click({ button: "right" });
    await page.getByRole("menuitem", { name: `Add ${starred} to favorites` }).click();
    await expect(
      page.getByRole("menuitem", { name: `Remove ${starred} from favorites` }),
    ).toBeVisible();
    await page.keyboard.press("Escape");

    const favorites = page
      .getByRole("navigation", { name: "Navigation", exact: true })
      .getByRole("button", { name: "Favorites", exact: true });
    await favorites.click();
    await expect(page).toHaveURL(/favorites=true/);
    await expect(modelCard(page, starred)).toBeVisible();
    await expect(modelCard(page, plain)).toHaveCount(0);

    // Toggling off restores the plain model to the grid.
    await favorites.click();
    await expect(modelCard(page, plain)).toBeVisible();

    // Cleanup: unstar so the shared DB doesn't drift the favorites facet.
    await modelCard(page, starred).click({ button: "right" });
    await page.getByRole("menuitem", { name: `Remove ${starred} from favorites` }).click();
  });
});
