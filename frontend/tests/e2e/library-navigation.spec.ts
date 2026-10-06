/** Library detail return belongs to the exact displayed history entry. */
import { expect, test } from "@playwright/test";
import { useMockApi } from "./_setup";
import { aModel, aModelListItem, aMultipartModel } from "../../src/test-support/factories";

useMockApi();

for (const kind of ["Model", "Multipart set"] as const) {
  test(`restores the originating Library history entry for a ${kind}`, async ({ page }) => {
    const model = aModelListItem({ id: 1, name: "skadis_kitchen-roll_screw", starred: true });
    const multipart = aMultipartModel({ id: 90, name: "Navigation set", starred: true });
    await page.route("**/api/v1/models/browse?**", (route) =>
      route.fulfill({
        json: {
          items: kind === "Model" ? [{ kind: "model", model }] : [{ kind: "multipart", multipart }],
          total: 1,
          next_cursor: null,
          browse_revision: "r1",
          authorization_revision: "a1",
        },
      }),
    );
    await page.route("**/api/v1/multipart-models/90", (route) =>
      route.fulfill({ json: multipart }),
    );
    await page.goto("/?c=maraio&type=all&sort=name-asc&favorites=true");
    const label = kind === "Model" ? model.name : multipart.name;
    const link = page
      .getByRole("main")
      .getByRole("link", { name: new RegExp(label) })
      .first();
    await expect(link).toBeVisible();
    const origin = page.url();
    const key = await page.evaluate(() => window.history.state.key);
    await link.click();
    await expect(page.getByRole("heading", { name: label, exact: true })).toBeVisible();
    await page
      .getByRole("link", { name: kind === "Model" ? "Back" : "Multipart sets", exact: true })
      .click();

    await expect(page).toHaveURL(origin);
    await expect(link).toBeVisible();
    expect(await page.evaluate(() => window.history.state.key)).toBe(key);
    await page.goForward();
    await expect(page.getByRole("heading", { name: label, exact: true })).toBeVisible();
  });
}

for (const { layout, viewport } of [
  { layout: "grid", viewport: "desktop" },
  { layout: "list", viewport: "desktop" },
  { layout: "grid", viewport: "mobile" },
  { layout: "list", viewport: "mobile" },
] as const) {
  test(`restores the nested ${layout} reading position on ${viewport}`, async ({ page }) => {
    if (viewport === "mobile") await page.setViewportSize({ width: 390, height: 844 });
    await page.addInitScript((value) => localStorage.setItem("ps-vault-view", value), layout);
    const models = Array.from({ length: 40 }, (_, index) =>
      aModelListItem({ id: index + 100, name: `Reading position ${index}` }),
    );
    models[20] = aModelListItem({ id: 1, name: "skadis_kitchen-roll_screw" });
    await page.route("**/api/v1/models/browse?**", (route) =>
      route.fulfill({
        json: {
          items: models.map((model) => ({ kind: "model", model })),
          total: models.length,
          next_cursor: null,
          browse_revision: "r1",
          authorization_revision: "a1",
        },
      }),
    );
    await page.goto("/?c=maraio&type=all&sort=name-asc");
    const link = page
      .getByRole("main")
      .getByRole("link", { name: /skadis_kitchen-roll_screw/ })
      .first();
    await link.scrollIntoViewIfNeeded();
    const before = await link.evaluate((element) => ({
      top: element.getBoundingClientRect().top,
      offsets: Array.from(document.querySelectorAll("main, main .overflow-y-auto")).map(
        (node) => node.scrollTop,
      ),
    }));
    expect(Math.max(...before.offsets)).toBeGreaterThan(100);
    const box = await link.boundingBox();
    if (!box) throw new Error("Reading anchor has no visible box");
    await page.mouse.click(box.x + 20, box.y + 20);
    await expect(
      page.getByRole("heading", { name: "skadis_kitchen-roll_screw", exact: true }),
    ).toBeVisible();
    await page.getByRole("link", { name: "Back", exact: true }).click();
    await expect(link).toBeVisible();
    await expect
      .poll(async () => link.evaluate((element) => element.getBoundingClientRect().top))
      .toBeCloseTo(before.top, 0);
    const offsets = await page
      .locator("main, main .overflow-y-auto")
      .evaluateAll((nodes) => nodes.map((node) => node.scrollTop));
    expect(offsets).toEqual(before.offsets);
    await page.goForward();
    await expect(
      page.getByRole("heading", { name: "skadis_kitchen-roll_screw", exact: true }),
    ).toBeVisible();
    await page.goBack();
    await expect
      .poll(async () => link.evaluate((element) => element.getBoundingClientRect().top))
      .toBeCloseTo(before.top, 0);
  });
}

