/** Protected image ownership preserves visible URLs and bounds authorized work. */
import { expect, test } from "@playwright/test";
import { aModelListItem } from "../../src/test-support/factories";
import { useMockApi } from "./_setup";

useMockApi();

test.describe("protected asset admission", () => {
  test("admits visible thumbnails before distant cards", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.addInitScript(() => localStorage.setItem("ps-vault-library-view", "all"));
    const items = Array.from({ length: 24 }, (_, index) =>
      aModelListItem({
        id: index + 1,
        name: `Thumbnail ${index + 1}`,
        thumbnail_url: `/api/v1/files/${index + 1}/thumbnail`,
      }),
    );
    await page.route("**/api/v1/models/page?**", (route) =>
      route.fulfill({ json: { items, total: items.length, next_cursor: null } }),
    );
    await page.route("**/api/v1/models/browse?**", (route) =>
      route.fulfill({
        json: {
          items: items.map((model) => ({ kind: "model", model })),
          total: items.length,
          next_cursor: null,
          browse_revision: "r1",
          authorization_revision: "a1",
        },
      }),
    );
    let held = true;
    let active = 0;
    let maximum = 0;
    const requested: number[] = [];
    const releases: (() => void)[] = [];
    const pixel = Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/lx2h4wAAAABJRU5ErkJggg==",
      "base64",
    );
    await page.route(/\/api\/v1\/files\/\d+\/thumbnail$/, async (route) => {
      requested.push(Number(new URL(route.request().url()).pathname.split("/")[4]));
      active += 1;
      maximum = Math.max(maximum, active);
      if (held) await new Promise<void>((resolve) => releases.push(resolve));
      active -= 1;
      await route.fulfill({ contentType: "image/png", body: pixel });
    });
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Thumbnail 1", exact: true })).toBeVisible();
    await expect.poll(() => requested.length).toBe(2);
    expect(requested).not.toContain(24);
    held = false;
    for (const release of releases) release();
    await page.waitForFunction(() => {
      const visible = [
        ...document.querySelectorAll("main article [data-library-thumbnail]"),
      ].filter((frame) => {
        const bounds = frame.getBoundingClientRect();
        return bounds.width > 0 && bounds.bottom > 0 && bounds.top < innerHeight;
      });
      return (
        visible.length > 0 &&
        visible.every((frame) => {
          const image = frame.querySelector("img");
          return (
            frame.getAttribute("data-library-thumbnail") === "ready" &&
            image?.complete &&
            image.naturalWidth > 0
          );
        })
      );
    });
    expect(maximum).toBeLessThanOrEqual(2);
    expect(requested).not.toContain(24);
    await page.getByRole("heading", { name: "Thumbnail 24", exact: true }).scrollIntoViewIfNeeded();
    await expect.poll(() => requested.includes(24)).toBe(true);
    await expect(page.getByAltText("Thumbnail 24", { exact: true })).toBeVisible();
    expect(maximum).toBeLessThanOrEqual(2);
  });
});
