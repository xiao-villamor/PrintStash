/** Issue #434: real browser input, request scheduling and decoded-image replacement. */
import { expect, test, type Page, type Route } from "@playwright/test";
import { aModelListItem } from "../../src/test-support/factories";
import type { ArtifactPreviews, ModelPreviews } from "../../src/types/previews";
import { useMockApi } from "./_setup";

declare global {
  interface Window {
    releasePreviewDecode: () => void;
  }
}

useMockApi();
const image =
  '<svg xmlns="http://www.w3.org/2000/svg" width="480" height="360"><rect x="40" y="40" width="400" height="280" fill="steelblue"/></svg>';
const views = {
  kind: "artifact",
  state: "ready",
  file_id: 1,
  reason: null,
  cover_camera: "unknown",
  published: {
    profile: "orbit",
    manifest_url: "/api/v1/models/1/previews/generation",
    views: [
      ...(["orbit-0", "orbit-1", "orbit-2", "orbit-3"] as const).map((id) => ({
        id,
        role: "orbit" as const,
        url: `/api/v1/models/1/previews/generation/${id}`,
      })),
      { id: "top", role: "top", url: "/api/v1/models/1/previews/generation/top" },
      { id: "bottom", role: "bottom", url: "/api/v1/models/1/previews/generation/bottom" },
    ],
  },
} satisfies ArtifactPreviews;
const published = views.published;
async function fulfillImage(route: Route) {
  await route.fulfill({ contentType: "image/svg+xml", body: image });
}
async function library(page: Page, previews: ModelPreviews = views, count = 1) {
  const transferred: string[] = [];
  page.on("request", (request) => {
    if (/\/previews\/|\/thumbnail|\/stl/.test(new URL(request.url()).pathname))
      transferred.push(new URL(request.url()).pathname);
  });
  await page.route("**/api/v1/models/browse?**", (route) =>
    route.fulfill({
      json: {
        items: Array.from({ length: count }, (_, index) => ({
          kind: "model",
          model: aModelListItem({
            id: index + 1,
            name: `Bracket ${index + 1}`,
            thumbnail_url: `/api/v1/files/${index + 1}/thumbnail`,
            previews,
          }),
        })),
        total: count,
        next_cursor: null,
        browse_revision: "r1",
        authorization_revision: "a1",
      },
    }),
  );
  await page.route("**/api/v1/files/*/thumbnail", fulfillImage);
  await page.route("**/api/v1/models/*/previews/**", fulfillImage);
  await page.goto("/");
  const card = page.locator('[data-model-preview="Bracket 1"]');
  await expect(card.locator("img")).toHaveAttribute("data-preview-view", "cover");
  return {
    card,
    transferred,
    next: card.getByRole("button", { name: "Next preview", exact: true }),
  };
}
function secondaries(paths: string[]) {
  return paths.filter((path) => path.includes("/previews/"));
}

