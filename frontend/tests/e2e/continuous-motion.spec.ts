/** Geometry and interruptions are checked in Chromium, not inferred from animation classes. */
import { expect, test, type Page } from "@playwright/test";
import { aCollectionNode, aModelListItem } from "../../src/test-support/factories";
import { useMockApi } from "./_setup";

useMockApi();
test.use({ viewport: { width: 1440, height: 900 } });

declare global {
  interface Window {
    holdLayoutStart: boolean;
    layoutDeltas: number[];
    sectionReveals: { surface: string; opacity: number[]; transforms: string[] }[];
  }
}

test.beforeEach(async ({ page, context }) => {
  const cdp = await context.newCDPSession(page);
  await cdp.send("Network.enable");
  await cdp.send("Network.setCacheDisabled", { cacheDisabled: true });
  await page.addInitScript(() => {
    window.sectionReveals = [];
    window.layoutDeltas = [];
    const animate = Element.prototype.animate;
    Element.prototype.animate = function (...args) {
      const result = animate.apply(this, args);
      if (
        this.matches(
          ".settings-main,.model-detail-tab-body,.upload-mode-content,.workbench-main-content,.library-content-reveal",
        ) &&
        result.effect instanceof KeyframeEffect
      ) {
        const frames = result.effect.getKeyframes();
        window.sectionReveals.push({
          surface: this.classList[0],
          opacity: frames.map((frame) => Number(frame.opacity)),
          transforms: frames.map((frame) => String(frame.transform ?? "none")),
        });
        result.pause();
        result.currentTime = Number(result.effect.getComputedTiming().duration) / 2;
      }
      if (this.matches(".library-model-content") && result.effect instanceof KeyframeEffect) {
        window.layoutDeltas.push(
          new DOMMatrixReadOnly(String(result.effect.getKeyframes()[0].transform)).m42,
        );
      }
      if (this.matches(".library-model-content,.library-collections-row")) {
        result.pause();
        result.currentTime = window.holdLayoutStart
          ? 0
          : Number(result.effect?.getComputedTiming().duration) / 2;
      }
      return result;
    };
  });
});

async function expectStationaryReveal(page: Page, surface: string) {
  await expect
    .poll(() =>
      page.evaluate((name) => window.sectionReveals.some((item) => item.surface === name), surface),
    )
    .toBe(true);
  const reveal = await page.evaluate(
    (name) => window.sectionReveals.filter((item) => item.surface === name).at(-1),
    surface,
  );
  expect(reveal).toBeDefined();
  expect(reveal!.transforms.every((value) => value === "none")).toBe(true);
  expect(reveal!.opacity.at(-1)).toBe(1);
  const content = page.locator(`.${surface}`);
  await expect(content).toHaveCSS("transform", "none");
  const opacity = await content.evaluate((element) => Number(getComputedStyle(element).opacity));
  expect(opacity).toBeGreaterThan(0.86);
  expect(opacity).toBeLessThan(1);
}

test("Settings tabs remain stationary and interrupted selections inherit opacity", async ({
  page,
}) => {
  await page.goto("/settings?section=design");
  const nav = page.getByRole("navigation", { name: "Settings sections" });
  await expect(nav).toBeVisible();
  expect(await page.evaluate(() => window.sectionReveals)).toEqual([]);
  await nav.getByRole("button", { name: "Previews", exact: true }).click();
  await expectStationaryReveal(page, "settings-main");
  const interrupted = await page
    .locator(".settings-main")
    .evaluate((element) => Number(getComputedStyle(element).opacity));
  await nav.getByRole("button", { name: "Appearance", exact: true }).click();
  await expect(page).toHaveURL(/section=design/);
  const reveals = await page.evaluate(() => window.sectionReveals);
  expect(reveals).toHaveLength(2);
  expect(reveals[1].opacity[0]).toBeCloseTo(interrupted, 5);
  await expectStationaryReveal(page, "settings-main");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(page.locator(".settings-main")).toHaveCSS("opacity", "1");
  await nav.getByRole("button", { name: "Previews", exact: true }).click();
  expect(await page.evaluate(() => window.sectionReveals)).toHaveLength(2);
});

test("Model detail tabs reveal without zooming", async ({ page }) => {
  await page.goto("/models/1");
  await page.getByRole("tab", { name: /Files/ }).click();
  await expectStationaryReveal(page, "model-detail-tab-body");
  await page.getByRole("tab", { name: /History/ }).click();
  await expectStationaryReveal(page, "model-detail-tab-body");
});

test("Upload mode switches reveal without zooming", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await page.getByRole("tab", { name: "From URL", exact: true }).click();
  await expect(
    page.getByPlaceholder("Model page, collection, or direct .stl/.zip link"),
  ).toBeVisible();
  await expectStationaryReveal(page, "upload-mode-content");
});

