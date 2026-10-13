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
import { aCollectionNode, aModelListItem, aMultipartModel } from "../../src/test-support/factories";

useMockApi();

declare global {
  interface Window {
    revisitedThumbnailOpacities: number[];
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

test.describe("motion and layering", () => {
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
      await page.goto("/");
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
  });

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