test.describe("Model preview browsing", () => {
  test("defers off-screen primary transfers", async ({ page }) => {
    await page.setViewportSize({ width: 1000, height: 650 });
    const { transferred } = await library(page, views, 60);
    expect(transferred).not.toContain("/api/v1/files/60/thumbnail");
    const last = page.locator('[data-model-preview="Bracket 60"]');
    await last.scrollIntoViewIfNeeded();
    await expect(last.locator("img")).toHaveAttribute("data-preview-view", "cover");
    expect(transferred).toContain("/api/v1/files/60/thumbnail");
  });

  test("prefetches neighbors after sustained hover", async ({ page }) => {
    const { card, transferred } = await library(page);
    await card.hover({ position: { x: 120, y: 30 } });
    await expect.poll(() => secondaries(transferred).length).toBe(2);
    expect(secondaries(transferred)).toEqual(
      expect.arrayContaining([published.views[0].url, published.views[3].url]),
    );
    await expect(card.locator("img")).toHaveAttribute("data-preview-view", "cover");
  });

  test("ignores fleeting hover", async ({ page }) => {
    const { card, transferred } = await library(page);
    await card.hover({ position: { x: 120, y: 30 } });
    await page.mouse.move(0, 0);
    await page.waitForTimeout(450);
    expect(secondaries(transferred)).toEqual([]);
  });

  test("prioritizes the focused arrow destination", async ({ page }) => {
    const { next, transferred } = await library(page);
    await next.focus();
    await expect.poll(() => secondaries(transferred)).toContain(published.views[0].url);
  });

  test("fetches an uncached view on activation", async ({ page }) => {
    const { next, card, transferred } = await library(page);
    await next.dispatchEvent("click");
    await expect(card.locator("img")).toHaveAttribute("data-preview-view", "orbit-0");
    expect(secondaries(transferred)).toContain(published.views[0].url);
  });

  test("retains the image during destination download", async ({ page }) => {
    const { next, card } = await library(page);
    let release!: () => void;
    const pending = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route("**/previews/generation/orbit-0", async (route) => {
      await pending;
      await fulfillImage(route);
    });
    await next.dispatchEvent("click");
    await expect(card.locator("img")).toHaveAttribute("data-preview-view", "cover");
    release();
    await expect(card.locator("img")).toHaveAttribute("data-preview-view", "orbit-0");
  });

  test("retains the image until decoding completes", async ({ page }) => {
    const { next, card } = await library(page);
    await page.evaluate(() => {
      const original = HTMLImageElement.prototype.decode;
      const gate = new Promise<void>((resolve) => {
        Object.assign(window, { releasePreviewDecode: resolve });
      });
      HTMLImageElement.prototype.decode = async function () {
        await original.call(this);
        await gate;
      };
    });
    await next.dispatchEvent("click");
    await page.waitForTimeout(150);
    await expect(card.locator("img")).toHaveAttribute("data-preview-view", "cover");
    await page.evaluate(() => window.releasePreviewDecode());
    await expect(card.locator("img")).toHaveAttribute("data-preview-view", "orbit-0");
  });

  test("honors the latest rapid selection", async ({ page }) => {
    const { next, card } = await library(page);
    await page.route("**/previews/generation/orbit-0", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 300));
      await fulfillImage(route);
    });
    await next.dispatchEvent("click");
    await next.dispatchEvent("click");
    await expect(card.locator("img")).toHaveAttribute("data-preview-view", "orbit-1");
    await page.waitForTimeout(350);
    await expect(card.locator("img")).toHaveAttribute("data-preview-view", "orbit-1");
  });

  test("keeps a usable image after destination failure", async ({ page }) => {
    const { next, card } = await library(page);
    await page.route("**/previews/generation/orbit-0", (route) => route.fulfill({ status: 503 }));
    await next.dispatchEvent("click");
    await expect(card.getByRole("button", { name: "Retry preview" })).toBeVisible();
    await expect(card.locator("img")).toHaveAttribute("data-preview-view", "cover");
    await page.unroute("**/previews/generation/orbit-0");
    await card.getByRole("button", { name: "Retry preview" }).click();
    await expect(card.locator("img")).toHaveAttribute("data-preview-view", "orbit-0");
  });

  test("respects data saving", async ({ page }) => {
    await page.addInitScript(() =>
      Object.defineProperty(navigator, "connection", { value: { saveData: true } }),
    );
    const { card, next, transferred } = await library(page);
    await card.hover({ position: { x: 120, y: 30 } });
    await page.waitForTimeout(450);
    expect(secondaries(transferred)).toEqual([]);
    await next.dispatchEvent("click");
    await expect(card.locator("img")).toHaveAttribute("data-preview-view", "orbit-0");
    expect(secondaries(transferred)).toEqual([published.views[0].url]);
  });

  test("does not transfer geometry while browsing previews", async ({ page }) => {
    const { card, next, transferred } = await library(page);
    await card.hover();
    await next.click();
    await expect(card.locator("img")).toHaveAttribute("data-preview-view", "orbit-0");
    expect(transferred.filter((path) => path.endsWith("/stl"))).toEqual([]);
  });

  test("keeps controls separate from card activation", async ({ page }) => {
    const { card, next } = await library(page);
    await next.click();
    await expect(card.locator("img")).toHaveAttribute("data-preview-view", "orbit-0");
    await expect(page).toHaveURL((url) => url.pathname === "/");
    await expect(
      page.getByRole("checkbox", { name: "Select Bracket 1", exact: true }),
    ).not.toBeChecked();
    await expect(card.locator("..")).not.toHaveClass(/opacity-40/);
  });

  test("supports keyboard view selection", async ({ page }) => {
    const { card, next } = await library(page);
    await next.focus();
    await expect(next).toBeFocused();
    await next.press("Enter");
    await expect(card.locator("img")).toHaveAttribute("data-preview-view", "orbit-0");
  });

  test("omits controls for a single image", async ({ page }) => {
    const { card } = await library(page, {
      ...views,
      published: { ...published, views: [published.views[0]] },
    });
    await expect(card.getByRole("button", { name: "Next preview" })).toHaveCount(0);
    await expect(card.getByRole("button", { name: "Model views" })).toHaveCount(0);
  });

  test("exposes named top and bottom views in the gallery", async ({ page }) => {
    const { card } = await library(page);
    await card.getByRole("button", { name: "Model views", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Model views" });
    await dialog.getByRole("button", { name: "Bottom", exact: true }).click();
    await expect(dialog.locator("img[data-preview-view]")).toHaveAttribute(
      "data-preview-view",
      "bottom",
    );
    await expect(dialog.getByRole("button", { name: "Top", exact: true })).toBeVisible();
  });

  test("keeps planar views in a named gallery", async ({ page }) => {
    const planar: ModelPreviews = {
      ...views,
      published: {
        profile: "planar",
        manifest_url: "/api/v1/models/1/previews/generation",
        views: [
          { id: "face", role: "face", url: "/api/v1/models/1/previews/generation/face" },
          { id: "reverse", role: "reverse", url: "/api/v1/models/1/previews/generation/reverse" },
          { id: "edge", role: "edge", url: "/api/v1/models/1/previews/generation/edge" },
        ],
      },
    };
    const { card } = await library(page, planar);
    await expect(card.getByRole("button", { name: "Next preview" })).toHaveCount(0);
    await card.getByRole("button", { name: "Model views", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Model views" });
    await dialog.getByRole("button", { name: "Reverse face", exact: true }).click();
    await expect(dialog.locator("img[data-preview-view]")).toHaveAttribute(
      "data-preview-view",
      "reverse",
    );
  });
});

test.describe("Touch previews", () => {
  test.use({ hasTouch: true, isMobile: true, viewport: { width: 390, height: 844 } });
  test("warms a neighbor only after deliberate interaction", async ({ page }) => {
    const { card, next, transferred } = await library(page);
    await expect(next).toBeVisible();
    await page.waitForTimeout(450);
    expect(secondaries(transferred)).toEqual([]);
    await next.tap();
    await expect(card.locator("img")).toHaveAttribute("data-preview-view", "orbit-0");
    await expect.poll(() => secondaries(transferred).length).toBe(2);
  });
});