test("main destinations use one stationary reveal without a nested Favorites fade", async ({
  page,
}) => {
  await page.goto("/settings");
  const rail = page.getByRole("navigation", { name: "Navigation", exact: true });
  await rail.getByRole("link", { name: "Search", exact: true }).click();
  await expect(page).toHaveURL(/\/search/);
  await expectStationaryReveal(page, "workbench-main-content");
  await rail.getByRole("link", { name: "Favorites", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Favorites & saved views" })).toBeVisible();
  await expectStationaryReveal(page, "workbench-main-content");
  expect(await page.evaluate(() => window.sectionReveals.map((item) => item.surface))).toEqual([
    "workbench-main-content",
    "workbench-main-content",
  ]);
});

async function finishLayout(page: Page) {
  await page.locator(".library-model-content,.library-collections-row").evaluateAll((elements) => {
    for (const element of elements)
      for (const animation of element.getAnimations()) animation.finish();
  });
}

test.describe("collection row continuity", () => {
  test.beforeEach(async ({ page }) => {
    const collection = aCollectionNode({
      id: 1,
      name: "maraio",
      path: "maraio",
      child_count: 0,
      has_readme: false,
    });
    await page.route("**/api/v1/collections/lookup?**", (route) =>
      route.fulfill({ json: { collection, ancestors: [] } }),
    );
    await page.route("**/api/v1/collections/children?**", (route) =>
      route.fulfill({
        json: {
          items: new URL(route.request().url()).searchParams.has("parent_id")
            ? []
            : [
                collection,
                aCollectionNode({ id: 2, name: "Other", path: "other", child_count: 0 }),
              ],
          next_cursor: null,
        },
      }),
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
  });

  for (const width of [1440, 390]) {
    test(`collapse and cached expansion move the grid continuously at ${width}px`, async ({
      page,
    }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/");
      await expect(page.getByRole("heading", { name: "Root model", exact: true })).toBeVisible();
      const region = page.locator(".library-collections-region");
      const height = () =>
        region.evaluate(
          (element) =>
            element.nextElementSibling!.getBoundingClientRect().top -
            element.getBoundingClientRect().top,
        );
      const full = await height();
      expect(full).toBeGreaterThan(0);
      expect(
        await page
          .locator(".library-model-content")
          .evaluate((element) => element.getAnimations().length),
      ).toBe(0);
      await page
        .getByRole("main")
        .getByRole("button", { name: /maraio/ })
        .click();
      await expect(page.getByRole("heading", { name: "Folder model", exact: true })).toBeVisible();
      await expect.poll(height).toBeLessThan(full);
      expect(await height()).toBeGreaterThan(0);
      expect(await page.evaluate(() => window.layoutDeltas[0])).toBeCloseTo(full, 0);
      await expect(region).toHaveAttribute("inert", "");
      const toolbarOffset = await page
        .locator(".library-model-toolbar-wrap")
        .evaluate(
          (element) =>
            element.getBoundingClientRect().top -
            element.parentElement!.previousElementSibling!.getBoundingClientRect().top,
        );
      expect(toolbarOffset).toBeCloseTo(await height(), 0);
      await finishLayout(page);
      await expect.poll(height).toBe(0);
      await expect(region.locator(".library-subcollections")).toHaveCount(0);
      await page.goBack();
      await expect(page.getByRole("heading", { name: "Root model", exact: true })).toBeVisible();
      await expect.poll(height).toBeGreaterThan(0);
      expect(await height()).toBeLessThan(full);
      expect(await page.evaluate(() => window.layoutDeltas[1])).toBeCloseTo(-full, 0);
      await finishLayout(page);
      await expect.poll(height).toBe(full);
      await expect(region).not.toHaveAttribute("inert");
    });
  }

  test("reversing a collapse keeps the visible height and the restored row", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Root model", exact: true })).toBeVisible();
    const region = page.locator(".library-collections-region");
    const full = await region.evaluate(
      (element) =>
        element.nextElementSibling!.getBoundingClientRect().top -
        element.getBoundingClientRect().top,
    );
    await page
      .getByRole("main")
      .getByRole("button", { name: /maraio/ })
      .click();
    await expect(page.getByRole("heading", { name: "Folder model", exact: true })).toBeVisible();
    await expect
      .poll(() =>
        region.evaluate(
          (element) =>
            element.nextElementSibling!.getBoundingClientRect().top -
            element.getBoundingClientRect().top,
        ),
      )
      .toBeLessThan(full);
    const midway = await region.evaluate(
      (element) =>
        element.nextElementSibling!.getBoundingClientRect().top -
        element.getBoundingClientRect().top,
    );
    expect(midway).toBeGreaterThan(0);
    await page.evaluate(() => {
      window.holdLayoutStart = true;
    });
    await page.goBack();
    await expect(page.getByRole("heading", { name: "Root model", exact: true })).toBeVisible();
    await expect(region).toHaveAttribute("data-open", "true");
    expect(
      await region.evaluate(
        (element) =>
          element.nextElementSibling!.getBoundingClientRect().top -
          element.getBoundingClientRect().top,
      ),
    ).toBeCloseTo(midway, 0);
    await finishLayout(page);
    await expect
      .poll(() =>
        region.evaluate(
          (element) =>
            element.nextElementSibling!.getBoundingClientRect().top -
            element.getBoundingClientRect().top,
        ),
      )
      .toBe(full);
    await expect(region.getByRole("button", { name: /maraio/ })).toBeVisible();
  });

  for (const preference of ["before", "during"] as const) {
    test(`reduced motion ${preference} navigation removes the row immediately`, async ({
      page,
    }) => {
      if (preference === "before") await page.emulateMedia({ reducedMotion: "reduce" });
      await page.goto("/");
      await page
        .getByRole("main")
        .getByRole("button", { name: /maraio/ })
        .click();
      await expect(page.getByRole("heading", { name: "Folder model", exact: true })).toBeVisible();
      if (preference === "during") await page.emulateMedia({ reducedMotion: "reduce" });
      const region = page.locator(".library-collections-region");
      await expect(region.locator(".library-subcollections")).toHaveCount(0);
      expect(
        await region.evaluate(
          (element) =>
            element.nextElementSibling!.getBoundingClientRect().top -
            element.getBoundingClientRect().top,
        ),
      ).toBe(0);
      expect(
        await page
          .locator(".library-model-content")
          .evaluate((element) => element.getAnimations().length),
      ).toBe(0);
      if (preference === "before")
        expect(await page.evaluate(() => window.sectionReveals)).toEqual([]);
      await expect(page.locator(".library-content-reveal")).toHaveCSS("opacity", "1");
    });
  }
});