for (const recovery of ["available", "removed", "stale"] as const) {
  test(`bounds history reconstruction when its anchor is ${recovery}`, async ({ page }) => {
    await page.clock.install();
    const first = Array.from({ length: 20 }, (_, index) =>
      aModelListItem({ id: index + 100, name: `First page ${index}` }),
    );
    const second = Array.from({ length: 20 }, (_, index) =>
      aModelListItem({ id: index + 200, name: `Second page ${index}` }),
    );
    second[10] = aModelListItem({ id: 1, name: "skadis_kitchen-roll_screw" });
    let returning = false;
    const restored: (string | null)[] = [];
    await page.route("**/api/v1/models/browse?**", (route) => {
      const cursor = new URL(route.request().url()).searchParams.get("cursor");
      if (returning) restored.push(cursor);
      if (returning && cursor && recovery === "stale")
        return route.fulfill({ status: 409, json: { detail: "browse_refresh_required" } });
      const models = cursor ? second : first;
      return route.fulfill({
        json: {
          items: models
            .filter((model) => !(returning && recovery === "removed" && model.id === 1))
            .map((model) => ({ kind: "model", model })),
          total: 60,
          next_cursor: cursor ? "unvisited-third-page" : "second",
          browse_revision: "r1",
          authorization_revision: "a1",
        },
      });
    });
    await page.goto("/?c=maraio&type=all&sort=name-asc");
    await page.getByRole("button", { name: "Load more", exact: true }).click();
    const link = page
      .getByRole("main")
      .getByRole("link", { name: /skadis_kitchen-roll_screw/ })
      .first();
    await link.scrollIntoViewIfNeeded();
    const before = await link.evaluate((node) => node.getBoundingClientRect().top);
    const box = await link.boundingBox();
    if (!box) throw new Error("Reading anchor has no visible box");
    await page.mouse.click(box.x + 20, box.y + 20);
    await expect(
      page.getByRole("heading", { name: "skadis_kitchen-roll_screw", exact: true }),
    ).toBeVisible();
    // Exercise actual Query GC rather than reaching into the app's cache from a test hook.
    await page.clock.fastForward(301_000);
    returning = true;
    await page.getByRole("link", { name: "Back", exact: true }).click();
    if (recovery === "available") {
      await expect(link).toBeVisible();
      await expect
        .poll(async () => link.evaluate((node) => node.getBoundingClientRect().top))
        .toBeCloseTo(before, 0);
    } else {
      await expect(
        page.getByText(
          "Your previous reading position could not be restored. Showing the start of this view.",
        ),
      ).toBeVisible();
      expect(await page.getByRole("main").evaluate((node) => node.scrollTop)).toBe(0);
    }
    expect(restored.filter((cursor) => cursor !== null)).toEqual(["second"]);
    expect(restored).not.toContain("unvisited-third-page");
  });
}

for (const { layout, kind } of [
  { layout: "grid", kind: "model" },
  { layout: "list", kind: "model" },
  { layout: "grid", kind: "multipart" },
] as const) {
  test(`preserves the ${layout} reading anchor after removing a confirmed favorite${kind === "multipart" ? " Multipart" : ""}`, async ({
    page,
  }) => {
    await page.addInitScript((value) => localStorage.setItem("ps-vault-view", value), layout);
    const models = Array.from({ length: 40 }, (_, index) =>
      aModelListItem({ id: index + 100, name: `Favorite ${index}`, starred: true }),
    );
    await page.route("**/api/v1/models/browse?**", (route) =>
      route.fulfill({
        json: {
          items: models.map((model) =>
            kind === "model"
              ? { kind: "model", model }
              : {
                  kind: "multipart",
                  multipart: aMultipartModel({ id: model.id, name: model.name, starred: true }),
                },
          ),
          total: models.length,
          next_cursor: null,
          browse_revision: "r1",
          authorization_revision: "a1",
        },
      }),
    );
    await page.route("**/api/v1/models/120", (route) =>
      route.fulfill({ json: aModel({ id: 120, name: "Favorite 20", starred: true }) }),
    );
    const acknowledgement = Promise.withResolvers<void>();
    await page.route(
      `**/api/v1/${kind === "model" ? "models" : "multipart-models"}/120/star`,
      async (route) => {
        await acknowledgement.promise;
        await route.fulfill({
          json:
            kind === "model"
              ? { model_id: 120, starred: false }
              : { multipart_model_id: 120, starred: false },
        });
      },
    );
    await page.goto("/?c=maraio&type=all&sort=name-asc&favorites=true");
    const target = page
      .getByRole("main")
      .getByRole("link", { name: /Favorite 20/ })
      .first();
    const remove =
      layout === "grid"
        ? page.getByRole("button", { name: "Remove Favorite 20 from favorites", exact: true })
        : target;
    await remove.scrollIntoViewIfNeeded();
    const survivor = page
      .getByRole("main")
      .getByRole("link", { name: /Favorite 21/ })
      .first();
    await expect(survivor).toBeVisible();
    const before = await survivor.evaluate((node) => node.getBoundingClientRect().top);
    const box = await remove.boundingBox();
    if (!box) throw new Error("Favorite action has no visible box");
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    if (layout === "list") {
      await page.getByRole("button", { name: "Favorited", exact: true }).click();
      await expect(page.getByRole("heading", { name: "Favorite 20", exact: true })).toBeVisible();
    } else await expect(target).toBeVisible();
    // Compare the same unhovered geometry; the next card can move under the pointer.
    await page.mouse.move(1, 1);
    acknowledgement.resolve();
    if (layout === "list") {
      await expect(page.getByRole("button", { name: "Favorite", exact: true })).toBeVisible();
      await page.getByRole("link", { name: "Back", exact: true }).click();
    }
    await expect(page.getByRole("main").getByRole("link", { name: /Favorite 20/ })).toHaveCount(0);
    await expect
      .poll(async () => survivor.evaluate((node) => node.getBoundingClientRect().top))
      .toBeCloseTo(before, 0);
  });
}

