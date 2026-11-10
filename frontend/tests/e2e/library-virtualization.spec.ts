/** Loaded pages keep their cursor order while measured rows bound the mounted cards. */
import { expect, test, type Page } from "@playwright/test";
import { aModelListItem, aMultipartModel, aCollectionNode } from "../../src/test-support/factories";
import { useMockApi, collectPageProblems } from "./_setup";

useMockApi();

async function scrollLibrary(page: Page, offset: "start" | "end") {
  await page.locator("main").evaluate((main, end) => {
    const list = main.querySelector<HTMLElement>("[data-library-list]");
    const scroll = list && list.scrollHeight > list.clientHeight ? list : main;
    scroll.scrollTop = end === "end" ? scroll.scrollHeight : 0;
  }, offset);
}

test.describe("Loaded Library surfaces", () => {
  for (const layout of ["grid", "list"] as const) {
    test(`bounds loaded ${layout} cards while retaining the final Model`, async ({ page }) => {
      const problems = await collectPageProblems(page);
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.addInitScript((view) => localStorage.setItem("ps-vault-view", view), layout);
      const cursors: (string | null)[] = [];
      await page.route("**/api/v1/collections/children?**", (route) =>
        route.fulfill({ json: { items: [], next_cursor: null } }),
      );
      await page.route("**/api/v1/models/browse?**", (route) => {
        const params = new URL(route.request().url()).searchParams;
        expect(params.get("limit")).toBe("24");
        const cursor = params.get("cursor");
        cursors.push(cursor);
        const start = cursor === null ? 0 : Number(cursor);
        return route.fulfill({
          json: {
            items: Array.from({ length: 24 }, (_, index) => ({
              kind: "model",
              model: aModelListItem({
                id: start + index + 100,
                name: `Loaded Model ${start + index + 1}`,
                tags:
                  index % 3 === 0 ? ["A long tag that wraps to exercise measured card rows"] : [],
              }),
            })),
            total: 192,
            next_cursor: start + 24 < 192 ? String(start + 24) : null,
            browse_revision: "r1",
            authorization_revision: "a1",
          },
        });
      });
      await page.goto("/?type=all");
      await expect(
        page
          .getByRole("main")
          .getByRole("link", { name: /Loaded Model 1\b/ })
          .first(),
      ).toBeVisible();
      for (let index = 1; index < 8; index += 1) {
        await scrollLibrary(page, "end");
        await expect.poll(() => new Set(cursors).size).toBeGreaterThanOrEqual(index + 1);
      }
      await scrollLibrary(page, "end");
      await expect(page.getByRole("link", { name: /Loaded Model 192\b/ })).toBeVisible();
      expect(cursors.filter((cursor) => cursor !== null)).toEqual([
        "24",
        "48",
        "72",
        "96",
        "120",
        "144",
        "168",
      ]);
      expect(await page.locator("main [data-library-key]").count()).toBeLessThan(60);
      await page.screenshot({ path: test.info().outputPath(`virtual-${layout}-end.png`) });
      expect(problems).toEqual([]);
    });
  }

  test("retains a focused card while its row leaves the viewport", async ({ page }) => {
    await page.route("**/api/v1/collections/children?**", (route) =>
      route.fulfill({ json: { items: [], next_cursor: null } }),
    );
    await page.route("**/api/v1/models/browse?**", (route) =>
      route.fulfill({
        json: {
          items: Array.from({ length: 192 }, (_, index) => ({
            kind: "model",
            model: aModelListItem({ id: 100 + index, name: `Focus Model ${index + 1}` }),
          })),
          total: 192,
          next_cursor: null,
          browse_revision: "r1",
          authorization_revision: "a1",
        },
      }),
    );
    await page.goto("/?type=all");
    const anchor = page.getByRole("link", { name: /Focus Model 3\b/ }).first();
    await anchor.focus();
    await scrollLibrary(page, "end");
    await expect(anchor).toBeFocused();
    await expect(page.getByRole("link", { name: /Focus Model 192\b/ })).toBeVisible();
    await anchor.press("Tab");
    await expect
      .poll(() =>
        page.evaluate(() =>
          document.activeElement?.closest("[data-library-key]")?.getAttribute("data-library-key"),
        ),
      )
      .not.toBeNull();
  });

  test("measures mixed cards after changing the grid columns", async ({ page }) => {
    await page.route("**/api/v1/collections/children?**", (route) =>
      route.fulfill({ json: { items: [], next_cursor: null } }),
    );
    await page.route("**/api/v1/models/browse?**", (route) =>
      route.fulfill({
        json: {
          items: Array.from({ length: 144 }, (_, index) =>
            index % 3 === 0
              ? {
                  kind: "multipart",
                  multipart: aMultipartModel({
                    id: 100 + index,
                    name: `Multipart ${index}`,
                    tags: ["Tall content", "Extra tag"],
                  }),
                }
              : {
                  kind: "model",
                  model: aModelListItem({
                    id: 100 + index,
                    name: `Model ${index} ${"longname ".repeat(index % 5)}`,
                  }),
                },
          ),
          total: 144,
          next_cursor: null,
          browse_revision: "r1",
          authorization_revision: "a1",
        },
      }),
    );
    await page.goto("/?type=all");
    await expect(page.locator('[data-library-renderer="virtual"]').first()).toBeVisible();
    await scrollLibrary(page, "end");
    const last = page.getByRole("link", { name: /^Model 143 / });
    await expect(last).toBeVisible();
    const anchor = await page.locator("main").evaluate((main) => {
      const top = main.getBoundingClientRect().top;
      const node = Array.from(main.querySelectorAll<HTMLElement>("[data-library-key]")).find(
        (candidate) => candidate.getBoundingClientRect().bottom > top,
      );
      if (!node?.dataset.libraryKey) throw new Error("Missing visible reading anchor");
      return node.dataset.libraryKey;
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.locator(`[data-library-key="${anchor}"]`)).toBeVisible();
    expect(
      await page.locator("main").evaluate((main) => main.scrollWidth <= main.clientWidth),
    ).toBe(true);
    const overlaps = await page
      .locator(".library-virtual-row")
      .evaluateAll((rows) =>
        rows.some(
          (row, index) =>
            index > 0 &&
            row.getBoundingClientRect().top < rows[index - 1]!.getBoundingClientRect().bottom - 1,
        ),
      );
    expect(overlaps).toBe(false);
  });

  test("keeps focus when 120 native entries become 144 virtual entries", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.route("**/api/v1/collections/children?**", (route) =>
      route.fulfill({ json: { items: [], next_cursor: null } }),
    );
    await page.route("**/api/v1/models/browse?**", (route) => {
      const cursor = new URL(route.request().url()).searchParams.get("cursor");
      const start = cursor === null ? 0 : Number(cursor);
      return route.fulfill({
        json: {
          items: Array.from({ length: 24 }, (_, index) => ({
            kind: "model",
            model: aModelListItem({
              id: 100 + start + index,
              name: `Threshold Model ${start + index + 1}`,
            }),
          })),
          total: 144,
          next_cursor: start + 24 < 144 ? String(start + 24) : null,
          browse_revision: "r1",
          authorization_revision: "a1",
        },
      });
    });
    await page.goto("/?type=all");
    const anchor = page.getByRole("link", { name: /Threshold Model 1\b/ }).first();
    await expect(page.locator('[data-library-renderer="native"]')).toBeVisible();
    await anchor.focus();
    for (let pages = 2; pages <= 5; pages += 1) {
      await scrollLibrary(page, "end");
      await expect(page.getByText(`${pages * 24} items shown`, { exact: true })).toBeVisible();
    }
    await expect(page.locator('[data-library-renderer="native"]')).toBeVisible();
    await expect(anchor).toBeFocused();
    await scrollLibrary(page, "end");
    await expect(page.locator('[data-library-renderer="virtual"]')).toBeVisible();
    await expect(anchor).toBeFocused();
  });

  test("keeps the source of a native Model drag mounted while scrolling", async ({ page }) => {
    await page.route("**/api/v1/collections/children?**", (route) =>
      route.fulfill({ json: { items: [], next_cursor: null } }),
    );
    await page.route("**/api/v1/models/browse?**", (route) =>
      route.fulfill({
        json: {
          items: Array.from({ length: 192 }, (_, index) => ({
            kind: "model",
            model: aModelListItem({
              id: 100 + index,
              name: `Drag Model ${index + 1}`,
              effective_role: "admin",
            }),
          })),
          total: 192,
          next_cursor: null,
          browse_revision: "r1",
          authorization_revision: "a1",
        },
      }),
    );
    await page.goto("/?type=all");
    const source = page.locator('[data-library-key="/models/100"] article');
    await expect(source).toHaveAttribute("draggable", "true");
    const transfer = await page.evaluateHandle(() => new DataTransfer());
    await source.dispatchEvent("dragstart", { dataTransfer: transfer });
    await scrollLibrary(page, "end");
    await expect(page.getByRole("link", { name: /Drag Model 192\b/ })).toBeVisible();
    await expect(source).toHaveCount(1);
    await source.dispatchEvent("dragend", { dataTransfer: transfer });
    await expect(source).toHaveCount(0);
  });

  test("selects a loaded range whose first Model is no longer visible", async ({ page }) => {
    await page.route("**/api/v1/collections/children?**", (route) =>
      route.fulfill({ json: { items: [], next_cursor: null } }),
    );
    await page.route("**/api/v1/models/browse?**", (route) =>
      route.fulfill({
        json: {
          items: Array.from({ length: 192 }, (_, index) => ({
            kind: "model",
            model: aModelListItem({ id: 100 + index, name: `Range Model ${index + 1}` }),
          })),
          total: 192,
          next_cursor: null,
          browse_revision: "r1",
          authorization_revision: "a1",
        },
      }),
    );
    await page.goto("/?type=all");
    await page
      .getByRole("link", { name: /Range Model 1\b/ })
      .first()
      .hover();
    await page.getByRole("checkbox", { name: "Select Range Model 1", exact: true }).check();
    await page.getByRole("button", { name: "Done", exact: true }).focus();
    await scrollLibrary(page, "end");
    await page.getByRole("link", { name: /Range Model 192\b/ }).click({ modifiers: ["Shift"] });
    await expect(page.getByRole("main").getByText("192 selected", { exact: true })).toBeVisible();
  });

  test("keeps status shortcuts within the mobile work surface", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.route("**/api/v1/models/facets**", (route) =>
      route.fulfill({
        json: {
          file_type: [],
          material_type: [],
          slicer_name: [],
          printer_model: [],
          revision_status: [
            { value: "known_good", count: 3 },
            { value: "needs_test", count: 1 },
          ],
          print_outcome: [],
          storage: [],
          printed: [],
        },
      }),
    );
    await page.goto("/?type=all");
    await expect(page.getByRole("button", { name: /^Ready/ })).toBeVisible();
    expect(
      await page.getByRole("main").evaluate((main) => main.scrollWidth <= main.clientWidth),
    ).toBe(true);
    const filters = page.getByRole("main").getByRole("button", { name: "Filters", exact: true });
    const visible = await filters.filter({ visible: true }).boundingBox();
    expect(visible?.width).toBeGreaterThan(80);
  });

  for (const recovery of ["available", "removed", "stale"] as const) {
    test(`reconstructs six virtual pages when the anchor is ${recovery}`, async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.clock.install();
      let returning = false;
      const restored: (string | null)[] = [];
      await page.route("**/api/v1/collections/children?**", (route) =>
        route.fulfill({ json: { items: [], next_cursor: null } }),
      );
      await page.route("**/api/v1/models/browse?**", (route) => {
        const cursor = new URL(route.request().url()).searchParams.get("cursor");
        if (returning) restored.push(cursor);
        if (returning && cursor && recovery === "stale")
          return route.fulfill({ status: 409, json: { detail: "browse_refresh_required" } });
        const start = cursor === null ? 0 : Number(cursor);
        const models = Array.from({ length: 24 }, (_, index) => {
          const number = start + index;
          return aModelListItem({
            id: number === 126 ? 1 : number + 100,
            name: number === 126 ? "skadis_kitchen-roll_screw" : `Reconstructed Model ${number}`,
          });
        });
        return route.fulfill({
          json: {
            items: models
              .filter((model) => !(returning && recovery === "removed" && model.id === 1))
              .map((model) => ({ kind: "model", model })),
            total: 168,
            next_cursor: start + 24 < 168 ? String(start + 24) : null,
            browse_revision: "r1",
            authorization_revision: "a1",
          },
        });
      });
      await page.goto("/?type=all");
      await expect(
        page.getByRole("link", { name: "Reconstructed Model 0", exact: true }),
      ).toBeVisible();
      for (let pages = 2; pages <= 6; pages += 1) {
        await scrollLibrary(page, "end");
        await expect(page.getByText(`${pages * 24} items shown`, { exact: true })).toBeVisible();
      }
      const anchor = page
        .getByRole("main")
        .getByRole("link", { name: "skadis_kitchen-roll_screw", exact: true });
      await expect
        .poll(
          async () => {
            if (await anchor.count()) return true;
            await page.getByRole("main").evaluate((main) => {
              main.scrollTop += main.clientHeight / 2;
            });
            await page.clock.runFor(20);
            return false;
          },
          { intervals: [50, 100, 150] },
        )
        .toBe(true);
      await anchor.evaluate((node) => node.scrollIntoView({ block: "center" }));
      await page.clock.runFor(50);
      await anchor.click();
      const openPage = page
        .locator("#model-drawer")
        .getByRole("link", { name: "Open page", exact: true });
      await expect(openPage).toBeVisible();
      await page.clock.runFor(100);
      // Match the departure layout after the real inspector changes the columns.
      const before = await anchor.evaluate((node) => node.getBoundingClientRect().top);
      await openPage.click();
      await expect(
        page.getByRole("heading", { name: "skadis_kitchen-roll_screw", exact: true }),
      ).toBeVisible();
      await page.clock.runFor(1_000);
      await page.clock.fastForward(301_000);
      returning = true;
      await page.getByRole("link", { name: "Back", exact: true }).click();
      if (recovery === "available") {
        await expect(anchor).toBeVisible();
        await expect
          .poll(() => anchor.evaluate((node) => node.getBoundingClientRect().top))
          .toBeCloseTo(before, 0);
      } else {
        await expect(
          page.getByText(
            "Your previous reading position could not be restored. Showing the start of this view.",
          ),
        ).toBeVisible();
      }
      expect(restored.filter((cursor) => cursor !== null)).toEqual(
        recovery === "stale" ? ["24"] : ["24", "48", "72", "96", "120"],
      );
      expect(restored).not.toContain("144");
    });
  }

  for (const layout of ["grid", "list"] as const) {
    test(`bounds loaded Collection ${layout} rows`, async ({ page }) => {
      await page.addInitScript((view) => localStorage.setItem("ps-vault-view", view), layout);
      const collections = Array.from({ length: 192 }, (_, index) =>
        aCollectionNode({
          id: 100 + index,
          name: `Collection ${index}`,
          path: `collection-${index}`,
          display_path: `Collection ${index}`,
        }),
      );
      await page.route("**/api/v1/collections/children?**", (route) =>
        route.fulfill({ json: { items: collections, next_cursor: null } }),
      );
      await page.route("**/api/v1/models/browse?**", (route) =>
        route.fulfill({
          json: {
            items: [],
            total: 0,
            next_cursor: null,
            browse_revision: "r1",
            authorization_revision: "a1",
          },
        }),
      );
      await page.goto("/?type=all");
      const main = page.getByRole("main");
      await expect(main.getByRole("button", { name: /Collection 0\b/ })).toBeVisible();
      expect(await main.locator("[data-collection-path]").count()).toBeLessThan(60);
      await scrollLibrary(page, "end");
      await expect(main.getByRole("button", { name: /Collection 191\b/ })).toBeInViewport();
    });
  }

  test("keeps a multipart tag draft when columns change", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    let multipart = aMultipartModel({ id: 90, name: "Persistent multipart", tags: ["Existing"] });
    const writes: { body: string | null; base: string | undefined }[] = [];
    await page.route("**/api/v1/collections/children?**", (route) =>
      route.fulfill({ json: { items: [], next_cursor: null } }),
    );
    await page.route("**/api/v1/models/browse?**", (route) =>
      route.fulfill({
        json: {
          items: Array.from({ length: 144 }, (_, index) =>
            index === 1
              ? { kind: "multipart", multipart }
              : {
                  kind: "model",
                  model: aModelListItem({ id: 100 + index, name: `Dialog Model ${index}` }),
                },
          ),
          total: 144,
          next_cursor: null,
          browse_revision: "r1",
          authorization_revision: "a1",
        },
      }),
    );
    await page.route("**/api/v1/multipart-models/90/tags", (route) => {
      writes.push({
        body: route.request().postData(),
        base: route.request().headers()["if-match"],
      });
      multipart = { ...multipart, tags: ["Existing", "Resize draft"], edit_version: 2 };
      return route.fulfill({ json: multipart });
    });
    await page.goto("/?type=all");
    await page
      .getByRole("button", { name: "Edit tags for Persistent multipart", exact: true })
      .click();
    const dialog = page.getByRole("dialog", {
      name: "Edit tags — Persistent multipart",
      exact: true,
    });
    await dialog.getByLabel("Find or create tag", { exact: true }).fill("Resize draft");
    await dialog.getByLabel("Find or create tag", { exact: true }).press("Enter");
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Resize draft", exact: true })).toBeVisible();
    await dialog.getByRole("button", { name: "Save tags", exact: true }).click();
    await expect(dialog).toBeHidden();
    expect(writes).toEqual([
      {
        body: JSON.stringify({ tags: ["Existing", "Resize draft"] }),
        base: `"multipart-90-e${multipart.edit_epoch}-v1"`,
      },
    ]);
  });
});
