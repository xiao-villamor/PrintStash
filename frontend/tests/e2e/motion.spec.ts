/*
 * The design rules from DESIGN.md that only exist once a page is assembled.
 *
 * Library route results remain still in either motion preference. Entrance
 * transforms used to move semantic scroll anchors after restoration measured them.
 *
 * The layering row is here because z-index bugs are invisible until two surfaces
 * are open at once: the header menu and the recent-folder menu each work alone
 * and one renders under the other.
 */
import { expect, test, type Page } from "@playwright/test";

import { useMockApi } from "./_setup";
import { animationStarts, recordAnimationStarts } from "./_animations";
import { aCollectionNode, aModelListItem, aMultipartModel } from "../../src/test-support/factories";

useMockApi();

declare global {
  interface Window {
    revisitedThumbnailOpacities: number[];
    releaseThumbnailDecode: () => void;
    thumbnailReveals: {
      duration: number | string;
      start: string | number | undefined;
      end: string | number | undefined;
      transform: string;
    }[];
    descriptionHeights: number[];
    panelMotions: { widths: number[]; transforms: string[]; duration: number }[];
  }
}

async function recordPanelMotion(page: Page, id: string, pause = false) {
  await page.addInitScript(
    ({ panelId, pause }) => {
      window.panelMotions = [];
      const animate = Element.prototype.animate;
      Element.prototype.animate = function (...args) {
        const animation = animate.apply(this, args);
        if (this.id === panelId && animation.effect instanceof KeyframeEffect) {
          if (pause) animation.pause();
          const frames = animation.effect.getKeyframes();
          window.panelMotions.push({
            widths: frames.map((frame) => parseFloat(String(frame.width))),
            transforms: frames.map((frame) => String(frame.transform ?? "none")),
            duration: Number(animation.effect.getTiming().duration),
          });
        }
        return animation;
      };
    },
    { panelId: id, pause },
  );
}

async function folderWithDescription(page: Page) {
  const collection = aCollectionNode({ id: 1, name: "maraio", path: "maraio", has_readme: true });
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
              id: folder ? 2 : 1,
              name: folder ? "Folder model" : "Root model",
              thumbnail_url: "/api/v1/files/1/thumbnail",
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
}