for (const recovery of ["available", "removed", "stale"] as const) {
  test(`bounds explicit refresh when its anchor is ${recovery}`, async ({ page }) => {
    await page.clock.install();
    const first = Array.from({ length: 20 }, (_, index) =>
      aModelListItem({ id: 100 + index, name: `First ${index}` }),
    );
    const second = Array.from({ length: 20 }, (_, index) =>
      aModelListItem({ id: 200 + index, name: `Second ${index}` }),
    );
    let changed = false;
    let removed: number | null = null;
    const refreshed: (string | null)[] = [];
    await page.route("**/api/v1/models/browse/revision", (route) =>
      route.fulfill({
        json: { browse_revision: changed ? "r2" : "r1", authorization_revision: "a1" },
      }),
    );
    await page.route("**/api/v1/models/browse?**", (route) => {
      const cursor = new URL(route.request().url()).searchParams.get("cursor");
      if (changed) refreshed.push(cursor);
      if (changed && cursor && recovery === "stale")
        return route.fulfill({ status: 409, json: { detail: "browse_refresh_required" } });
      const models = cursor
        ? second
        : changed
          ? [aModelListItem({ id: 999, name: "Inserted" }), ...first]
          : first;
      return route.fulfill({
        json: {
          items: models
            .filter((model) => !(changed && model.id === removed))
            .map((model) => ({ kind: "model", model })),
          total: 60,
          next_cursor: cursor ? "unvisited-third" : changed ? "second-new" : "second-old",
          browse_revision: changed ? "r2" : "r1",
          authorization_revision: "a1",
        },
      });
    });
    await page.goto("/?c=maraio&type=all&sort=name-asc");
    await page.getByRole("button", { name: "Load more", exact: true }).click();
    await page
      .getByRole("main")
      .getByRole("link", { name: /Second 10/ })
      .first()
      .scrollIntoViewIfNeeded();
    const bookmark = await page.getByRole("main").evaluate((main) => {
      const bounds = main.getBoundingClientRect();
      const anchor = Array.from(main.querySelectorAll<HTMLElement>("[data-library-entry]")).find(
        (node) => {
          const rect = node.getBoundingClientRect();
          return rect.bottom > bounds.top && rect.top < bounds.bottom;
        },
      );
      if (!anchor?.dataset.libraryEntry) throw new Error("No visible reading anchor");
      return { key: anchor.dataset.libraryEntry, top: anchor.getBoundingClientRect().top };
    });
    if (recovery === "removed") removed = Number(bookmark.key.split("/").at(-1));
    changed = true;
    await page.clock.fastForward(31_000);
    const refresh = page.getByRole("button", { name: "Refresh library", exact: true });
    await expect(refresh).toBeVisible();
    // Activate the actual control without locator auto-scroll changing the reading bookmark.
    await refresh.evaluate((button: HTMLButtonElement) => button.click());
    if (recovery === "available") {
      // The coherent old snapshot keeps this anchor in place while refresh is
      // pending. Prove fresh pages published before checking its new position.
      await expect.poll(() => refreshed).toEqual([null, "second-new"]);
      await expect(
        page
          .getByRole("main")
          .getByRole("link", { name: /Inserted/ })
          .first(),
      ).toBeAttached();
      const anchor = page.locator(`[data-library-entry="${bookmark.key}"]`).first();
      await expect
        .poll(async () => anchor.evaluate((node) => node.getBoundingClientRect().top))
        .toBeCloseTo(bookmark.top, 0);
    } else
      await expect(
        page.getByText(
          "Your previous reading position could not be restored. Showing the start of this view.",
        ),
      ).toBeVisible();
    expect(refreshed).toEqual([null, "second-new"]);
  });
}
