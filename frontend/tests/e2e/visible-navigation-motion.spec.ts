/** Observe rendered opacity on real animation frames, without pausing or replacing animations. */
import { expect, test, type Page } from "@playwright/test";
import { aCollectionNode, aModelListItem } from "../../src/test-support/factories";
import { useMockApi } from "./_setup";

useMockApi();
test.use({ viewport: { width: 1440, height: 900 } });

declare global {
  interface Window {
    revealFrames: { surface: string; opacity: number; transform: string }[];
  }
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.revealFrames = [];
    const sample = () => {
      for (const element of document.querySelectorAll(
        ".library-content-reveal,.workbench-main-content,.workbench-section-content",
      )) {
        const style = getComputedStyle(element);
        window.revealFrames.push({
          surface: element.classList[0],
          opacity: Number(style.opacity),
          transform: style.transform,
        });
      }
      requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  });
});

async function visibleHandoff(page: Page, surface: string, action: () => Promise<void>) {
  await page.evaluate(() => {
    window.revealFrames = [];
  });
  await action();
  // A nearly opaque single frame is not a perceptible entrance. Assert the
  // actual rendered starting contrast and completed state, not animate().
  // A busy main thread may miss intermediate rAF samples while the compositor
  // keeps fading; the separate interruption tests inspect that interpolation.
  await expect
    .poll(() =>
      page.evaluate(
        (name) =>
          window.revealFrames.some((frame) => frame.surface === name && frame.opacity <= 0.6),
        surface,
      ),
    )
    .toBe(true);
  await expect(page.locator(`.${surface}`)).toHaveCSS("opacity", "1");
  const frames = await page.evaluate(
    (name) => window.revealFrames.filter((frame) => frame.surface === name),
    surface,
  );
  expect(frames.every((frame) => frame.transform === "none")).toBe(true);
}

test("cached collection visits have a visible stationary handoff every time", async ({ page }) => {
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
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Root model", exact: true })).toBeVisible();
  await visibleHandoff(page, "library-content-reveal", () =>
    page
      .getByRole("main")
      .getByRole("button", { name: /maraio/ })
      .click(),
  );
  for (let visit = 0; visit < 2; visit++) {
    await visibleHandoff(page, "library-content-reveal", async () => {
      await page.goBack();
    });
    await expect(page.getByRole("heading", { name: "Root model", exact: true })).toBeVisible();
    await visibleHandoff(page, "library-content-reveal", async () => {
      await page.goForward();
    });
    await expect(page.getByRole("heading", { name: "Folder model", exact: true })).toBeVisible();
  }
});

test("Search, Pending and Favorites retain visible transitions on repeated rail visits", async ({
  page,
}) => {
  await page.goto("/settings");
  await expect(page.getByRole("navigation", { name: "Settings sections" })).toBeVisible();
  const rail = page.getByRole("navigation", { name: "Navigation", exact: true });
  for (let visit = 0; visit < 2; visit++) {
    await visibleHandoff(page, "workbench-main-content", () =>
      rail.getByRole("link", { name: "Search", exact: true }).click(),
    );
    await expect(page).toHaveURL(/\/search/);
    await visibleHandoff(page, "workbench-section-content", () =>
      rail.getByRole("link", { name: "Pending", exact: true }).click(),
    );
    await visibleHandoff(page, "workbench-main-content", () =>
      rail.getByRole("link", { name: "Favorites", exact: true }).click(),
    );
    await expect(page.getByRole("heading", { name: "Favorites & saved views" })).toBeVisible();
    await visibleHandoff(page, "workbench-main-content", () =>
      rail.getByRole("link", { name: "Collections", exact: true }).click(),
    );
    await expect(page.getByRole("heading", { name: "All Models", exact: true })).toBeVisible();
  }
});

test("the Pending page reveals on cached mobile history visits", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/search");
  await visibleHandoff(page, "workbench-main-content", () =>
    page.getByRole("link", { name: "Pending", exact: true }).click(),
  );
  await expect(page).toHaveURL(/\/inbox/);
  await visibleHandoff(page, "workbench-main-content", async () => {
    await page.goBack();
  });
  await visibleHandoff(page, "workbench-main-content", async () => {
    await page.goForward();
  });
});
