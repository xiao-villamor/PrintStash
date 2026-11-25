/** Folder selection keeps the displayed collection tree mounted throughout navigation. */
import { expect, test } from "@playwright/test";
import { useMockApi } from "./_setup";

useMockApi();

test.describe("Collection tree", () => {
  test("opens the library root from the sidebar", async ({ page }) => {
    await page.goto("/?c=maraio");
    const root = page.getByRole("tree").getByRole("button", { name: "All Models", exact: true });
    await expect(page.getByRole("main").getByRole("heading", { name: "maraio" })).toBeVisible();
    await root.click();
    await expect(page.getByRole("main").getByRole("heading", { name: "All Models" })).toBeVisible();
    await expect(page).toHaveURL(
      (url) => !url.searchParams.has("c") && !url.searchParams.has("cid"),
    );
  });

  test("keeps displayed rows mounted when selecting a folder", async ({ page }) => {
    const pending = Promise.withResolvers<void>();
    let selecting = false;
    await page.route("**/api/v1/outliner/**", async (route) => {
      if (selecting) await pending.promise;
      await route.continue();
    });
    await page.goto("/");
    const folder = page.getByRole("treeitem", { name: "maraio", exact: true });
    await expect(folder).toBeVisible();
    const continuity = await folder.evaluateHandle((row) => {
      const state = { removals: 0 };
      const observer = new MutationObserver((records) => {
        for (const record of records) {
          for (const removed of record.removedNodes) {
            if (removed === row || removed.contains(row)) state.removals += 1;
          }
        }
      });
      observer.observe(document.body, { childList: true, subtree: true });
      return { state, observer };
    });
    selecting = true;

    try {
      await folder.click();
      await expect(page).toHaveURL(/c=maraio/);
      await expect(folder).toBeVisible();
      expect(await continuity.evaluate(({ state }) => state.removals)).toBe(0);
    } finally {
      pending.resolve();
      await continuity.evaluate(({ observer }) => observer.disconnect());
    }
  });
});
