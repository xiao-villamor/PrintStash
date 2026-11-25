/** Issue #434: real browser input, request scheduling and decoded-image replacement. */
import { expect, test, type Page, type Route } from "@playwright/test";
import { aModelListItem } from "../../src/test-support/factories";
import type { ArtifactPreviews, ModelPreviews } from "../../src/types/previews";
import { NO_PREVIEWS } from "../../src/types/previews";
import { useMockApi } from "./_setup";
import { collapseNavigationSidebar } from "../e2e-real/util";

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
const planar = {
  ...views,
  published: {
    profile: "planar",
    manifest_url: published.manifest_url,
    views: [
      { id: "face", role: "face", url: "/api/v1/models/1/previews/generation/face" },
      { id: "reverse", role: "reverse", url: "/api/v1/models/1/previews/generation/reverse" },
      { id: "edge", role: "edge", url: "/api/v1/models/1/previews/generation/edge" },
      { id: "top", role: "top", url: "/api/v1/models/1/previews/generation/top" },
      { id: "bottom", role: "bottom", url: "/api/v1/models/1/previews/generation/bottom" },
    ],
  },
} satisfies ArtifactPreviews;
async function fulfillImage(route: Route) {
  await route.fulfill({ contentType: "image/svg+xml", body: image });
}
async function library(page: Page, previews: ModelPreviews = views, count = 1, href = "/") {
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
  await page.goto(href);
  // Controls sit beside the card's link, over its image: scope to the card.
  const card = page
    .locator("main article")
    .filter({ has: page.locator('[data-model-preview="Bracket 1"]') });
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
  test("follows a gallery drag with a compact on-page preview", async ({ page }, testInfo) => {
    await page.addInitScript(() => {
      localStorage.setItem("printstash.theme", "dark");
      document.addEventListener("dragstart", () => {
        document.body.dataset.nativeDrag = "true";
      });
    });
    const { card } = await library(page);
    await collapseNavigationSidebar(page);
    const image = card.locator("[data-model-preview]");
    await expect(image.locator("img")).toHaveJSProperty("complete", true);
    const bounds = await image.boundingBox();
    if (!bounds) throw new Error("Missing gallery drag source");
    const start = { x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2 };
    const preview = page.locator("[data-gallery-drag-preview]");

    await page.mouse.move(start.x, start.y);
    await page.mouse.down();
    await page.mouse.move(start.x + 30, start.y, { steps: 3 });
    await expect(preview).toBeVisible();
    const end = { x: start.x + 110, y: start.y - 40 };
    await page.mouse.move(end.x, end.y);
    await expect(preview).toHaveText("Bracket 1");
    await expect(preview.locator("img")).toBeVisible();
    await expect(preview).toHaveCSS("height", "48px");
    const frame = await preview.boundingBox();
    if (!frame) throw new Error("Missing rendered drag feedback");
    expect(frame.width).toBeLessThan(160);
    expect(Math.abs(frame.x - (end.x + 12))).toBeLessThan(3);
    expect(Math.abs(frame.y - (end.y + 16))).toBeLessThan(3);
    await expect(page.locator("body")).not.toHaveAttribute("data-native-drag");
    await preview.screenshot({ path: testInfo.outputPath("model-drag-preview.png") });
    await page.keyboard.press("Escape");
    await page.mouse.up();
    await expect(preview).toHaveCount(0);
  });

  test("hides gallery controls after dropping outside a collection", async ({ page }) => {
    const { card, next } = await library(page);
    await collapseNavigationSidebar(page);
    const navigation = card.locator("[data-preview-navigation]");
    await card.locator("[data-model-preview]").hover();
    await next.click();
    await expect(next).toBeFocused();
    await expect(navigation).toHaveCSS("opacity", "1");

    const heading = page.getByRole("main").getByRole("heading", { name: "All Models" });
    const source = await card.locator("[data-model-preview]").boundingBox();
    const target = await heading.boundingBox();
    if (!source || !target) throw new Error("Missing gallery drag bounds");
    const x = source.x + source.width / 2;
    const y = source.y + source.height / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + 30, y, { steps: 3 });
    const preview = page.locator("[data-gallery-drag-preview]");
    await expect(preview).toBeVisible();
    await page.mouse.move(target.x, target.y, { steps: 6 });
    await page.mouse.up();
    await expect(preview).toHaveCount(0);

    await expect(navigation).toHaveCSS("opacity", "0");
    await expect(card.getByRole("checkbox", { name: "Select Bracket 1" }).locator("..")).toHaveCSS(
      "opacity",
      "0",
    );
  });

  test("releasing a drag over its original card keeps the gallery open", async ({ page }) => {
    const { card } = await library(page);
    await collapseNavigationSidebar(page);
    const image = card.locator("[data-model-preview]");
    const bounds = await image.boundingBox();
    if (!bounds) throw new Error("Missing gallery drag source");
    const x = bounds.x + bounds.width / 2;
    const y = bounds.y + bounds.height / 2;
    const galleryUrl = page.url();
    const preview = page.locator("[data-gallery-drag-preview]");
    const main = page.getByRole("main");

    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + 40, y, { steps: 4 });
    await expect(preview).toBeVisible();
    await page.mouse.move(x, y, { steps: 4 });
    await page.mouse.up();
    await expect(preview).toHaveCount(0);
    await expect(page).toHaveURL(galleryUrl);
    await expect(main).not.toHaveAttribute("data-inspector-open");

    await image.click();
    await expect(main).toHaveAttribute("data-inspector-open", "true");
  });

  test("browses all five flat-model views from the card", async ({ page }) => {
    const { card, next } = await library(page, planar);
    await card.hover();
    await expect(next).toBeVisible();
    for (const id of ["face", "reverse", "edge", "top", "bottom", "cover"]) {
      await next.click();
      await expect(card.locator("img")).toHaveAttribute("data-preview-view", id);
    }
    await card.getByRole("button", { name: "Previous preview", exact: true }).click();
    await expect(card.locator("img")).toHaveAttribute("data-preview-view", "bottom");
  });

  for (const [layout, href] of [
    ["Library", "/"],
    ["Favorites", "/?favorites=true"],
  ] as const) {
    test(`keeps ${layout} cover sizing consistent when angle previews are available`, async ({
      page,
    }) => {
      await page.setViewportSize({ width: 2560, height: 1440 });
      const multiple = await library(page, views, 1, href);
      const multiImage = await multiple.card.locator("img").boundingBox();
      const multiTitle = await multiple.card
        .getByRole("heading", { name: "Bracket 1" })
        .boundingBox();
      const single = await library(page, NO_PREVIEWS, 1, href);
      const singleImage = await single.card.locator("img").boundingBox();
      const singleTitle = await single.card
        .getByRole("heading", { name: "Bracket 1" })
        .boundingBox();
      expect(multiImage).not.toBeNull();
      expect(singleImage).not.toBeNull();
      expect(Math.abs(multiImage!.height - singleImage!.height)).toBeLessThan(1);
      expect(Math.abs(multiImage!.width - singleImage!.width)).toBeLessThan(1);
      expect(Math.abs(multiTitle!.y - singleTitle!.y)).toBeLessThan(1);
    });
  }

  test("gives card images more room on a 2K display", async ({ page }) => {
    await page.setViewportSize({ width: 2560, height: 1440 });
    const { card } = await library(page);
    const frame = await card.locator("[data-model-preview]").boundingBox();
    const picture = await card.locator("img").boundingBox();
    expect(picture!.height).toBeGreaterThan(frame!.width / 2);
  });

  test("opens a large preview gallery on a 2K display", async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 2560, height: 1440 });
    const { card } = await library(page);
    await card.getByRole("button", { name: "Model views", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Model views" });
    const frame = await dialog.locator("[data-model-preview]").boundingBox();
    expect(frame!.width).toBeGreaterThanOrEqual(1000);
    expect(frame!.height).toBeGreaterThanOrEqual(700);
    await expect(dialog.getByRole("button", { name: "Bottom", exact: true })).toBeInViewport();
    await page.screenshot({ path: testInfo.outputPath("gallery-2k.png") });
  });

  test("keeps the enlarged gallery usable on a narrow screen", async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const { card } = await library(page, planar);
    await card.getByRole("button", { name: "Model views", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Model views" });
    const bounds = await dialog.boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
    await dialog.getByRole("button", { name: "Bottom", exact: true }).click();
    await expect(dialog.locator("img[data-preview-view]")).toHaveAttribute(
      "data-preview-view",
      "bottom",
    );
    await expect(dialog.getByRole("button", { name: "Close", exact: true })).toBeInViewport();
    await page.screenshot({ path: testInfo.outputPath("gallery-mobile.png") });
  });

  test("cycles the card through the cover in both directions", async ({ page }) => {
    const { card, next } = await library(page);
    for (const id of ["orbit-1", "orbit-2", "orbit-3", "cover"]) {
      await next.click();
      await expect(card.locator("img")).toHaveAttribute("data-preview-view", id);
    }
    const previous = card.getByRole("button", { name: "Previous preview", exact: true });
    for (const id of ["orbit-3", "orbit-2", "orbit-1", "cover"]) {
      await previous.click();
      await expect(card.locator("img")).toHaveAttribute("data-preview-view", id);
    }
  });

  for (const theme of ["light", "dark"] as const) {
    test(`shows complete preview counts on a narrow ${theme} screen`, async ({
      page,
    }, testInfo) => {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.addInitScript((value) => localStorage.setItem("printstash.theme", value), theme);
      await page.route("**/api/v1/admin/work/preview-activity", (route) =>
        route.fulfill({
          json: { total: 1000, completed: 250, remaining: 700, skipped: 40, failed: 10 },
        }),
      );
      await library(page);
      await page.getByRole("button", { name: "Notifications", exact: true }).click();
      const counts = page.getByText(
        "1000 preview tasks · 250 done · 700 remaining · 40 skipped · 10 failed",
        { exact: true },
      );

      await expect(counts).toBeVisible();
      expect(await counts.evaluate((element) => element.scrollHeight <= element.clientHeight)).toBe(
        true,
      );
      await page.screenshot({ path: testInfo.outputPath(`preview-progress-${theme}.png`) });
    });
  }

  for (const theme of ["light", "dark"] as const) {
    for (const controlName of ["Next preview", "Model views"] as const) {
      test(`keeps ${controlName} readable in ${theme} mode`, async ({ page }) => {
        await page.addInitScript((value) => localStorage.setItem("printstash.theme", value), theme);
        const { card, next } = await library(page);
        await card.locator('[data-model-preview="Bracket 1"]').hover();
        await expect(card.locator("[data-preview-navigation]")).toHaveCSS("opacity", "1");
        if (theme === "light") await next.hover();
        const control = card.getByRole("button", { name: controlName, exact: true });
        const contrast = () =>
          control.evaluate((element, theme) => {
            const canvas = document.createElement("canvas");
            canvas.width = canvas.height = 1;
            const context = canvas.getContext("2d")!;
            function luminance(color: string, backdrop?: string) {
              context.clearRect(0, 0, 1, 1);
              if (backdrop) {
                context.fillStyle = backdrop;
                context.fillRect(0, 0, 1, 1);
              }
              context.fillStyle = color;
              context.fillRect(0, 0, 1, 1);
              const [r, g, b] = [...context.getImageData(0, 0, 1, 1).data].map((value) => {
                const channel = value / 255;
                return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
              });
              return 0.2126 * r + 0.7152 * g + 0.0722 * b;
            }
            const style = getComputedStyle(element);
            const foreground = luminance(style.color);
            // The palest image is worst for light icons, and vice versa for dark icons.
            const background = luminance(
              style.backgroundColor,
              theme === "dark" ? "white" : "black",
            );
            return (
              (Math.max(foreground, background) + 0.05) / (Math.min(foreground, background) + 0.05)
            );
          }, theme);
        await expect.poll(contrast).toBeGreaterThanOrEqual(4.5);
      });
    }
  }

  test("lets the image show through dark card controls", async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("printstash.theme", "dark"));
    const { card, next } = await library(page);
    await card.locator('[data-model-preview="Bracket 1"]').hover();
    for (const control of [
      next,
      card.getByRole("button", { name: "Model views", exact: true }),
      card.getByRole("link", { name: "Open full page", exact: true }),
    ]) {
      const alpha = await control.evaluate((element) => {
        const canvas = document.createElement("canvas");
        canvas.width = canvas.height = 1;
        const context = canvas.getContext("2d")!;
        context.fillStyle = getComputedStyle(element).backgroundColor;
        context.fillRect(0, 0, 1, 1);
        return context.getImageData(0, 0, 1, 1).data[3];
      });
      expect(alpha).toBeGreaterThan(0);
      expect(alpha).toBeLessThan(255);
    }
  });

  for (const theme of ["light", "dark"] as const) {
    test(`outlines card controls against the image area in ${theme} mode`, async ({ page }) => {
      await page.addInitScript((value) => localStorage.setItem("printstash.theme", value), theme);
      const { card } = await library(page);
      const plate = await card
        .locator('[data-model-preview="Bracket 1"]')
        .evaluate((element) => getComputedStyle(element).backgroundColor);
      for (const control of [
        card.getByRole("button", { name: "Model views", exact: true }),
        card.getByRole("checkbox", { name: /^Select / }),
      ]) {
        // A photo or render can sit on any colour; the chip is bounded by its own edge and fill.
        const style = await control.evaluate((element) => {
          const { backgroundColor, borderTopColor, borderTopWidth } = getComputedStyle(element);
          return { backgroundColor, borderTopColor, borderTopWidth };
        });
        expect(style.borderTopWidth).toBe("1px");
        expect(style.borderTopColor).not.toBe("rgba(0, 0, 0, 0)");
        expect(style.backgroundColor).not.toBe("rgba(0, 0, 0, 0)");
        expect(style.backgroundColor).not.toBe(plate);
      }
    });
  }

  test("shows preview arrows without a resting outline", async ({ page }, testInfo) => {
    await page.addInitScript(() => localStorage.setItem("printstash.theme", "dark"));
    const { card, next } = await library(page);
    await collapseNavigationSidebar(page);

    await card.locator("[data-model-preview]").hover();
    await next.hover();

    await card.screenshot({ path: testInfo.outputPath("preview-arrows.png") });
    await expect(next).toHaveCSS("border-top-width", "0px", { timeout: 1000 });
  });

  test("uses a neutral shadow on dark card hover", async ({ page }, testInfo) => {
    await page.addInitScript(() => localStorage.setItem("printstash.theme", "dark"));
    const { card } = await library(page);
    await collapseNavigationSidebar(page);

    await card.locator("[data-model-preview]").hover();

    await page.screenshot({ path: testInfo.outputPath("card-hover.png") });
    const shadow = await card.evaluate((element) => {
      const style = getComputedStyle(element);
      const reference = document.createElement("span");
      reference.style.color = "var(--border-strong)";
      element.append(reference);
      const neutral = getComputedStyle(reference).color;
      reference.remove();
      return {
        radius: style.borderRadius,
        overflow: getComputedStyle(element.closest(".library-virtual-row")!).overflowX,
        neutral: style.borderTopColor === neutral,
        shadow: style.boxShadow,
      };
    });
    expect(shadow).toMatchObject({ radius: "6px", neutral: true, overflow: "visible" });
    expect(shadow.shadow).toContain("0px 14px 28px -12px");
  });

  test("persists a hidden card preview label across reload", async ({ page }) => {
    await page.goto("/settings?section=design");
    await collapseNavigationSidebar(page);
    const label = page.getByRole("switch", { name: "Show preview label" });
    await expect(label).toBeChecked();

    await label.click();
    await expect(
      page.getByRole("article", { name: "Example bracket" }).getByText("Cover", { exact: true }),
    ).toHaveCount(0);
    await page.reload();
    await expect(label).not.toBeChecked();
    const { card } = await library(page);
    const gallery = card.getByRole("button", { name: "Model views", exact: true });
    await expect(gallery).toHaveText("");
    await gallery.press("Enter");

    await expect(page.getByRole("dialog", { name: "Model views" })).toBeVisible();
  });

  test("keeps card arrows compact over the image", async ({ page }) => {
    const { card, next } = await library(page);

    await card.hover();

    expect(await next.boundingBox()).toMatchObject({ width: 24, height: 32 });
  });

  test("browses views with arrows inside the gallery", async ({ page }) => {
    const { card } = await library(page);
    await card.locator("[data-model-preview]").hover();
    await card.getByRole("button", { name: "Model views", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Model views" });

    await dialog.getByRole("button", { name: "Next preview", exact: true }).click();

    await expect(dialog.locator("img[data-preview-view]")).toHaveAttribute(
      "data-preview-view",
      "orbit-0",
    );
  });

  test("browses views with the arrow keys inside the gallery", async ({ page }) => {
    const { card } = await library(page);
    await card.getByRole("button", { name: "Model views", exact: true }).press("Enter");
    const dialog = page.getByRole("dialog", { name: "Model views" });
    await dialog.getByRole("button", { name: "Next preview", exact: true }).focus();

    await page.keyboard.press("ArrowRight");

    await expect(dialog.locator("img[data-preview-view]")).toHaveAttribute(
      "data-preview-view",
      "orbit-0",
    );
    await expect(dialog.getByRole("button", { pressed: true })).toHaveAccessibleName(/Front left/);
  });

  test("fills the dialog width with the gallery stage", async ({ page }) => {
    const { card } = await library(page);
    await card.getByRole("button", { name: "Model views", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Model views" });
    const stage = await dialog.locator("[data-model-preview]").boundingBox();
    const strip = await dialog.getByRole("group", { name: "Model views" }).boundingBox();
    expect(Math.abs(stage!.width - strip!.width)).toBeLessThan(1);
  });

  test("rounds the gallery view indicator", async ({ page }, testInfo) => {
    await page.emulateMedia({ colorScheme: "dark" });
    const { card } = await library(page);
    await collapseNavigationSidebar(page);

    await card.getByRole("button", { name: "Model views", exact: true }).press("Enter");

    const dialog = page.getByRole("dialog", { name: "Model views" });
    const indicator = dialog.locator('[aria-live="polite"]');
    await expect(indicator).toBeVisible();
    await dialog.screenshot({ path: testInfo.outputPath("gallery-indicator.png") });
    await expect(indicator).toHaveCSS("border-radius", "6px", { timeout: 1000 });
  });

  test("centers the gallery view indicator", async ({ page }) => {
    const { card } = await library(page);
    await card.getByRole("button", { name: "Model views", exact: true }).press("Enter");
    const dialog = page.getByRole("dialog", { name: "Model views" });

    const stage = await dialog.locator("[data-model-preview]").boundingBox();
    const indicator = await dialog.locator('[aria-live="polite"]').boundingBox();

    expect(
      Math.abs(indicator!.x + indicator!.width / 2 - (stage!.x + stage!.width / 2)),
    ).toBeLessThan(1);
  });

  test("keeps the view count discoverable before hover", async ({ page }) => {
    const { card } = await library(page);
    await page.mouse.move(0, 0);
    const gallery = card.getByRole("button", { name: "Model views", exact: true });
    await expect(gallery).toHaveText("6 views");
    const opacity = await gallery.evaluate((element) => {
      let opacity = 1;
      for (let current: Element | null = element; current; current = current.parentElement) {
        opacity *= Number(getComputedStyle(current).opacity);
      }
      return opacity;
    });
    expect(opacity).toBe(1);
    await expect(card.locator("[data-preview-navigation]")).toHaveCSS("opacity", "0");
  });

  test("keeps the gallery control clear of the view status on narrow cards", async ({ page }) => {
    const { card } = await library(page);
    await card.evaluate((element) => {
      element.style.width = "220px";
    });
    await card.hover();
    const gallery = await card
      .getByRole("button", { name: "Model views", exact: true })
      .boundingBox();
    const status = await card.locator('[aria-live="polite"]').boundingBox();
    expect(gallery).not.toBeNull();
    expect(status).not.toBeNull();
    const overlaps =
      gallery!.x < status!.x + status!.width &&
      gallery!.x + gallery!.width > status!.x &&
      gallery!.y < status!.y + status!.height &&
      gallery!.y + gallery!.height > status!.y;
    expect(overlaps).toBe(false);
  });

  test("reveals preview controls for keyboard focus", async ({ page }) => {
    const { card, next } = await library(page);
    await page.mouse.move(0, 0);
    await next.focus();
    await expect(card.locator("[data-preview-navigation]")).toHaveCSS("opacity", "1");
    await next.press("Enter");
    await expect(card.locator("img")).toHaveAttribute("data-preview-view", "orbit-0");
  });

  test("keeps the preview image above the gallery toolbar", async ({ page }) => {
    const { card } = await library(page);
    const image = await card.locator("img").boundingBox();
    const gallery = await card
      .getByRole("button", { name: "Model views", exact: true })
      .boundingBox();
    expect(image).not.toBeNull();
    expect(gallery).not.toBeNull();
    expect(image!.y + image!.height).toBeLessThanOrEqual(gallery!.y);
  });

  test("reveals preview controls immediately with reduced motion", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    const { card } = await library(page);
    await card.hover();
    await expect(card.locator("[data-preview-navigation]")).toHaveCSS("transition-duration", "0s");
  });

  test("retains the gallery through its shared modal exit", async ({ page }) => {
    await page.clock.install({ time: new Date("2026-01-01T00:00:00Z") });
    const { card } = await library(page);
    await card.getByRole("button", { name: "Model views", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Model views" });
    await expect(dialog).toHaveAttribute("data-state", "open");
    await page.clock.pauseAt(new Date("2026-01-01T00:01:00Z"));
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveAttribute("data-state", "closed");
    await page.clock.runFor(300);
    await expect(dialog).toHaveCount(0);
    await expect(card.getByRole("button", { name: "Model views", exact: true })).toBeFocused();
  });

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
      expect.arrayContaining([published.views[1].url, published.views[3].url]),
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
    await expect.poll(() => secondaries(transferred)).toContain(published.views[1].url);
  });

  test("fetches an uncached view on activation", async ({ page }) => {
    const { next, card, transferred } = await library(page);
    await next.dispatchEvent("click");
    await expect(card.locator("img")).toHaveAttribute("data-preview-view", "orbit-1");
    expect(secondaries(transferred)).toContain(published.views[1].url);
  });

  test("retains the image during destination download", async ({ page }) => {
    const { next, card } = await library(page);
    let release!: () => void;
    const pending = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route("**/previews/generation/orbit-1", async (route) => {
      await pending;
      await fulfillImage(route);
    });
    await next.dispatchEvent("click");
    await expect(card.locator("img")).toHaveAttribute("data-preview-view", "cover");
    release();
    await expect(card.locator("img")).toHaveAttribute("data-preview-view", "orbit-1");
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
    await expect(card.locator("img")).toHaveAttribute("data-preview-view", "orbit-1");
  });

  test("honors the latest rapid selection", async ({ page }) => {
    const { next, card } = await library(page);
    await page.route("**/previews/generation/orbit-1", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 300));
      await fulfillImage(route);
    });
    await next.dispatchEvent("click");
    await next.dispatchEvent("click");
    await expect(card.locator("img")).toHaveAttribute("data-preview-view", "orbit-2");
    await page.waitForTimeout(350);
    await expect(card.locator("img")).toHaveAttribute("data-preview-view", "orbit-2");
  });

  test("keeps a usable image after destination failure", async ({ page }) => {
    const { next, card } = await library(page);
    await page.route("**/previews/generation/orbit-1", (route) => route.fulfill({ status: 503 }));
    await next.dispatchEvent("click");
    await expect(card.getByRole("button", { name: "Retry preview" })).toBeVisible();
    await expect(card.locator("img")).toHaveAttribute("data-preview-view", "cover");
    await page.unroute("**/previews/generation/orbit-1");
    await card.getByRole("button", { name: "Retry preview" }).click();
    await expect(card.locator("img")).toHaveAttribute("data-preview-view", "orbit-1");
  });

  test("asks for new views when the published ones are gone", async ({ page }) => {
    const { next, card } = await library(page);
    const regenerated: string[] = [];
    await page.route("**/api/v1/models/1/previews/regenerate", (route) => {
      regenerated.push(route.request().method());
      return route.fulfill({ status: 202, json: { queued: 1 } });
    });
    await page.route("**/previews/generation/orbit-1", (route) =>
      route.fulfill({ status: 410, json: { detail: "preview_bytes_unavailable" } }),
    );
    await next.dispatchEvent("click");
    const retry = card.getByRole("button", { name: "Retry preview" });
    await expect(retry).toBeVisible();
    // A compact icon over the image, not a text button.
    expect(await retry.boundingBox()).toMatchObject({ width: 32, height: 32 });

    await retry.click();

    await expect.poll(() => regenerated).toEqual(["POST"]);
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
    await expect(card.locator("img")).toHaveAttribute("data-preview-view", "orbit-1");
    expect(secondaries(transferred)).toEqual([published.views[1].url]);
  });

  test("does not transfer geometry while browsing previews", async ({ page }) => {
    const { card, next, transferred } = await library(page);
    await card.hover();
    await next.click();
    await expect(card.locator("img")).toHaveAttribute("data-preview-view", "orbit-1");
    expect(transferred.filter((path) => path.endsWith("/stl"))).toEqual([]);
  });

  test("keeps controls separate from card activation", async ({ page }) => {
    const { card, next } = await library(page);
    await next.click();
    await expect(card.locator("img")).toHaveAttribute("data-preview-view", "orbit-1");
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
    await expect(card.locator("img")).toHaveAttribute("data-preview-view", "orbit-1");
  });

  test("omits controls for a single image", async ({ page }) => {
    const { card } = await library(page, {
      ...views,
      published: { ...published, views: [published.views[0]] },
    });
    await expect(card.getByRole("button", { name: "Next preview" })).toHaveCount(0);
    await expect(card.getByRole("button", { name: "Model views" })).toHaveCount(0);
  });

  test("exposes named Top/Bottom views in the gallery", async ({ page }) => {
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

  test("keeps the generated front view selectable in the gallery", async ({ page }) => {
    const { card } = await library(page);
    await card.getByRole("button", { name: "Model views", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Model views" });
    await dialog.getByRole("button", { name: "Oblique 1", exact: true }).click();
    await expect(dialog.locator("img[data-preview-view]")).toHaveAttribute(
      "data-preview-view",
      "orbit-0",
    );
  });

  test("keeps planar views in a named gallery", async ({ page }) => {
    const { card } = await library(page, planar);
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
    await expect(card.locator("img")).toHaveAttribute("data-preview-view", "orbit-1");
    await expect.poll(() => secondaries(transferred).length).toBe(2);
  });
});
