/** A confirmed Favorites removal preserves the reader's visible neighboring card. */
import { execFileSync } from "node:child_process";
import path from "node:path";
import { test, expect } from "./helpers";

test.describe("confirmed favorite removal", () => {
  test("preserves the reading position after a confirmed favorite removal", async ({ page }) => {
    await page.setViewportSize({ width: 600, height: 1000 });
    const backend = path.resolve("../backend");
    const dataRoot = process.env.PLAYWRIGHT_REAL_DATA_DIR ?? path.resolve("tests/e2e-real/.data");
    const seeded = JSON.parse(
      execFileSync(
        path.join(backend, ".venv/bin/python"),
        ["-m", "tests.fakes.outliner_library", dataRoot],
        { cwd: backend, encoding: "utf8" },
      ),
    );
    const release = Promise.withResolvers<void>();
    let committed = false;
    try {
      const response = await page.request.get(
        `/api/v1/models?collection=${encodeURIComponent(seeded.folder.path)}&limit=24`,
      );
      expect(response.ok()).toBe(true);
      const models: { id: number; name: string }[] = await response.json();
      expect(models).toHaveLength(24);
      for (const model of models) {
        const starred = await page.request.put(`/api/v1/models/${model.id}/star`);
        expect(starred.ok(), await starred.text()).toBe(true);
      }
      await page.goto(`/?c=${encodeURIComponent(seeded.folder.path)}&favorites=true`);
      const cards = page.locator("main article");
      await expect(cards).toHaveCount(24);
      const target = cards.nth(9);
      const neighbor = cards.nth(10);
      const targetLink = target.locator("[data-library-entry]");
      const neighborLink = neighbor.locator("[data-library-entry]");
      const targetHref = await targetLink.getAttribute("data-library-entry");
      const neighborHref = await neighborLink.getAttribute("data-library-entry");
      if (!targetHref || !neighborHref) throw new Error("Missing favorite identity");
      // The removed card is the first visible reading anchor, not an arbitrary
      // card below an unaffected anchor (whose neighbors should normally reflow).
      await targetLink.evaluate((node) => {
        const main = node.closest("main");
        if (!main) throw new Error("Missing Library scroll container");
        main.scrollTop += node.getBoundingClientRect().top - main.getBoundingClientRect().top;
      });
      const surviving = page.locator(`main [data-library-entry="${neighborHref}"]`);
      const beforeClick = await surviving.boundingBox();
      if (!beforeClick) throw new Error("Missing reading neighbor");
      await page.route(`**/api/v1${targetHref}/star`, async (route) => {
        const saved = await route.fetch();
        expect(saved.ok()).toBe(true);
        committed = true;
        await release.promise;
        await route.fulfill({ response: saved });
      });
      const removed = page.locator(`main [data-library-entry="${targetHref}"]`);
      await target.getByRole("button", { name: /Remove .* from favorites/ }).click();
      await expect.poll(() => committed).toBe(true);
      await expect(removed).toHaveCount(1);
      const before = await surviving.boundingBox();
      if (!before) throw new Error("Missing pending reading neighbor");
      release.resolve();
      await expect(removed).toHaveCount(0);
      await test.info().attach("favorite-reading-positions", {
        contentType: "application/json",
        body: JSON.stringify({
          beforeClick,
          beforeConfirmation: before,
          afterConfirmation: await surviving.boundingBox(),
        }),
      });
      await expect
        .poll(async () => {
          const after = await surviving.boundingBox();
          return after ? Math.abs(after.y - before.y) : Infinity;
        })
        .toBeLessThanOrEqual(2);
      expect(await page.locator("main").evaluate((element) => element.scrollTop)).toBeGreaterThan(
        0,
      );
    } finally {
      release.resolve();
      expect(
        (
          await page.request.delete(`/api/v1/collections/${seeded.root.id}?recursive=true`)
        ).status(),
      ).toBe(204);
    }
  });
});
