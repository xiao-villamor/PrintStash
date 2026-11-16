/** Cached navigation retains one quiet reveal; decoded previews never snap into their frames. */
import { expect, test } from "@playwright/test";
import { useMockApi } from "./_setup";
import { aCollectionNode, aModelListItem } from "../../src/test-support/factories";

useMockApi();

declare global {
  interface Window {
    libraryReveals: { opacity: number[]; transforms: string[] }[];
  }
}

test.describe("Library motion", () => {
  test.use({ viewport: { width: 1920, height: 1080 } });
  test.beforeEach(async ({ page }) => {
    const collection = aCollectionNode({
      id: 1,
      name: "maraio",
      path: "maraio",
      has_readme: false,
    });
    await page.route("**/api/v1/collections/lookup?**", (route) =>
      route.fulfill({ json: { collection, ancestors: [] } }),
    );
    await page.route("**/api/v1/models/browse?**", (route) => {
      const folder = new URL(route.request().url()).searchParams.get("collection") === "maraio";
      return route.fulfill({
        json: {
          items: [
            {
              kind: "model",
              model: aModelListItem({
                id: 1,
                name: folder ? "Folder model" : "Root model",
                thumbnail_url: null,
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
    await page.addInitScript(() => {
      window.libraryReveals = [];
      const animate = Element.prototype.animate;
      Element.prototype.animate = function (...args) {
        const result = animate.apply(this, args);
        if (
          this.matches(".library-card-fill-frame,.library-content-reveal") &&
          result.effect instanceof KeyframeEffect
        ) {
          const frames = result.effect.getKeyframes();
          window.libraryReveals.push({
            opacity: frames.map((frame) => Number(frame.opacity)),
            transforms: frames.map((frame) => String(frame.transform ?? "none")),
          });
        }
        return result;
      };
    });
  });

  test("cached history visits replay a stationary reveal", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Root model", exact: true })).toBeVisible();
    await expect(page.locator(".library-card-fill-frame")).toHaveCSS("opacity", "1");
    await page.evaluate(() => {
      window.libraryReveals = [];
    });
    await page
      .getByRole("main")
      .getByRole("button", { name: /maraio/ })
      .click();
    await expect(page.getByRole("heading", { name: "Folder model", exact: true })).toBeVisible();
    await expect.poll(() => page.evaluate(() => window.libraryReveals.length)).toBe(1);
    await page.goBack();
    await expect(page.getByRole("heading", { name: "Root model", exact: true })).toBeVisible();
    await expect.poll(() => page.evaluate(() => window.libraryReveals.length)).toBe(2);
    await page.goForward();
    await expect(page.getByRole("heading", { name: "Folder model", exact: true })).toBeVisible();
    await expect.poll(() => page.evaluate(() => window.libraryReveals.length)).toBe(3);
    await page.goBack();
    await expect(page.getByRole("heading", { name: "Root model", exact: true })).toBeVisible();

    await expect.poll(() => page.evaluate(() => window.libraryReveals.length)).toBe(4);
    const reveals = await page.evaluate(() => window.libraryReveals);
    expect(reveals).toHaveLength(4);
    expect(reveals.every((reveal) => reveal.opacity[0] >= 0.2 && reveal.opacity[0] <= 1)).toBe(
      true,
    );
    expect(reveals.every((reveal) => reveal.opacity.at(-1) === 1)).toBe(true);
    expect(
      reveals.every((reveal) => reveal.transforms.every((transform) => transform === "none")),
    ).toBe(true);
  });

  test("interrupted navigation continues from the visible opacity", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Root model", exact: true })).toBeVisible();
    await expect(page.locator(".library-card-fill-frame")).toHaveCSS("opacity", "1");
    await page.evaluate(() => {
      window.libraryReveals = [];
    });
    await page.evaluate(() => {
      const animate = Element.prototype.animate;
      Element.prototype.animate = function (...args) {
        const animation = animate.apply(this, args);
        if (this.matches(".library-card-fill-frame,.library-content-reveal")) {
          animation.pause();
          animation.currentTime = Number(animation.effect?.getComputedTiming().duration) / 2;
        }
        return animation;
      };
    });
    await page
      .getByRole("main")
      .getByRole("button", { name: /maraio/ })
      .click();
    await expect(page.getByRole("heading", { name: "Folder model", exact: true })).toBeVisible();
    const interruptedOpacity = await page
      .locator(".library-card-fill-frame")
      .evaluate((element) => Number(getComputedStyle(element).opacity));
    expect(interruptedOpacity).toBeGreaterThan(0.2);
    expect(interruptedOpacity).toBeLessThan(1);

    await page.goBack();
    await expect(page.getByRole("heading", { name: "Root model", exact: true })).toBeVisible();

    const reveals = await page.evaluate(() => window.libraryReveals);
    expect(reveals).toHaveLength(2);
    expect(reveals[1].opacity[0]).toBeCloseTo(interruptedOpacity, 5);
    expect(reveals[1].opacity.at(-1)).toBe(1);
  });

  test("cached breadcrumb visits reveal on each return", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Root model", exact: true })).toBeVisible();
    await expect(page.locator(".library-card-fill-frame")).toHaveCSS("opacity", "1");
    await page.evaluate(() => {
      window.libraryReveals = [];
    });
    await page
      .getByRole("main")
      .getByRole("button", { name: /maraio/ })
      .click();
    await expect(page.getByRole("heading", { name: "Folder model", exact: true })).toBeVisible();
    await expect.poll(() => page.evaluate(() => window.libraryReveals.length)).toBe(1);
    await page
      .locator("header")
      .getByRole("button", { name: "All Models", exact: true })
      .first()
      .press("Enter");
    await expect(page.getByRole("heading", { name: "Root model", exact: true })).toBeVisible();
    await expect.poll(() => page.evaluate(() => window.libraryReveals.length)).toBe(2);
    await page
      .getByRole("main")
      .getByRole("button", { name: /maraio/ })
      .click();
    await expect(page.getByRole("heading", { name: "Folder model", exact: true })).toBeVisible();

    await expect.poll(() => page.evaluate(() => window.libraryReveals.length)).toBe(3);
  });

  test("pane reflow does not replay the library reveal", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Root model", exact: true })).toBeVisible();
    await expect(page.locator(".library-card-fill-frame")).toHaveCSS("opacity", "1");
    await page.evaluate(() => {
      window.libraryReveals = [];
    });
    await page
      .getByRole("main")
      .getByRole("button", { name: /maraio/ })
      .click();
    await expect(page.getByRole("heading", { name: "Folder model", exact: true })).toBeVisible();
    await expect(page.locator(".library-card-fill-frame")).toHaveCSS("opacity", "1");
    await page.evaluate(() => {
      window.libraryReveals = [];
    });

    const toggle = page.getByRole("button", { name: "Toggle sidebar", exact: true });
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(page.locator("#collections-pane")).toBeHidden();

    expect(await page.evaluate(() => window.libraryReveals)).toEqual([]);
    await expect(page.getByRole("heading", { name: "Folder model", exact: true })).toBeVisible();
  });

  test("filter results reveal when their snapshot commits", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Root model", exact: true })).toBeVisible();
    await expect(page.locator(".library-card-fill-frame")).toHaveCSS("opacity", "1");
    await page.evaluate(() => {
      window.libraryReveals = [];
    });

    await page
      .getByRole("radiogroup", { name: "Filter models by status" })
      .getByRole("radio", { name: /Needs test/ })
      .click();

    await expect.poll(() => page.evaluate(() => window.libraryReveals.length)).toBe(1);
    await expect(page.getByRole("heading", { name: "Root model", exact: true })).toBeVisible();
  });

  test("list layout reveals without scaling", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Root model", exact: true })).toBeVisible();
    await expect(page.locator(".library-card-fill-frame")).toHaveCSS("opacity", "1");
    await page.evaluate(() => {
      window.libraryReveals = [];
    });

    await page.getByRole("button", { name: "List View", exact: true }).click();

    await expect.poll(() => page.evaluate(() => window.libraryReveals.length)).toBe(1);
    expect((await page.evaluate(() => window.libraryReveals))[0].transforms).toEqual([
      "none",
      "none",
    ]);
  });

  test("reduced motion keeps cached history visits immediate", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Root model", exact: true })).toBeVisible();
    await expect(page.locator(".library-card-fill-frame")).toHaveCSS("opacity", "1");
    await page.evaluate(() => {
      window.libraryReveals = [];
    });
    await page
      .getByRole("main")
      .getByRole("button", { name: /maraio/ })
      .click();
    await expect(page.getByRole("heading", { name: "Folder model", exact: true })).toBeVisible();
    await page.goBack();
    await expect(page.getByRole("heading", { name: "Root model", exact: true })).toBeVisible();

    expect(await page.evaluate(() => window.libraryReveals)).toEqual([]);
    await expect(page.locator(".library-card-fill-frame")).toHaveCSS("opacity", "1");
  });
});
