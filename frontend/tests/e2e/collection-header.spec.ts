/** Collection edits must work against the shared preview API without response overrides. */
import { expect, test } from "@playwright/test";
import { useMockApi } from "./_setup";
import { collapseNavigationSidebar } from "../e2e-real/util";
import { aCollectionNode, aModelListItem } from "../../src/test-support/factories";

useMockApi();

test.describe("Collection header preview", () => {
  test("edits New collection at the end of the collection cards", async ({ page }, testInfo) => {
    await page.addInitScript(() => localStorage.setItem("printstash.theme", "dark"));
    await page.goto("/");
    await collapseNavigationSidebar(page);
    const main = page.getByRole("main");
    const cards = main.getByRole("region", { name: "Collections", exact: true });
    const create = cards.getByRole("button", { name: "New collection", exact: true });
    await expect(main.getByRole("button", { name: "New collection", exact: true })).toHaveCount(1);
    await expect(create).toBeVisible();
    await expect(cards.locator("[data-library-key]").last()).toContainText("New collection");
    await cards.screenshot({ path: testInfo.outputPath("new-collection-card.png") });
    await create.click();
    const name = cards.getByRole("textbox", { name: "Collection name" });
    await expect(name).toBeFocused();
    await expect(name).toHaveCSS("border-top-style", "dashed");
    await expect(name).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
    await expect(name).toHaveCSS("box-shadow", "none");
    await expect(page.getByRole("dialog", { name: "New collection" })).toHaveCount(0);
    const submit = cards.getByRole("button", { name: "Create collection" });
    await expect(submit).toBeDisabled();

    let writes = 0;
    page.on("request", (request) => {
      if (
        request.method() === "POST" &&
        new URL(request.url()).pathname === "/api/v1/collections"
      ) {
        writes++;
      }
    });
    await name.fill("A".repeat(101));
    await expect(cards.getByRole("alert")).toHaveText("Use 100 characters or fewer.");
    await expect(name).toHaveAttribute("aria-invalid", "true");
    await expect(submit).toBeDisabled();
    await name.press("Enter");
    await expect(name).toBeVisible();
    expect(writes).toBe(0);
    await cards.screenshot({ path: testInfo.outputPath("new-collection-validation.png") });

    await name.fill("A".repeat(100));
    await expect(cards.getByRole("alert")).toHaveCount(0);
    await expect(submit).toBeEnabled();
    await name.press("Escape");
    await expect(create).toBeFocused();
    await create.click();
    await expect(name).toHaveValue("");
  });

  test("aligns the collection row with the gallery columns", async ({ page }, testInfo) => {
    await page.addInitScript(() => localStorage.setItem("printstash.theme", "dark"));
    await page.route("**/api/v1/collections/children?**", (route) =>
      route.fulfill({
        json: {
          items: Array.from({ length: 8 }, (_, index) => {
            const slug = `folder-${index + 1}`;
            return aCollectionNode({
              id: index + 2,
              parent_id: null,
              name: `Folder ${index + 1}`,
              slug,
              path: slug,
              model_count: 0,
            });
          }),
          next_cursor: null,
        },
      }),
    );
    await page.route("**/api/v1/models/browse?**", (route) =>
      route.fulfill({
        json: {
          items: Array.from({ length: 8 }, (_, index) => ({
            kind: "model",
            model: aModelListItem({ id: index + 1, name: `Model ${index + 1}` }),
          })),
          total: 8,
          next_cursor: null,
          browse_revision: "r1",
          authorization_revision: "a1",
        },
      }),
    );
    await page.goto("/");
    await collapseNavigationSidebar(page);
    const main = page.getByRole("main");
    const cards = main.getByRole("region", { name: "Collections", exact: true });
    const disclosure = cards.getByRole("button", { name: /^\d+ more$/ });
    const create = cards.getByRole("button", { name: "New collection", exact: true });
    await expect(disclosure).toBeVisible();
    await expect(create).toBeVisible();
    await expect
      .poll(async () => {
        const button = await disclosure.boundingBox();
        const card = await create.boundingBox();
        if (!button || !card) return false;
        const grid = await main.locator("article.library-model-card").evaluateAll((articles) => {
          const [first, ...others] = articles.map((article) => article.getBoundingClientRect());
          if (!first) return null;
          const firstRow = [first, ...others.filter((rect) => Math.abs(rect.top - first.top) < 2)];
          return { width: first.width, right: Math.max(...firstRow.map((rect) => rect.right)) };
        });
        return (
          grid &&
          Math.abs(button.x + button.width - card.x - card.width) < 2 &&
          Math.abs(card.width - grid.width) < 2 &&
          Math.abs(card.x + card.width - grid.right) < 2 &&
          card.y - (button.y + button.height) >= 8
        );
      })
      .toBe(true);
    await main.screenshot({ path: testInfo.outputPath("collection-disclosure-spacing.png") });
  });

  test("persists consecutive inline tags through the preview API", async ({ page }) => {
    await page.goto("/?c=maraio");
    const opener = page.getByRole("button", { name: "Add tag", exact: true });
    await page.mouse.move(0, 0);
    await expect(opener).toBeVisible();
    await expect(opener).toHaveCSS("opacity", "1");
    const idleHeight = (await opener.boundingBox())!.height;
    await opener.click();
    const input = page.getByRole("textbox", { name: "New tag" });
    const field = input.locator("..");
    await expect(input).toHaveCSS("box-shadow", "none");
    await expect(field).toHaveCSS("box-shadow", "none");
    await expect(field).toHaveCSS("border-style", "dashed");
    await expect(field).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
    expect((await field.boundingBox())!.height).toBe(idleHeight);
    await input.fill("#Outdoor Parts");
    const first = page.waitForResponse(
      (r) => r.url().endsWith("/collections/1/tags") && r.request().method() === "PUT",
    );
    await input.press("Enter");
    expect((await first).status()).toBe(200);
    await expect(input).toHaveValue("");
    await expect(input).toBeFocused();
    await expect(page.getByRole("button", { name: "Remove tag outdoor-parts" })).toBeVisible();
    await input.fill("functional");
    await input.press(",");
    await expect(page.getByRole("button", { name: "Remove tag functional" })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("button", { name: "Remove tag outdoor-parts" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Remove tag functional" })).toBeVisible();
  });

  test("persists tag removal through the preview API", async ({ page }) => {
    const seed = await page.request.put("/api/v1/collections/1/tags", {
      data: { tags: ["functional", "outdoor"] },
    });
    expect(seed.status()).toBe(200);
    await page.goto("/?c=maraio");
    await page.getByRole("button", { name: "Remove tag outdoor" }).click();
    await expect(page.getByRole("button", { name: "Remove tag outdoor" })).toHaveCount(0);
    await page.reload();
    await expect(page.getByRole("button", { name: "Remove tag functional" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Remove tag outdoor" })).toHaveCount(0);
  });

  test("keeps a duplicate draft available for correction", async ({ page }) => {
    await page.request.put("/api/v1/collections/1/tags", { data: { tags: ["functional"] } });
    await page.goto("/?c=maraio");
    await page.getByRole("button", { name: "Add tag", exact: true }).click();
    const input = page.getByRole("textbox", { name: "New tag" });
    await input.fill("FUNCTIONAL");
    await input.press("Enter");
    await expect(page.getByRole("alert")).toHaveText("#functional is already on this collection.");
    await expect(input).toHaveValue("FUNCTIONAL");
    await expect(input).toBeFocused();
    await input.fill("outdoor");
    await expect(page.getByRole("alert")).toHaveCount(0);
    await input.press("Enter");
    await expect(page.getByRole("button", { name: "Remove tag outdoor" })).toBeVisible();
  });

  test("keeps the empty description aligned with collection information", async ({ page }) => {
    await page.goto("/?c=maraio");
    const header = page.locator(".library-collection-header");
    const description = header.getByRole("button", {
      name: "Add a description for this collection",
      exact: true,
    });
    await expect(description).toBeVisible();
    await expect(description.locator("svg")).toHaveCount(0);
    await expect(description).toHaveCSS("font-size", "13px");
    const bounds = await description.boundingBox();
    const meta = await header.locator(".library-collection-meta").boundingBox();
    expect(bounds!.height).toBeLessThan(20);
    expect(Math.abs(bounds!.x - meta!.x)).toBeLessThan(1);
    expect(meta!.y - bounds!.y - bounds!.height).toBeCloseTo(8, 0);
  });

  test("persists collection description through the preview API", async ({ page }) => {
    await page.goto("/?c=maraio");
    await page
      .getByRole("button", { name: "Add a description for this collection", exact: true })
      .click();
    const description =
      "Base in PETG for UV resistance. Legs are fine in PLA. Use a brim on the large pieces.";
    const input = page.getByRole("textbox", { name: "Description", exact: true });
    await input.fill(description);
    const saved = page.waitForResponse(
      (r) => r.url().endsWith("/collections/1/readme") && r.request().method() === "PUT",
    );
    await input.press("Control+Enter");
    expect((await saved).status()).toBe(200);
    await expect(page.getByText(description, { exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByText(description, { exact: true })).toBeVisible();
    const text = page.locator(".collection-header-readme .prose p");
    await expect(text).toHaveCSS("font-size", "13px");
    await expect(text).toHaveCSS("margin-top", "0px");
  });

  test("retains collection previews when a returning tab refreshes the library", async ({
    page,
    context,
  }) => {
    const firstRead = page.waitForResponse(
      (response) => new URL(response.url()).pathname === "/api/v1/models/browse",
    );
    await page.goto("/");
    const originalPage = await (await firstRead).json();
    const card = page.locator('main [data-collection-path="maraio"]');
    const image = card.locator("img").first();
    await expect(image).toBeVisible();
    const original = await image.getAttribute("src");
    const count = await card.locator("p").last().textContent();
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    let reading = false;
    await page.route("**/api/v1/models/browse/revision", async (route) => {
      await route.fulfill({
        json: {
          browse_revision: "r-returning-tab",
          authorization_revision: originalPage.authorization_revision,
        },
      });
    });
    await page.route("**/api/v1/models/browse?**", async (route) => {
      reading = true;
      await held;
      await route.fulfill({
        json: { ...originalPage, browse_revision: "r-returning-tab" },
      });
    });
    const otherTab = await context.newPage();
    await otherTab.goto("about:blank");
    await otherTab.bringToFront();
    await page.bringToFront();
    // Headless Chromium may keep both pages visible. Exercise the same visibility
    // events that Query's focus manager receives from a real browser tab switch.
    await page.evaluate(() => {
      Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
      document.dispatchEvent(new Event("visibilitychange"));
      Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    try {
      await expect.poll(() => reading).toBe(true);
      await expect(image).toHaveAttribute("src", original!);
      await expect(image).toBeVisible();
      await expect(card.locator("p").last()).toHaveText(count!);
      await expect(page.getByText("Updating...", { exact: true })).toHaveCount(0);
      await expect(page.getByRole("button", { name: "Refresh library", exact: true })).toHaveCount(
        0,
      );
    } finally {
      release();
      await otherTab.close();
    }
    await expect(page.locator("main")).not.toHaveAttribute("aria-busy", "true");
    await expect(image).toHaveAttribute("src", original!);
  });
});
