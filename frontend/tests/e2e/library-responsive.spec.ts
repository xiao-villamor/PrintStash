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
  await expect(
    page.getByRole("main").getByRole("button", { name: "Upload", exact: true }),
  ).toBeVisible();
  const more = page.getByRole("main").getByRole("button", { name: "More", exact: true });
  if (await more.isVisible()) await more.click();
  await page
    .getByRole("button", { name: "Display", exact: true })
    .or(page.getByRole("menuitem", { name: "Display", exact: true }))
    .click();
}

test.describe("Library responsive layout", () => {
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

  test("keeps optional collection actions in one compact band", async ({ page }) => {
    await page.setViewportSize({ width: 824, height: 1000 });
    await page.goto("/?c=maraio");
    const tags = page.getByRole("button", { name: "Add tags", exact: true });
    const description = page.getByRole("button", {
      name: "Add a description for this collection",
      exact: true,
    });
    await expect(description).toBeVisible();

    const tagsBox = await tags.boundingBox();
    const descriptionBox = await description.boundingBox();
    expect(tagsBox).not.toBeNull();
    expect(descriptionBox).not.toBeNull();
    expect(Math.abs(tagsBox!.y - descriptionBox!.y)).toBeLessThanOrEqual(4);
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
      .getByRole("main")
      .getByRole("navigation")
      .evaluate((nav) => ({ width: nav.scrollWidth, available: nav.clientWidth }));
    expect(bounds.width).toBeLessThanOrEqual(bounds.available + 1);
  });

  test("opens the desktop filters from a narrow tablet pane", async ({ page }) => {
    await page.setViewportSize({ width: 824, height: 900 });
    await page.goto("/");
    const trigger = page.getByRole("button", { name: "Filters", exact: true });
    await trigger.click();

    await expect(page.getByRole("region", { name: "Filters", exact: true })).toBeVisible();
    await expect(trigger).toHaveAttribute("aria-expanded", "true");
    await trigger.click();
    await expect(page.getByRole("region", { name: "Filters", exact: true })).toBeHidden();
  });

  test("opens the filter drawer on a phone", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 900 });
    await page.goto("/");
    await page.getByRole("button", { name: "Filters", exact: true }).click();

    await expect(page.getByRole("dialog", { name: "Filters", exact: true })).toBeVisible();
  });

  test("keeps secondary actions available in a narrow pane", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 900 });
    await page.goto("/");
    await page.getByRole("main").getByRole("button", { name: "More", exact: true }).click();

    const menu = page.getByRole("menu");
    await expect(menu.getByRole("menuitem", { name: "Library tools" })).toBeVisible();
    await expect(menu.getByRole("menuitem", { name: "New multipart set" })).toBeVisible();
    await expect(menu.getByRole("menuitem", { name: "Display", exact: true })).toBeVisible();
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