test.describe("motion and layering", () => {
  test.beforeEach(async ({ page }) => {
    await recordAnimationStarts(page);
  });

  for (const scenario of [
    { width: 1920, reducedMotion: "no-preference" },
    { width: 390, reducedMotion: "no-preference" },
    { width: 390, reducedMotion: "reduce" },
  ] as const) {
    test(`new thumbnails crossfade without moving cards at ${scenario.width}px with ${scenario.reducedMotion}`, async ({
      page,
    }, info) => {
      await page.setViewportSize({ width: scenario.width, height: 844 });
      await page.emulateMedia({ reducedMotion: scenario.reducedMotion });
      await folderWithDescription(page);
      await page.route("**/api/v1/files/1/thumbnail", (route) =>
        route.fulfill({
          contentType: "image/svg+xml",
          body: '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="192" viewBox="0 0 320 192"><rect width="320" height="192" fill="#eef2f6"/><path d="M160 32 228 66 160 100 92 66Z" fill="#c7d7e8"/><path d="M92 66 160 100 160 164 92 130Z" fill="#6c87a6"/><path d="M160 100 228 66 228 130 160 164Z" fill="#99b0ca"/></svg>',
        }),
      );
      const thumbnailRequests: string[] = [];
      page.on("request", (request) => {
        if (new URL(request.url()).pathname === "/api/v1/files/1/thumbnail")
          thumbnailRequests.push(request.url());
      });
      await page.addInitScript(() => {
        const original = HTMLImageElement.prototype.decode;
        const pending = new Promise<void>((resolve) => {
          window.releaseThumbnailDecode = resolve;
        });
        window.thumbnailReveals = [];
        HTMLImageElement.prototype.decode = async function () {
          await original.call(this);
          await pending;
        };
        document.addEventListener(
          "transitionrun",
          (event) => {
            if (!(event instanceof TransitionEvent) || event.propertyName !== "opacity") return;
            const image = event.target;
            if (!(image instanceof HTMLImageElement) || image.alt !== "Root model") return;
            const transition = image
              .getAnimations()
              .find(
                (animation) =>
                  animation instanceof CSSTransition && animation.transitionProperty === "opacity",
              );
            if (!transition || !(transition.effect instanceof KeyframeEffect)) return;
            const frames = transition.effect.getKeyframes();
            window.thumbnailReveals.push({
              duration: Number(transition.effect.getComputedTiming().duration),
              start: String(frames[0].opacity),
              end: String(frames.at(-1)?.opacity),
              transform: getComputedStyle(image).transform,
            });
            transition.pause();
            transition.currentTime = Number(transition.effect.getComputedTiming().duration) / 2;
            const placeholder = image.nextElementSibling;
            for (const animation of placeholder?.getAnimations() ?? []) {
              animation.pause();
              animation.currentTime = Number(animation.effect?.getComputedTiming().duration) / 2;
            }
          },
          true,
        );
      });
      await page.goto("/");
      const card = page
        .getByRole("heading", { name: "Root model", exact: true })
        .locator("xpath=ancestor::article");
      await card.scrollIntoViewIfNeeded();
      const image = card.getByRole("img", { name: "Root model", exact: true });
      await expect(image).toHaveCSS("opacity", "0");
      const initialBox = await card.boundingBox();
      await page.evaluate(() => window.releaseThumbnailDecode());
      await expect.poll(() => page.evaluate(() => window.thumbnailReveals.length)).toBe(1);
      expect(await page.evaluate(() => window.thumbnailReveals[0])).toEqual({
        duration: scenario.reducedMotion === "reduce" ? 150 : 240,
        start: "0",
        end: "1",
        transform: "none",
      });
      const opacity = Number(await image.evaluate((element) => getComputedStyle(element).opacity));
      expect(opacity).toBeGreaterThan(0);
      expect(opacity).toBeLessThan(1);
      const coverOpacity = await image.evaluate((element) =>
        Number(getComputedStyle(element.nextElementSibling!).opacity),
      );
      expect(coverOpacity).toBeGreaterThan(0);
      expect(coverOpacity).toBeLessThan(1);
      await card.screenshot({ path: info.outputPath("thumbnail-mid-fade.png") });
      await image.evaluate(async (element) => {
        const animations = [
          ...element.getAnimations(),
          ...element.nextElementSibling!.getAnimations(),
        ];
        for (const animation of animations) animation.play();
        await Promise.all(animations.map((animation) => animation.finished));
      });
      await expect(image).toHaveCSS("opacity", "1");
      expect(await card.boundingBox()).toEqual(initialBox);
      expect(thumbnailRequests).toHaveLength(1);
      await card.screenshot({ path: info.outputPath("thumbnail-ready.png") });
    });
  }

  for (const reducedMotion of ["no-preference", "reduce"] as const) {
    test(`Library route results stay still with ${reducedMotion}`, async ({ page }) => {
      await page.emulateMedia({ reducedMotion });
      await page.route("**/api/v1/models/browse?**", (route) =>
        route.fulfill({
          json: {
            items: [
              { kind: "model", model: aModelListItem({ id: 1 }) },
              { kind: "model", model: aModelListItem({ id: 2 }) },
              { kind: "multipart", multipart: aMultipartModel({ id: 3 }) },
            ],
            total: 3,
            next_cursor: null,
            browse_revision: "r1",
            authorization_revision: "a1",
          },
        }),
      );
      // This is a motion contract, so first wait for the fixture's browse acknowledgement.
      const browse = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname === "/api/v1/models/browse" &&
          response.request().method() === "GET",
      );
      await page.goto("/");
      expect((await browse).status()).toBe(200);
      const cards = page.getByRole("main").locator("article");
      await expect(cards.first()).toBeVisible();
      await expect(cards).toHaveCount(3);
      expect(
        await cards.evaluateAll((nodes) =>
          nodes.map((node) => getComputedStyle(node).animationName),
        ),
      ).toEqual(Array(await cards.count()).fill("none"));
    });
  }

  test("an empty folder shows its add-description prompt with its content, not after", async ({
    page,
  }) => {
    await folderWithDescription(page);
    await page.unroute("**/api/v1/collections/lookup?**");
    await page.route("**/api/v1/collections/lookup?**", (route) =>
      route.fulfill({
        json: {
          collection: aCollectionNode({ id: 1, name: "maraio", path: "maraio", has_readme: false }),
          ancestors: [],
        },
      }),
    );
    await page.goto("/");
    await page
      .getByRole("main")
      .getByRole("button", { name: /maraio/ })
      .click();
    const prompt = page.getByRole("button", { name: /Add a description for this collection/ });
    await expect(page.getByRole("heading", { name: "Folder model", exact: true })).toBeVisible();
    await expect(prompt).toBeVisible();
    await expect(prompt).toBeEnabled();
  });

  test("opening a folder by its URL does not animate its content", async ({ page }) => {
    await folderWithDescription(page);
    await page.goto("/?c=maraio");
    await expect(page.getByRole("heading", { name: "Folder model", exact: true })).toBeVisible();
    await expect(
      page.locator(".folder-cards-forward, .folder-cards-back, .folder-cards-jump"),
    ).toHaveCount(0);
    expect(
      (await animationStarts(page)).filter((start) => start.container.includes("folder-cards")),
    ).toEqual([]);
  });

  test("folder content waits for its description before replacing the current view", async ({
    page,
  }) => {
    await folderWithDescription(page);
    const requested = Promise.withResolvers<void>();
    const release = Promise.withResolvers<void>();
    await page.route("**/api/v1/collections/1/readme", async (route) => {
      requested.resolve();
      await release.promise;
      await route.fulfill({ json: { readme: "Folder description" } });
    });
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "All Models" })).toBeVisible();
    await page
      .getByRole("main")
      .getByRole("button", { name: /maraio/ })
      .click();
    await requested.promise;

    try {
      await expect(page.getByRole("heading", { name: "All Models" })).toBeVisible();
    } finally {
      release.resolve();
    }
    await expect(page.getByText("Folder description", { exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Folder model", exact: true })).toBeVisible();
  });

  test("a tall description stays collapsed from its first painted frame", async ({ page }) => {
    await folderWithDescription(page);
    await page.route("**/api/v1/collections/1/readme", (route) =>
      route.fulfill({
        json: {
          readme:
            "## Tall folder description\n\n" + "A paragraph about this folder.\n\n".repeat(30),
        },
      }),
    );
    await page.addInitScript(() => {
      window.descriptionHeights = [];
      function sample() {
        const heading = Array.from(document.querySelectorAll("main h2")).find(
          (node) => node.textContent === "Tall folder description",
        );
        const content = heading?.parentElement?.parentElement;
        if (content) window.descriptionHeights.push(content.getBoundingClientRect().height);
        requestAnimationFrame(sample);
      }
      requestAnimationFrame(sample);
    });
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "All Models" })).toBeVisible();

    await page
      .getByRole("main")
      .getByRole("button", { name: /maraio/ })
      .click();
    await expect(page.getByRole("button", { name: "Show more", exact: true })).toBeVisible();
    await page.evaluate(
      () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve())),
    );

    const heights = await page.evaluate(() => window.descriptionHeights);
    expect(heights.length).toBeGreaterThan(0);
    expect(Math.max(...heights)).toBeLessThanOrEqual(192);
  });

  test("a description image that loads later exposes the expansion control", async ({ page }) => {
    await folderWithDescription(page);
    await page.route("**/api/v1/collections/1/readme", (route) =>
      route.fulfill({
        json: { readme: "![Reference](https://example.test/folder-reference.svg)" },
      }),
    );
    const requested = Promise.withResolvers<void>();
    const release = Promise.withResolvers<void>();
    await page.route("https://example.test/folder-reference.svg", async (route) => {
      requested.resolve();
      await release.promise;
      await route.fulfill({
        contentType: "image/svg+xml",
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="400"><rect width="200" height="400" fill="gray"/></svg>',
      });
    });
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "All Models" })).toBeVisible();
    await page
      .getByRole("main")
      .getByRole("button", { name: /maraio/ })
      .click();
    await requested.promise;
    try {
      await expect(page.getByRole("button", { name: "Show more", exact: true })).toHaveCount(0);
    } finally {
      release.resolve();
    }

    await expect(page.getByRole("button", { name: "Show more", exact: true })).toBeVisible();
  });

  test("an unreadable description does not block opening its folder", async ({ page }) => {
    await folderWithDescription(page);
    await page.route("**/api/v1/collections/1/readme", (route) =>
      route.fulfill({ status: 500, json: { detail: "unavailable" } }),
    );
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "All Models" })).toBeVisible();

    await page
      .getByRole("main")
      .getByRole("button", { name: /maraio/ })
      .click();

    await expect(page.getByRole("heading", { name: "Folder model", exact: true })).toBeVisible();
  });

  test("revisiting a folder keeps cached thumbnails visible", async ({ page }) => {
    const downloads: string[] = [];
    page.on("request", (request) => {
      if (new URL(request.url()).pathname === "/api/v1/files/1/thumbnail")
        downloads.push(request.url());
    });
    await folderWithDescription(page);
    await page.route("**/api/v1/collections/1/readme", (route) =>
      route.fulfill({ json: { readme: "Folder description" } }),
    );
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "All Models" })).toBeVisible();
    await page
      .getByRole("main")
      .getByRole("button", { name: /maraio/ })
      .click();
    const image = page.getByRole("img", { name: "Folder model", exact: true });
    await expect(image).toBeVisible();
    await expect(image.locator("..")).toHaveAttribute("data-library-thumbnail", "ready");
    await page.goBack();
    await expect(page.getByRole("heading", { name: "All Models" })).toBeVisible();
    await page.evaluate(() => {
      window.revisitedThumbnailOpacities = [];
      new MutationObserver(() => {
        document.querySelectorAll("main article img").forEach((image) => {
          if (image.getAttribute("alt") === "Folder model")
            window.revisitedThumbnailOpacities.push(Number(getComputedStyle(image).opacity));
        });
      }).observe(document.querySelector("main")!, {
        subtree: true,
        childList: true,
        attributes: true,
      });
    });

    await page
      .getByRole("main")
      .getByRole("button", { name: /maraio/ })
      .click();
    await expect(image.locator("..")).toHaveAttribute("data-library-thumbnail", "ready");

    const opacities = await page.evaluate(() => window.revisitedThumbnailOpacities);
    expect(opacities.length).toBeGreaterThan(0);
    expect(Math.min(...opacities)).toBeGreaterThanOrEqual(0.9);
    expect(downloads).toHaveLength(1);
  });

  for (const reducedMotion of ["no-preference", "reduce"] as const) {
    test(`opening the collections pane respects ${reducedMotion}`, async ({ page }) => {
      await page.emulateMedia({ reducedMotion });
      await recordPanelMotion(page, "collections-pane");
      await page.goto("/");
      const toggle = page
        .locator("header")
        .getByRole("button", { name: "Toggle sidebar", exact: true });
      const pane = page.locator("#collections-pane");
      await toggle.click();
      await expect(pane).toHaveCSS("width", "0px");
      await page.evaluate(() => {
        window.panelMotions = [];
      });
      await toggle.click();
      await expect(toggle).toHaveAttribute("aria-expanded", "true");
      await expect(page.getByRole("tree", { name: "Collections", exact: true })).toBeVisible();
      const motions = await page.evaluate(() => window.panelMotions);
      expect(motions).toHaveLength(reducedMotion === "reduce" ? 0 : 1);
      if (reducedMotion === "no-preference") {
        expect(motions[0].widths[0]).toBe(0);
        expect(motions[0].widths.at(-1)).toBeGreaterThan(0);
        expect(motions[0].duration).toBe(200);
        expect(
          motions[0].widths.every(
            (width, index, widths) => index === 0 || width >= widths[index - 1],
          ),
        ).toBe(true);
      }
    });
  }

  for (const reducedMotion of ["no-preference", "reduce"] as const) {
    test(`closing the collections pane respects ${reducedMotion}`, async ({ page }) => {
      await page.emulateMedia({ reducedMotion });
      await recordPanelMotion(page, "collections-pane");
      await page.goto("/");
      const toggle = page
        .locator("header")
        .getByRole("button", { name: "Toggle sidebar", exact: true });
      await expect(toggle).toHaveAttribute("aria-expanded", "true");
      await toggle.press("Enter");
      await expect(toggle).toHaveAttribute("aria-expanded", "false");
      await expect(page.locator("#collections-pane")).toHaveCSS("width", "0px");

      const motions = await page.evaluate(() => window.panelMotions);
      expect(motions).toHaveLength(reducedMotion === "reduce" ? 0 : 1);
      if (reducedMotion === "no-preference") {
        const [motion] = motions;
        expect(motion.duration).toBe(200);
        expect(motion.widths[0]).toBeGreaterThan(0);
        expect(motion.widths.at(-1)).toBe(0);
        expect(
          motion.widths.every((width, index, widths) => index === 0 || width <= widths[index - 1]),
        ).toBe(true);
      }
    });
  }

  test("a page that loads with the collections pane open shows it in place", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("tree", { name: "Collections" })).toBeVisible();
    await expect(page.locator("#collections-pane")).not.toHaveClass(/pane-in/);
    expect(
      (await animationStarts(page)).filter((start) => start.target.includes("pane-in")),
    ).toEqual([]);
  });

  for (const direction of ["opening", "closing"] as const) {
    test(`workspace glides as one surface while ${direction} Collections`, async ({ page }) => {
      await recordPanelMotion(page, "collections-pane", true);
      await page.goto("/?c=maraio&type=all");
      const pane = page.locator("#collections-pane");
      const toggle = page.getByRole("button", { name: "Toggle sidebar", exact: true });
      await expect(page.getByRole("link", { name: "skadis_kitchen-roll_screw" })).toBeVisible();
      if (direction === "opening") {
        await toggle.click();
        await pane.evaluate((element) => element.getAnimations()[0].finish());
        await expect(pane).toHaveCSS("width", "0px");
      }

      await toggle.click();
      const sample = (fraction: number) =>
        pane.evaluate((element, progress) => {
          const animation = element.getAnimations()[0];
          animation.currentTime = Number(animation.effect?.getTiming().duration) * progress;
          const workspace = document.querySelector("main")!;
          const left = workspace.getBoundingClientRect().left;
          const cards = [...workspace.querySelectorAll("[data-library-key]")].map((card) => {
            const { x, y, width, height } = card.getBoundingClientRect();
            return { key: card.getAttribute("data-library-key"), x: x - left, y, width, height };
          });
          return { paneWidth: element.getBoundingClientRect().width, left, cards };
        }, fraction);
      const early = await sample(0.25);
      const late = await sample(0.75);
      expect(early.paneWidth).not.toBe(late.paneWidth);
      expect(late.left - early.left).toBeCloseTo(late.paneWidth - early.paneWidth);
      expect(early.cards.length).toBeGreaterThan(0);
      expect(late.cards).toEqual(early.cards);
      await pane.evaluate((element) => element.getAnimations()[0].finish());
      await expect(toggle).toHaveAttribute("aria-expanded", String(direction === "opening"));
      const workspace = page.getByRole("main");
      await expect(pane).toHaveCSS("width", direction === "opening" ? /^[1-9]\d+px$/ : "0px");
      await expect(workspace).toHaveJSProperty("scrollTop", 0);
    });
  }

  test("collection strip keeps the same card dimensions at both sidebar positions", async ({
    page,
  }) => {
    // This width crosses a collection column breakpoint when the sidebar opens.
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.route("**/api/v1/collections/lookup?**", (route) => {
      const query = new URL(route.request().url()).searchParams;
      if (query.get("path") !== "maraio" && query.get("id") !== "1") return route.fallback();
      return route.fulfill({
        json: {
          collection: aCollectionNode({ id: 1, name: "maraio", path: "maraio", child_count: 3 }),
          ancestors: [],
        },
      });
    });
    await page.route("**/api/v1/collections/children?**", (route) => {
      const parent = new URL(route.request().url()).searchParams.get("parent_id");
      if (parent !== "1") return route.fallback();
      return route.fulfill({
        json: {
          items: ["Garden", "Backpack wall mount", "Bag holder"].map((name, index) =>
            aCollectionNode({ id: index + 2, name, path: `maraio/folder-${index}`, parent_id: 1 }),
          ),
          next_cursor: null,
        },
      });
    });
    await page.goto("/?c=maraio&type=all");
    const workspace = page.getByRole("main");
    const card = workspace.getByRole("button", { name: /^Backpack wall mount/ });
    const toggle = page.getByRole("button", { name: "Toggle sidebar", exact: true });
    const pane = page.locator("#collections-pane");
    await expect(card).toBeVisible();
    const geometry = () =>
      card.evaluate((button) => {
        const { x, y, width, height } = button.getBoundingClientRect();
        return { x: x - button.closest("main")!.getBoundingClientRect().x, y, width, height };
      });
    const initial = await geometry();
    const initiallyOpen = (await toggle.getAttribute("aria-expanded")) === "true";
    for (const open of [!initiallyOpen, initiallyOpen]) {
      await toggle.click();
      await expect(pane).toHaveCSS("width", open ? /^[1-9]\d+px$/ : "0px");
      await expect(page.locator("html")).not.toHaveAttribute("data-pane-moving");
      expect(await geometry()).toEqual(initial);
    }
  });

  for (const [reducedMotion, moves] of [
    ["no-preference", true],
    ["reduce", false],
  ] as const) {
    test(`the details panel leaves toward its edge only with ${reducedMotion}`, async ({
      page,
    }) => {
      await page.emulateMedia({ reducedMotion });
      await recordPanelMotion(page, "model-drawer");
      await page.goto("/?c=maraio&type=all");
      await page
        .getByRole("main")
        .getByRole("link", { name: "skadis_kitchen-roll_screw", exact: true })
        .click();
      const drawer = page.locator("#model-drawer");
      await expect(drawer).toHaveAttribute("data-state", "open");

      await expect(drawer).toHaveCSS("--inspector-displacement", "0");
      await page.evaluate(() => {
        window.panelMotions = [];
      });
      await drawer.getByRole("button", { name: "Close details", exact: true }).click();

      await expect(drawer).toBeHidden();
      const motions = await page.evaluate(() => window.panelMotions);
      expect(motions.length > 0).toBe(moves);
      if (moves) {
        expect(motions[0].transforms[0]).not.toBe(motions[0].transforms.at(-1));
        expect(motions[0].duration).toBe(200);
      }
    });
  }

  test("header and recent-folder menus stay above adjacent vault surfaces", async ({ page }) => {
    await page.addInitScript(() =>
      localStorage.setItem("ps-recent-folders", JSON.stringify(["maraio"])),
    );
    await page.goto("/");

    const headerZ = await page.locator("header").evaluate((element) => {
      const layer = getComputedStyle(element).zIndex;
      return layer === "auto" ? 0 : Number(layer);
    });
    const stickyZ = await page.getByRole("main").evaluate((element) => {
      const layer = getComputedStyle(element).zIndex;
      return layer === "auto" ? 0 : Number(layer);
    });
    expect(headerZ).toBeGreaterThan(stickyZ);

    await page.getByRole("button", { name: "Recent" }).click();
    const menuBox = await page.getByRole("menu").boundingBox();
    const sidebarBox = await page.locator("aside").boundingBox();
    expect(menuBox).not.toBeNull();
    expect(sidebarBox).not.toBeNull();
    expect(menuBox!.x).toBeGreaterThanOrEqual(sidebarBox!.x + sidebarBox!.width);
  });
});
