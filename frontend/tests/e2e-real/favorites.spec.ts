/** A confirmed Favorites removal preserves the reader's visible neighboring card. */
import { execFileSync } from "node:child_process";
import path from "node:path";
import { test, expect } from "./helpers";
import { scrollToLibraryEntry } from "../e2e/_scroll";

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
      models.sort((a, b) => a.name.localeCompare(b.name));
      expect(models).toHaveLength(24);
      for (const model of models) {
        const starred = await page.request.put(`/api/v1/models/${model.id}/star`);
        expect(starred.ok(), await starred.text()).toBe(true);
      }
      await page.goto(`/?c=${encodeURIComponent(seeded.folder.path)}&favorites=true&sort=name-asc`);
      const targetHref = `/models/${models[9].id}`;
      const neighborHref = `/models/${models[10].id}`;
      const targetLink = page.locator(`main [data-library-entry="${targetHref}"]`);
      const target = targetLink.locator("xpath=ancestor::article");
      await scrollToLibraryEntry(page, targetLink);
      // The removed card is the first visible anchor in the virtual scroll surface.
      await targetLink.evaluate((node) => {
        const scroll = node.closest("main");
        if (!scroll) throw new Error("Missing Library scroll container");
        scroll.scrollTop += node.getBoundingClientRect().top - scroll.getBoundingClientRect().top;
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
      expect(await page.getByRole("main").evaluate((main) => main.scrollTop)).toBeGreaterThan(0);
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
