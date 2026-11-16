/** Library controls fit the available pane without sacrificing titles or access to actions. */
import { expect, test, type Page } from "@playwright/test";

import { aCollectionNode, aModelListItem } from "../../src/test-support/factories";
import { useMockApi } from "./_setup";

useMockApi();

const collectionName = "aw11-mr2-cupholder-bracket";

// Layout fixtures use real endpoint shapes; layout assertions observe rendered geometry.
test.beforeEach(async ({ page }) => {
  const collection = aCollectionNode({
    id: 1,
    name: collectionName,
    path: "maraio",
    tags: [],
    has_readme: false,
  });
  await page.route("**/api/v1/collections/lookup?**", (route) =>
    route.fulfill({ json: { collection, ancestors: [] } }),
  );
  await page.route("**/api/v1/collections/children?**", (route) =>
    route.fulfill({ json: { items: [], next_cursor: null } }),
  );
  await page.route("**/api/v1/models/browse?**", (route) =>
    route.fulfill({
      json: {
        items: Array.from({ length: 8 }, (_, index) => ({
          kind: "model",
          model: aModelListItem({ id: index + 1, name: `Responsive bracket ${index + 1}` }),
        })),
        total: 8,
        next_cursor: null,
        browse_revision: "r1",
        authorization_revision: "a1",
      },
    }),
  );
});

async function openDisplayMenu(page: Page): Promise<void> {
  await expect(page.getByRole("button", { name: "Upload", exact: true })).toBeVisible();
  const more = page.getByRole("main").getByRole("button", { name: "More", exact: true });
  if (await more.isVisible()) await more.click();
  else await page.getByRole("heading", { name: "Models", exact: true }).click({ button: "right" });
  await page
    .getByRole("button", { name: "Display", exact: true })
    .or(page.getByRole("menuitem", { name: "Display", exact: true }))
    .click();
}

