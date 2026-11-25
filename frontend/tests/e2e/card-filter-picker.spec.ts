/** Card actions and filter discovery stay anchored in the Library being browsed. */
import { expect, test } from "@playwright/test";
import {
  aCollectionNode,
  aModelFacets,
  aModelListItem,
  aTag,
} from "../../src/test-support/factories";
import { useMockApi } from "./_setup";
import { collapseNavigationSidebar } from "../e2e-real/util";

useMockApi();

test.describe("Library filter visibility", () => {
  test("opens the filter picker immediately after dismissal", async ({ page }) => {
    await page.goto("/");
    await collapseNavigationSidebar(page);
    const toggle = page.getByRole("button", { name: "Filters", exact: true });
    const dialog = page.getByRole("dialog", { name: "Filter Models", exact: true });
    const search = dialog.getByRole("searchbox");
    await toggle.click();
    await expect(search).toBeFocused();
    await expect(dialog).toHaveAttribute("data-state", "open");
    await search.fill("not a matching value");
    await search.press("Escape");
    await expect(toggle).toBeFocused();
    await expect(dialog).toHaveCount(0);
    await toggle.click();
    await expect(search).toBeFocused();
    await expect(search).toHaveValue("");
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    await expect(toggle).toBeFocused();
  });
});

test.describe("Library filters and card actions", () => {
  test.use({ viewport: { width: 1440, height: 900 } });
  const modelName = "Workshop bracket with a deliberately long model name";
  const facets = aModelFacets({
    material_type: [
      { value: "PLA", count: 2 },
      { value: "PETG", count: 1 },
    ],
    file_type: [{ value: "stl", count: 3 }],
    printed: [
      { value: "yes", count: 1 },
      { value: "no", count: 2 },
    ],
  });

  test.beforeEach(async ({ page }) => {
    let starred = false;
    await page.route("**/api/v1/models/browse?**", (route) => {
      const params = new URL(route.request().url()).searchParams;
      const material = params.getAll("material_type").join(" + ");
      return route.fulfill({
        json: {
          items: [
            {
              kind: "model",
              model: aModelListItem({
                id: 1,
                name: material || modelName,
                starred,
                thumbnail_url: null,
                effective_role: "admin",
              }),
            },
          ],
          total: 1,
          next_cursor: null,
          browse_revision: "r1",
          authorization_revision: "a1",
        },
      });
    });
    await page.route("**/api/v1/models/1/star", (route) => {
      starred = route.request().method() === "PUT";
      return route.fulfill({ json: { model_id: 1, starred } });
    });
    await page.route("**/api/v1/models/facets**", (route) => route.fulfill({ json: facets }));
    await page.route("**/api/v1/tags", (route) =>
      route.fulfill({ json: [aTag({ name: "Workshop", slug: "workshop" })] }),
    );
    await page.route("**/api/v1/collections/lookup?**", (route) =>
      route.fulfill({
        json: {
          collection: aCollectionNode({
            id: 1,
            name: "maraio",
            path: "maraio",
            child_count: 0,
            has_readme: false,
          }),
          ancestors: [],
        },
      }),
    );
  });

  async function openFilters(page: import("@playwright/test").Page) {
    await page.getByRole("button", { name: /^Filters(?: \d+)?$/ }).click();
    const picker = page.getByRole("dialog", { name: "Filter Models" });
    await expect(picker.getByRole("searchbox")).toBeFocused();
    await expect(picker).not.toHaveAttribute("aria-modal", "true");
    return picker;
  }

  test("refines materials in place through the filter lifecycle", async ({ page }) => {
    await page.goto("/?c=maraio&q=bracket");
    await expect(page.getByRole("heading", { name: modelName, exact: true })).toBeVisible();
    const picker = await openFilters(page);
    await picker.getByRole("searchbox").fill("pla");
    await picker.getByRole("checkbox", { name: "PLA", exact: true }).check();
    await expect(page.getByRole("heading", { name: "PLA", exact: true })).toBeVisible();
    await expect(picker).toBeVisible();
    await expect
      .poll(async () => {
        const trigger = await page
          .getByRole("button", { name: /^Filters(?: \d+)?$/ })
          .boundingBox();
        const panel = await picker.boundingBox();
        return Math.abs(panel!.y - trigger!.y - trigger!.height - 8);
      })
      .toBeLessThan(2);
    await picker.getByRole("searchbox").fill("petg");
    await picker.getByRole("checkbox", { name: "PETG", exact: true }).check();
    await expect(page.getByRole("heading", { name: "PLA PETG", exact: true })).toBeVisible();
    expect(new URL(page.url()).searchParams.get("c")).toBe("maraio");
    expect(new URL(page.url()).searchParams.get("q")).toBe("bracket");
    await picker.getByRole("button", { name: "Close", exact: true }).click();
    await page.reload();
    await expect(page.getByRole("heading", { name: "PLA PETG", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Material: PLA", exact: true }).click();
    await expect(page.getByRole("heading", { name: "PETG", exact: true })).toBeVisible();
  });

  test("applies a quick filter without opening its category", async ({ page }) => {
    await page.goto("/?c=maraio");
    const picker = await openFilters(page);
    const printed = picker.getByRole("group", { name: "Printed", exact: true });

    await printed.getByRole("button", { name: "Not printed", exact: true }).click();

    await expect.poll(() => new URL(page.url()).searchParams.get("printed")).toBe("no");
    await expect(printed.getByRole("button", { name: "Not printed", exact: true })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect(picker.getByRole("region", { name: "Printing", exact: true })).toBeVisible();
    expect(new URL(page.url()).searchParams.get("c")).toBe("maraio");
  });

  test("preserves collection scope throughout filter reset", async ({ page }) => {
    await page.goto("/?c=maraio&q=bracket&material_type=PLA&tag=missing");
    const picker = await openFilters(page);
    await picker.getByRole("button", { name: /^Tags/ }).click();
    await expect(picker.getByRole("checkbox", { name: "missing", exact: true })).toBeChecked();
    await picker.getByRole("button", { name: "Back", exact: true }).click();
    await picker.getByRole("button", { name: /^Material/ }).click();
    await expect(picker.getByRole("checkbox", { name: "PLA", exact: true })).toBeChecked();
    await picker.getByRole("searchbox").fill("zzzz");
    await expect(picker.getByRole("status")).toHaveText("No matching filters or values.");
    await picker.getByRole("button", { name: "Reset filters", exact: true }).click();
    await expect.poll(() => new URL(page.url()).searchParams.has("material_type")).toBe(false);
    const params = new URL(page.url()).searchParams;
    expect(params.get("c")).toBe("maraio");
    expect(params.get("q")).toBe("bracket");
    expect(params.has("tag")).toBe(false);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "Filters", exact: true })).toBeFocused();
  });

  test("keeps selected filters reachable in a narrow dark viewport", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ colorScheme: "dark" });
    await page.goto("/");
    const picker = await openFilters(page);
    await picker.getByRole("searchbox").fill("pla");
    await expect(picker.getByRole("checkbox", { name: "PLA", exact: true })).toBeVisible();
    await page.keyboard.press("Tab");
    await expect(picker.getByRole("checkbox", { name: "PLA", exact: true })).toBeFocused();
    await page.keyboard.press("Space");
    await expect(picker.getByRole("checkbox", { name: "PLA", exact: true })).toBeChecked();
    const bounds = await picker.boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(844);
  });

  test("failed filter values offer retry without discarding active filters", async ({ page }) => {
    let fail = true;
    await page.route("**/api/v1/models/facets**", (route) =>
      route.fulfill(fail ? { status: 503, json: { detail: "Unavailable" } } : { json: facets }),
    );
    await page.goto("/?material_type=PLA");
    const picker = await openFilters(page);
    await expect(picker.getByRole("alert")).toBeVisible();
    fail = false;
    await picker.getByRole("button", { name: "Retry", exact: true }).click();
    await picker.getByRole("searchbox").fill("pla");
    await expect(picker.getByRole("checkbox", { name: "PLA", exact: true })).toBeChecked();
    await expect(picker.getByRole("alert")).toHaveCount(0);
  });

  test("returns focus after using the card menu", async ({ page }) => {
    await page.goto("/");
    const card = page.locator("article.library-model-card").first();
    const link = card.getByRole("link", { name: modelName, exact: true });
    await link.focus();
    await page.keyboard.press("Shift+F10");
    const menu = page.getByRole("menu", { name: "Model actions" });
    await expect(menu.getByRole("menuitem", { name: "Open page", exact: true })).toBeFocused();
    const favorite = menu.getByRole("menuitem", {
      name: `Add ${modelName} to favorites`,
      exact: true,
    });
    await expect(favorite).toHaveText("Add to favorites");
    await favorite.click();
    await expect(menu).toBeHidden();
    await page.reload();
    await card.click({ button: "right" });
    await expect(
      menu.getByRole("menuitem", { name: `Remove ${modelName} from favorites`, exact: true }),
    ).toHaveText("Remove from favorites");
    await page.keyboard.press("Escape");
    await expect(link).toBeFocused();
  });

  test("keeps card menu edits in the Library", async ({ page }) => {
    await page.goto("/");
    const card = page.locator("article.library-model-card").first();
    await card.click({ button: "right" });
    await page.getByRole("menuitem", { name: "Model details", exact: true }).click();
    await expect(page.locator("main[data-inspector-open=true]")).toBeVisible();
    expect(new URL(page.url()).pathname).toBe("/");
    await card.click({ button: "right" });
    await page.getByRole("menuitem", { name: `Add tags to ${modelName}`, exact: true }).click();
    await expect(page.getByRole("dialog", { name: "Edit Model tags", exact: true })).toBeVisible();
  });

  test("preserves focus when dismissing typed filters", async ({ page }) => {
    await page.goto("/?has_similar_candidates=no");
    const picker = await openFilters(page);
    await picker.getByRole("searchbox").fill("similar");
    await expect(picker.getByRole("checkbox", { name: "No", exact: true })).toBeChecked();
    await picker.getByRole("searchbox").fill("printed");
    await picker.getByRole("checkbox", { name: "Printed", exact: true }).check();
    await expect.poll(() => new URL(page.url()).searchParams.get("printed")).toBe("yes");
    await picker.getByRole("checkbox", { name: "Not printed", exact: true }).check();
    await expect.poll(() => new URL(page.url()).searchParams.get("printed")).toBe("no");
    await expect(picker.getByRole("checkbox", { name: "Printed", exact: true })).not.toBeChecked();
    await picker.getByRole("searchbox").fill("uploaded");
    await picker.getByLabel("After", { exact: true }).fill("2026-01-01");
    await expect
      .poll(() => new URL(page.url()).searchParams.get("uploaded_after"))
      .toBe("2026-01-01");
    await picker.getByRole("button", { name: "Close", exact: true }).click();
    await expect(page.getByRole("button", { name: /^Filters/ })).toBeFocused();
  });
});
