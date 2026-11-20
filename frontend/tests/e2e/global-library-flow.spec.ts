/** Search and Favorites refine one Library; real cards reveal on cold and cached visits. */
import { expect, test, type Page } from "@playwright/test";
import { aModelListItem } from "../../src/test-support/factories";
import { useMockApi } from "./_setup";

/** Favorites is a Library filter: toggle it in the (non-modal) Filters picker. */
async function toggleFavorites(page: Page) {
  const picker = page.getByRole("dialog", { name: "Filter Models" });
  if (!(await picker.isVisible()))
    await page.getByRole("button", { name: /^Filters(?: \d+)?$/ }).click();
  await picker.getByRole("switch", { name: "Favorites", exact: true }).click();
}

useMockApi();
declare global {
  interface Window {
    cardFrames: { name: string; opacity: number; transform: string }[];
  }
}

test.describe("Global Library search and filters", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test.beforeEach(async ({ page }) => {
    await page.route("**/api/v1/models/browse?**", (route) => {
      const params = new URL(route.request().url()).searchParams;
      const name =
        params.get("favorites") === "true"
          ? "Favorite bracket"
          : params.get("q")
            ? "Matching bracket"
            : "Library bracket";
      return route.fulfill({
        json: {
          items: [{ kind: "model", model: aModelListItem({ id: 1, name, thumbnail_url: null }) }],
          total: 1,
          next_cursor: null,
          browse_revision: "r1",
          authorization_revision: "a1",
        },
      });
    });
    await page.addInitScript(() => {
      window.cardFrames = [];
      const sample = () => {
        for (const card of document.querySelectorAll(".library-card-fill-frame")) {
          const style = getComputedStyle(card);
          window.cardFrames.push({
            name: card.querySelector("h3")?.textContent ?? "",
            opacity: Number(style.opacity),
            transform: style.transform,
          });
        }
        requestAnimationFrame(sample);
      };
      requestAnimationFrame(sample);
    });
  });

  test("reveals real cards consistently through cached filter returns", async ({ page }) => {
    await page.goto("/");
    const assertReveal = async (name: string) => {
      await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
      await expect
        .poll(() =>
          page.evaluate(
            (title) =>
              window.cardFrames.some((frame) => frame.name === title && frame.opacity < 0.5),
            name,
          ),
        )
        .toBe(true);
      await expect(page.locator(".library-card-fill-frame").first()).toHaveCSS("opacity", "1");
      expect(
        await page.evaluate(
          (title) =>
            window.cardFrames.some(
              (frame) => frame.name === title && frame.opacity > 0.2 && frame.opacity < 0.99,
            ),
          name,
        ),
      ).toBe(true);
      expect(
        await page.evaluate(() => window.cardFrames.every((frame) => frame.transform === "none")),
      ).toBe(true);
      await page.evaluate(() => {
        window.cardFrames = [];
      });
    };
    await assertReveal("Library bracket");
    for (let visit = 0; visit < 2; visit++) {
      await toggleFavorites(page);
      await assertReveal("Favorite bracket");
      await toggleFavorites(page);
      await assertReveal("Library bracket");
    }
  });

  test("preserves sidebar search through a Favorites round trip", async ({ page }) => {
    await page.goto("/");
    const rail = page.getByRole("navigation", { name: "Navigation", exact: true });
    await rail.getByRole("link", { name: "Search", exact: true }).click();
    expect(new URL(page.url()).pathname).toBe("/");
    await expect(page.getByRole("heading", { name: "All Models", exact: true })).toBeVisible();
    const search = page.locator("[data-shell-panel-search]");
    await search.fill("bracket");
    await expect(
      page.getByRole("heading", { name: "Matching bracket", exact: true }),
    ).toBeVisible();
    await toggleFavorites(page);
    await expect(
      page.getByRole("heading", { name: "Favorite bracket", exact: true }),
    ).toBeVisible();
    await expect(search).toHaveValue("bracket");
    await expect(search).toBeVisible();
    await expect(page.getByRole("heading", { name: "All Models", exact: true })).toBeVisible();
    await expect(page.getByRole("complementary", { name: "Favorites", exact: true })).toHaveCount(
      0,
    );
    await expect(page.locator("article.library-model-card[data-favorites]")).toHaveCount(0);
    await toggleFavorites(page);
    await expect(
      page.getByRole("heading", { name: "Matching bracket", exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Clear search", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Library bracket", exact: true })).toBeVisible();
  });

  test("mobile Search filters the visible Library without a separate results page", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    const nav = page.getByRole("navigation", { name: "Navigation", exact: true });
    await nav.getByRole("link", { name: "Search", exact: true }).click();
    const input = page.locator("[data-model-search]:visible");
    await expect(input).toBeFocused();
    await input.fill("bracket");
    await expect(
      page.getByRole("heading", { name: "Matching bracket", exact: true }),
    ).toBeVisible();
    expect(new URL(page.url()).pathname).toBe("/");
    await expect(page.getByRole("heading", { name: "All Models", exact: true })).toBeVisible();
  });

  test("reduced motion keeps real cards opaque, including first load", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Library bracket", exact: true })).toBeVisible();
    await toggleFavorites(page);
    await expect(
      page.getByRole("heading", { name: "Favorite bracket", exact: true }),
    ).toBeVisible();
    expect(
      await page.evaluate(() =>
        window.cardFrames.every((frame) => frame.opacity === 1 && frame.transform === "none"),
      ),
    ).toBe(true);
  });
});