test.describe("Library responsive layout", () => {
  for (const width of [320, 824, 1440]) {
    test(
      width < 768
        ? "keeps secondary breadcrumbs out of the phone header"
        : `keeps Recent folders inside the viewport at ${width}px`,
      async ({ page }) => {
        const label = "PrintStash/Organization/Gridfinity/Workshop/" + "NestedCollection".repeat(8);
        await page.setViewportSize({ width, height: 900 });
        // Recent entries resolve their current server label when the menu opens.
        await page.route("**/api/v1/collections/lookup?**", (route) =>
          route.fulfill({
            json: {
              collection: aCollectionNode({
                id: 1,
                path: "maraio",
                name: label,
                display_path: label,
              }),
              ancestors: [],
            },
          }),
        );
        await page.addInitScript((folderLabel) => {
          localStorage.setItem(
            "ps-recent-folders-labelled",
            JSON.stringify([["maraio", folderLabel]]),
          );
        }, label);
        await page.goto("/");
        await expect(page.getByRole("heading", { name: "All Models", exact: true })).toBeVisible();
        if (width < 768) {
          await expect(page.getByRole("button", { name: "Recent", exact: true })).toBeHidden();
          return;
        }
        await page.getByRole("button", { name: "Recent", exact: true }).click();

        const menu = page.getByRole("menu");
        const folder = menu.getByRole("menuitem", { name: label, exact: true });
        await expect(folder).toBeVisible();
        await expect(folder).toHaveAttribute("title", label);
        const bounds = await menu.evaluate((element) => ({
          left: element.getBoundingClientRect().left,
          right: element.getBoundingClientRect().right,
          content: element.scrollWidth,
          width: element.clientWidth,
        }));
        expect(bounds.left).toBeGreaterThanOrEqual(0);
        expect(bounds.right).toBeLessThanOrEqual(width);
        expect(bounds.content).toBeLessThanOrEqual(bounds.width + 1);
        await expect(menu.getByRole("menuitem", { name: "Clear recent folders" })).toBeVisible();
      },
    );
  }

  for (const width of [320, 390, 640, 824, 1044, 1440]) {
    test(`keeps the full collection heading readable at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/?c=maraio");
      const heading = page.getByRole("heading", { level: 1, name: collectionName, exact: true });
      await expect(heading).toBeVisible();

      const bounds = await heading.evaluate((element) => ({
        content: element.scrollWidth,
        box: element.clientWidth,
      }));
      expect(bounds.content).toBeLessThanOrEqual(bounds.box + 1);
      const upload = await page.getByRole("button", { name: "Upload", exact: true }).boundingBox();
      expect(upload).not.toBeNull();
      expect(upload!.x + upload!.width).toBeLessThanOrEqual(width);
    });
  }

  for (const width of [824, 1440]) {
    for (const density of ["Comfortable", "Compact"]) {
      test(`uses the available width for a ${density} grid at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        await page.goto("/");
        await openDisplayMenu(page);
        await page.getByRole("menuitem", { name: density, exact: true }).click();
        await expect(page.getByRole("article")).toHaveCount(8);

        const bounds = await page
          .getByRole("article")
          .first()
          .evaluate((card) => {
            const grid = card.parentElement!;
            const first = card.getBoundingClientRect();
            const row = [...grid.children]
              .map((child) => child.getBoundingClientRect())
              .filter((box) => Math.abs(box.top - first.top) < 1);
            return {
              edge: Math.max(...row.map((box) => box.right)),
              available: grid.getBoundingClientRect().right,
            };
          });
        expect(Math.abs(bounds.available - bounds.edge)).toBeLessThanOrEqual(1);
      });
    }
  }

  test("keeps collection tag entry in the metadata band", async ({ page }) => {
    await page.setViewportSize({ width: 824, height: 1000 });
    await page.goto("/?c=maraio");
    const tags = page.getByRole("button", { name: "+ Add tag", exact: true });
    const description = page.getByRole("button", {
      name: "Add a description for this collection",
      exact: true,
    });
    await expect(description).toBeVisible();

    const tagsBox = await tags.boundingBox();
    const descriptionBox = await description.boundingBox();
    expect(tagsBox).not.toBeNull();
    expect(descriptionBox).not.toBeNull();
    expect(tagsBox!.y).toBeGreaterThanOrEqual(descriptionBox!.y + descriptionBox!.height);
    expect(tagsBox!.height).toBe(26);
  });

  test("keeps description editor actions reachable on a phone", async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 900 });
    await page.goto("/?c=maraio");
    await page
      .getByRole("button", { name: "Add a description for this collection", exact: true })
      .click();
    await expect(page.getByRole("textbox", { name: "Description", exact: true })).toBeVisible();

    const hint = await page
      .getByText("Markdown · paste or drop images", { exact: true })
      .boundingBox();
    expect(hint).not.toBeNull();
    expect(hint!.x + hint!.width).toBeLessThanOrEqual(320);
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "Add a description for this collection", exact: true }),
    ).toBeVisible();
  });

  test("keeps collection breadcrumbs inside the pane", async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 900 });
    await page.goto("/?c=maraio");
    const heading = page.getByRole("heading", { name: collectionName, exact: true });
    await expect(heading).toBeVisible();

    const bounds = await page
      .locator("header nav")
      .evaluate((nav) => ({ width: nav.scrollWidth, available: nav.clientWidth }));
    expect(bounds.width).toBeLessThanOrEqual(bounds.available + 1);
  });

  test("opens the desktop filters from a narrow tablet pane", async ({ page }) => {
    await page.setViewportSize({ width: 824, height: 900 });
    await page.goto("/");
    const trigger = page.getByRole("button", { name: "Filters", exact: true });
    await trigger.click();

    await expect(page.getByRole("dialog", { name: "Filter Models", exact: true })).toBeVisible();
    await expect(trigger).toHaveAttribute("aria-expanded", "true");
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: "Filter Models", exact: true })).toBeHidden();
  });

  test("opens the filter drawer on a phone", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 900 });
    await page.goto("/");
    await page.getByRole("button", { name: "Filters", exact: true }).click();

    await expect(page.getByRole("dialog", { name: "Filter Models", exact: true })).toBeVisible();
  });

  test("keeps secondary actions available in a narrow pane", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 900 });
    await page.goto("/");
    await page.getByRole("main").getByRole("button", { name: "More", exact: true }).click();

    const menu = page.getByRole("menu");
    await expect(menu.getByRole("menuitem", { name: "Library tools" })).toHaveCount(0);
    await expect(menu.getByRole("menuitem", { name: "New multipart set" })).toBeVisible();
    await expect(menu.getByRole("menuitem", { name: "Display", exact: true })).toBeVisible();
  });

  for (const width of [320, 390, 824, 1440]) {
    test(`fits library actions within the toolbar at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/");
      const actions = page.getByRole("main").getByRole("group", { name: "Library tools" });
      const toolbar = actions.locator("..");
      await expect(toolbar.getByRole("button", { name: "Filters", exact: true })).toBeVisible();
      await expect(actions.getByRole("link", { name: "Similar models" })).toBeVisible();
      await expect(actions.getByRole("button", { name: "New collection" })).toBeVisible();
      await expect(actions.getByRole("button", { name: "Select", exact: true })).toBeVisible();
      await expect(
        page.getByRole("checkbox", { name: "Select Responsive bracket 1", exact: true }),
      ).toBeVisible();
      const bounds = await toolbar.boundingBox();
      expect(bounds).not.toBeNull();
      expect(bounds!.x).toBeGreaterThanOrEqual(0);
      expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
    });
  }

  test("aligns library actions with Filters", async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto("/");
    const actions = page.getByRole("main").getByRole("group", { name: "Library tools" });
    const filters = actions.locator("..").getByRole("button", { name: "Filters", exact: true });
    const boxes = await Promise.all([
      filters.boundingBox(),
      actions.getByRole("link", { name: "Similar models" }).boundingBox(),
      actions.getByRole("button", { name: "New collection" }).boundingBox(),
    ]);
    expect(boxes.every((box) => box !== null)).toBe(true);
    expect(new Set(boxes.map((box) => Math.round(box!.y))).size).toBe(1);
    expect(boxes[1]!.x).toBeGreaterThan(boxes[0]!.x);
    await page.screenshot({
      path: testInfo.outputPath("library-actions-toolbar.png"),
      animations: "disabled",
    });
  });

  test("follows the visible toolbar order with the keyboard on phones", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 900 });
    await page.goto("/");
    const main = page.getByRole("main");
    await main.getByRole("button", { name: "Filters", exact: true }).focus();

    await page.keyboard.press("Tab");
    await expect(main.getByRole("button", { name: "Sort models", exact: true })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(main.getByRole("button", { name: "More", exact: true })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(main.getByRole("link", { name: "Similar models", exact: true })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(main.getByRole("button", { name: "New collection", exact: true })).toBeFocused();
  });

  test("selects directly from a card using the keyboard on a phone", async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 900 });
    await page.goto("/");
    const checkbox = page.getByRole("checkbox", {
      name: "Select Responsive bracket 1",
      exact: true,
    });
    await expect(page.getByRole("button", { name: "Done", exact: true })).toHaveCount(0);
    await checkbox.focus();
    await page.keyboard.press("Space");
    await expect(checkbox).toBeChecked();
    await expect(page).toHaveURL((url) => url.pathname === "/");
    await expect(page.getByRole("button", { name: "Done", exact: true })).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath("direct-selection-phone.png"),
      animations: "disabled",
    });
    await checkbox.press("Space");
    await expect(checkbox).not.toBeChecked();
    await expect(page.getByRole("button", { name: "Done", exact: true })).toHaveCount(0);
  });

  test("starts collection creation from the phone toolbar", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 900 });
    await page.goto("/");

    await page
      .getByRole("group", { name: "Library tools" })
      .getByRole("button", { name: "New collection", exact: true })
      .click();

    await expect(
      page
        .getByRole("dialog", { name: "New collection", exact: true })
        .getByRole("textbox", { name: "Name", exact: true }),
    ).toBeVisible();
  });

  test("navigates to Similar models from the phone toolbar", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 900 });
    await page.goto("/");

    await page
      .getByRole("group", { name: "Library tools" })
      .getByRole("link", { name: "Similar models", exact: true })
      .click();

    await expect(page).toHaveURL(/\/library\/similar$/);
  });

  test("reserves the same card columns while results load", async ({ page }) => {
    const pending = Promise.withResolvers<void>();
    await page.setViewportSize({ width: 824, height: 1000 });
    await page.route("**/api/v1/models/browse?**", async (route) => {
      await pending.promise;
      await route.fallback();
    });
    await page.goto("/");
    try {
      const loading = page
        .getByRole("main")
        .getByRole("status", { name: "Loading...", exact: true });
      await expect(loading).toBeVisible();
      const reserved = await loading.evaluate(
        (element) => element.firstElementChild!.firstElementChild!.getBoundingClientRect().width,
      );
      pending.resolve();
      await expect(page.getByRole("article")).toHaveCount(8);

      const loaded = await page.getByRole("article").first().boundingBox();
      expect(loaded).not.toBeNull();
      expect(Math.abs(loaded!.width - reserved)).toBeLessThanOrEqual(1);
    } finally {
      pending.resolve();
    }
  });

  test("keeps description editing discoverable without hover", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 900 });
    await page.route("**/api/v1/collections/lookup?**", (route) =>
      route.fulfill({
        json: {
          collection: aCollectionNode({
            id: 1,
            name: collectionName,
            path: "maraio",
            has_readme: true,
          }),
          ancestors: [],
        },
      }),
    );
    await page.route("**/api/v1/collections/1/readme", (route) =>
      route.fulfill({ json: { readme: "Parts for the cupholder." } }),
    );
    await page.goto("/?c=maraio");
    const edit = page.getByRole("button", { name: "Edit", exact: true });
    await expect(edit).toBeVisible();

    expect(await edit.evaluate((button) => getComputedStyle(button).opacity)).toBe("1");
    await edit.click();
    await expect(page.getByRole("textbox", { name: "Description", exact: true })).toHaveValue(
      "Parts for the cupholder.",
    );
  });
});

test.describe("Collection header", () => {
  test("uses the prototype collection header with inline tag persistence", async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    const collection = aCollectionNode({
      id: 1,
      name: collectionName,
      path: "maraio",
      tags: ["functional"],
      has_readme: true,
    });
    await page.route("**/api/v1/collections/lookup?**", (route) =>
      route.fulfill({ json: { collection, ancestors: [] } }),
    );
    await page.route("**/api/v1/collections/1/readme", (route) =>
      route.fulfill({ json: { readme: "Base in PETG for UV resistance." } }),
    );
    await page.route("**/api/v1/collections/1/tags", async (route) => {
      expect(route.request().method()).toBe("PUT");
      collection.tags = route.request().postDataJSON().tags;
      await route.fulfill({ json: collection });
    });
    await page.goto("/?c=maraio");
    const header = page.locator(".library-collection-header");
    await expect(header.getByText("Base in PETG for UV resistance.")).toBeVisible();
    expect((await header.locator(".collection-header-tag").first().boundingBox())!.height).toBe(28);
    await header.getByRole("button", { name: "+ Add tag", exact: true }).click();
    await header.getByRole("textbox", { name: "New tag" }).fill("outdoor");
    await header.getByRole("textbox", { name: "New tag" }).press("Enter");
    await expect(header.getByRole("button", { name: "Remove tag outdoor" })).toBeVisible();
    await page.reload();
    await header.getByRole("button", { name: "Remove tag functional" }).click();
    await expect(header.getByRole("button", { name: "Remove tag functional" })).toHaveCount(0);
    expect(collection.tags).toEqual(["outdoor"]);
    await expect(header.getByRole("button", { name: "Remove tag outdoor" })).toBeVisible();
  });

  test("keeps the prototype desktop Models toolbar in one row", async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto("/");
    const toolbar = page.locator(".library-model-toolbar");
    const filters = toolbar.getByRole("button", { name: /^Filters(?: \d+)?$/ });
    const status = toolbar.getByRole("radiogroup", { name: "Filter models by status" });
    await expect(filters).toBeVisible();
    await expect(filters.locator("svg")).toHaveCount(0);
    await expect(status.getByRole("radio", { name: "No G-code" })).toBeDisabled();
    await expect(toolbar.getByRole("button", { name: "More", exact: true })).toBeHidden();
    const boxes = await Promise.all(
      [
        toolbar.getByRole("heading", { name: "Models" }),
        status,
        filters,
        toolbar.getByRole("button", { name: "Sort models" }),
      ].map((locator) => locator.boundingBox()),
    );
    const centers = boxes.map((box) => box!.y + box!.height / 2);
    expect(Math.max(...centers) - Math.min(...centers)).toBeLessThanOrEqual(1);
    await status.getByRole("radio", { name: /^Ready/ }).click();
    await expect(page).toHaveURL(/revision_status=known_good/);
    await filters.focus();
    await page.keyboard.press("Shift+F10");
    await expect(page.getByRole("menuitem", { name: "Display", exact: true })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(filters).toBeFocused();
  });

  test("persists the inline collection title", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const collection = aCollectionNode({
      id: 1,
      name: "Planter with legs",
      path: "maraio",
      has_readme: true,
    });
    let description =
      "Base in PETG for UV resistance. Legs are fine in PLA. Use a brim on the large pieces.";
    let renames = 0;
    await page.route("**/api/v1/collections/lookup?**", (route) =>
      route.fulfill({ json: { collection, ancestors: [] } }),
    );
    await page.route("**/api/v1/collections/1", async (route) => {
      if (route.request().method() !== "PATCH") return route.fallback();
      renames++;
      collection.name = route.request().postDataJSON().name;
      await route.fulfill({ json: collection });
    });
    await page.route("**/api/v1/collections/1/readme", async (route) => {
      if (route.request().method() === "PUT") description = route.request().postDataJSON().readme;
      await route.fulfill({ json: { readme: description } });
    });
    await page.goto("/?c=maraio");
    const heading = page.getByRole("heading", { name: "Planter with legs", exact: true });
    await expect(heading).toBeVisible();
    const headingBounds = await heading.boundingBox();
    await page.getByRole("button", { name: "Rename Planter with legs" }).click();
    const name = page.getByRole("textbox", { name: "Collection name" });
    await expect(name).toBeFocused();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    const inputBounds = await name.boundingBox();
    expect(inputBounds!.height).toBe(40);
    expect(Math.abs(inputBounds!.y - headingBounds!.y)).toBeLessThan(6);
    await expect(name).toHaveCSS("font-size", "24px");
    await name.fill("Discard this");
    await name.press("Escape");
    await expect(page.getByRole("button", { name: "Rename Planter with legs" })).toBeFocused();
    expect(renames).toBe(0);
    await page.keyboard.press("F2");
    await expect(name).toHaveValue("Planter with legs");
    await name.fill("Outdoor planter");
    await name.press("Enter");
    await expect(page.getByRole("heading", { name: "Outdoor planter", exact: true })).toBeVisible();
    expect(renames).toBe(1);
    await page.reload();
    await expect(page.getByRole("heading", { name: "Outdoor planter", exact: true })).toBeVisible();
  });

  test("persists a compact collection description", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const collection = aCollectionNode({
      id: 1,
      name: collectionName,
      path: "maraio",
      has_readme: true,
    });
    let description =
      "Base in PETG for UV resistance. Legs are fine in PLA. Use a brim on the large pieces.";
    await page.route("**/api/v1/collections/lookup?**", (route) =>
      route.fulfill({ json: { collection, ancestors: [] } }),
    );
    await page.route("**/api/v1/collections/1/readme", async (route) => {
      if (route.request().method() === "PUT") description = route.request().postDataJSON().readme;
      await route.fulfill({ json: { readme: description } });
    });
    await page.goto("/?c=maraio");
    await page.getByText(description, { exact: true }).hover();
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    const editor = page.getByRole("textbox", { name: "Description", exact: true });
    await expect(editor).toBeFocused();
    await expect(editor).toHaveAttribute("rows", "3");
    await expect(editor).toHaveCSS("font-size", "13px");
    await editor.fill("Use a brim on the large pieces.");
    await editor.press("Control+Enter");
    await expect(page.getByText("Use a brim on the large pieces.", { exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByText("Use a brim on the large pieces.", { exact: true })).toBeVisible();
  });
});
