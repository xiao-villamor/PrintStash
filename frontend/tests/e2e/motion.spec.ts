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
import { animationStarts, forgetAnimationStarts, recordAnimationStarts } from "./_animations";
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
  }
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

/** The animations folder entrances started on the rows of a container class. */
async function entrancesUnder(page: Page, container: string) {
  return (await animationStarts(page))
    .filter((start) => start.container.split(" ").includes(container))
    .map((start) => start.name);
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
            transition.currentTime = 150;
            const placeholder = image.nextElementSibling;
            for (const animation of placeholder?.getAnimations() ?? []) {
              animation.pause();
              animation.currentTime = 150;
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
        duration: 300,
        start: "0",
        end: "1",
        transform: "none",
      });
      const opacity = Number(await image.evaluate((element) => getComputedStyle(element).opacity));
      expect(opacity).toBeGreaterThan(0);
      expect(opacity).toBeLessThan(1);
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

  for (const [reducedMotion, animation] of [
    ["no-preference", "folder-in-forward"],
    ["reduce", "fade-in"],
  ] as const) {
    test(`drilling into a folder settles its content in from the right with ${reducedMotion}`, async ({
      page,
    }) => {
      await page.emulateMedia({ reducedMotion });
      await folderWithDescription(page);
      await page.goto("/");
      await expect(page.getByRole("heading", { name: "All Models" })).toBeVisible();
      await expect(page.locator(".folder-cards-forward")).toHaveCount(0);

      await page
        .getByRole("main")
        .getByRole("button", { name: /maraio/ })
        .click();
      await expect(page.getByRole("heading", { name: "Folder model", exact: true })).toBeVisible();

      await expect.poll(() => entrancesUnder(page, "folder-cards-forward")).toContain(animation);
    });
  }

  test("the card entrance is large enough to be noticed on cached content", async ({ page }) => {
    await folderWithDescription(page);
    await page.goto("/");
    await page
      .getByRole("main")
      .getByRole("button", { name: /maraio/ })
      .click();
    await expect
      .poll(() => entrancesUnder(page, "folder-cards-forward"))
      .toContain("folder-in-forward");

    const [start] = (await animationStarts(page)).filter(
      (entry) => entry.name === "folder-in-forward",
    );
    const keyframes = await page.evaluate(() =>
      Array.from(document.styleSheets)
        .flatMap((sheet) => Array.from(sheet.cssRules))
        .filter(
          (rule): rule is CSSKeyframesRule =>
            rule instanceof CSSKeyframesRule && rule.name === "folder-in-forward",
        )
        .map((rule) => rule.cssText)
        .join(" "),
    );
    expect(start.duration).toBe("0.3s");
    expect(keyframes).toContain("translateX(24px)");
  });

  test("a folder whose cards are already cached still animates them in on a revisit", async ({
    page,
  }) => {
    await folderWithDescription(page);
    await page.goto("/");
    const folder = page.getByRole("main").getByRole("button", { name: /maraio/ });
    await folder.click();
    await expect(page.getByRole("heading", { name: "Folder model", exact: true })).toBeVisible();
    await page.getByRole("main").getByRole("button", { name: "All Models" }).first().click();
    await expect(page.getByRole("heading", { name: "Root model", exact: true })).toBeVisible();
    await forgetAnimationStarts(page);

    await folder.click();
    await expect(page.getByRole("heading", { name: "Folder model", exact: true })).toBeVisible();

    await expect
      .poll(() => entrancesUnder(page, "folder-cards-forward"))
      .toContain("folder-in-forward");
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

  test("climbing back out of a folder settles its parent in from the left", async ({ page }) => {
    await folderWithDescription(page);
    await page.goto("/");
    await page
      .getByRole("main")
      .getByRole("button", { name: /maraio/ })
      .click();
    await expect(page.getByRole("heading", { name: "Folder model", exact: true })).toBeVisible();

    await page.getByRole("main").getByRole("button", { name: "All Models" }).first().click();
    await expect(page.getByRole("heading", { name: "Root model", exact: true })).toBeVisible();

    await expect.poll(() => entrancesUnder(page, "folder-cards-back")).toContain("folder-in-back");
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

  test("a reflow after a folder entrance does not play the entrance again", async ({ page }) => {
    await folderWithDescription(page);
    await page.goto("/");
    await page
      .getByRole("main")
      .getByRole("button", { name: /maraio/ })
      .click();
    await expect
      .poll(() => entrancesUnder(page, "folder-cards-forward"))
      .toContain("folder-in-forward");
    await expect(page.locator(".folder-cards-forward")).toHaveCount(0);
    await forgetAnimationStarts(page);

    await page
      .locator("header")
      .getByRole("button", { name: "Toggle sidebar", exact: true })
      .click();

    await expect
      .poll(async () => (await animationStarts(page)).map((start) => start.name))
      .toContain("results-settle");
    expect(
      (await animationStarts(page)).filter((start) => start.name.startsWith("folder-in")),
    ).toEqual([]);
  });

  test("filtering the open folder settles its results in place", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "All Models" })).toBeVisible();
    await forgetAnimationStarts(page);

    await page
      .getByRole("radiogroup", { name: "Filter models by status" })
      .getByRole("radio", { name: /Needs test/ })
      .click();

    await expect
      .poll(async () =>
        (await animationStarts(page))
          .filter((start) => start.target.split(" ").includes("library-items"))
          .map((start) => start.name),
      )
      .toEqual(["results-settle"]);
  });

  test("switching to the list settles it in place", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "All Models" })).toBeVisible();
    await forgetAnimationStarts(page);

    await page.getByRole("button", { name: "List View", exact: true }).click();

    await expect
      .poll(async () =>
        (await animationStarts(page))
          .filter((start) => start.target.split(" ").includes("results-settle"))
          .map((start) => start.name),
      )
      .toEqual(["results-settle"]);
  });

  for (const [reducedMotion, animation] of [
    ["no-preference", "pane-in"],
    ["reduce", "fade-in"],
  ] as const) {
    test(`opening the collections pane eases it in with ${reducedMotion}`, async ({ page }) => {
      await page.emulateMedia({ reducedMotion });
      await page.goto("/");
      const toggle = page
        .locator("header")
        .getByRole("button", { name: "Toggle sidebar", exact: true });
      await toggle.click();
      await expect(toggle).toHaveAttribute("aria-expanded", "false");
      await forgetAnimationStarts(page);

      await toggle.click();

      await expect
        .poll(async () =>
          (await animationStarts(page))
            .filter((start) => start.target.split(" ").includes("pane-in"))
            .map((start) => start.name),
        )
        .toEqual([animation]);
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

  for (const [reducedMotion, moves] of [
    ["no-preference", true],
    ["reduce", false],
  ] as const) {
    test(`the details panel leaves toward its edge only with ${reducedMotion}`, async ({
      page,
    }) => {
      await page.emulateMedia({ reducedMotion });
      await page.goto("/?c=maraio&type=all");
      await page
        .getByRole("main")
        .getByRole("link", { name: "skadis_kitchen-roll_screw", exact: true })
        .click();
      const drawer = page.locator("#model-drawer");
      await expect(drawer).toHaveAttribute("data-state", "open");

      await drawer.getByRole("button", { name: "Close details", exact: true }).click();

      await expect(drawer).toBeHidden();
      const transform = await drawer.evaluate((node) => getComputedStyle(node).transform);
      expect(transform !== "none").toBe(moves);
    });
  }

  test("header and recent-folder menus stay above adjacent vault surfaces", async ({ page }) => {
    await page.addInitScript(() =>
      localStorage.setItem("ps-recent-folders", JSON.stringify(["maraio"])),
    );
    await page.goto("/");

    const headerZ = await page
      .locator("header")
      .evaluate((element) => Number(getComputedStyle(element).zIndex));
    const stickyZ = await page
      .locator(".sticky.top-0")
      .evaluate((element) => Number(getComputedStyle(element).zIndex));
    expect(headerZ).toBeGreaterThan(stickyZ);

    await page.getByRole("button", { name: "Recent" }).click();
    const menuBox = await page.getByRole("menu").boundingBox();
    const sidebarBox = await page.locator("aside").boundingBox();
    expect(menuBox).not.toBeNull();
    expect(sidebarBox).not.toBeNull();
    expect(menuBox!.x).toBeGreaterThanOrEqual(sidebarBox!.x + sidebarBox!.width);
  });
});
