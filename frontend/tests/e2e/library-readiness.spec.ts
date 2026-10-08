/** Diagnostic fault injection verifies readiness; acceptance timings use real API responses. */
import { writeFile } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";
import { useMockApi } from "./_setup";
import {
  currentNavigationStart,
  installObserver,
  observeInteraction,
  openAvailableLibraryTree,
} from "../../scripts/library-performance/observer.mjs";
import { aCollectionNode, aModelListItem } from "../../src/test-support/factories";

declare global {
  interface Window {
    libraryObservation: { started: number; complete: number | null; errors: string[] };
    releaseReadinessDecode: () => void;
    currentNavigationStart: () => PerformanceMark | null;
    completedDrawer: { left: number; running: number } | null;
  }
}

useMockApi();
test.beforeEach(async ({ page }) => {
  await page.addInitScript(`window.currentNavigationStart = ${currentNavigationStart.toString()}`);
});

test.afterEach(async ({ page }, info) => {
  if (info.status === info.expectedStatus) return;
  const state = await page.evaluate(() => ({
    history: history.state,
    marks: performance
      .getEntriesByType("mark")
      .filter((entry): entry is PerformanceMark => entry instanceof PerformanceMark)
      .map((entry) => ({ ...entry.toJSON(), detail: entry.detail })),
    images: [...document.querySelectorAll("[data-library-thumbnail]")].map((node) => ({
      state: node.getAttribute("data-library-thumbnail"),
      complete: node.querySelector("img")?.complete,
      width: node.querySelector("img")?.naturalWidth,
    })),
  }));
  const path = info.outputPath("readiness-state.json");
  await writeFile(path, JSON.stringify(state, null, 2));
  await info.attach("readiness-state", { path, contentType: "application/json" });
});

async function phases(page: Page, phase: string) {
  return page.evaluate((name) => {
    const start = window.currentNavigationStart();
    return start
      ? performance
          .getEntriesByName(start.name.replace(/start$/, name))
          .map((entry) => entry.startTime)
      : [];
  }, phase);
}

test.describe("Library readiness", () => {
  test("opens only an immediately usable mobile tree control", async ({ page }) => {
    await page.setContent(`<main>
      <button style="display:none">Filters</button>
      <button id="visible-filter" disabled onclick="this.dataset.clicked='yes'">Filters</button>
      <div id="cover" style="position:fixed;inset:0;background:white"></div>
    </main>`);
    expect(await page.evaluate(openAvailableLibraryTree)).toBe(false);
    await page.locator("#visible-filter").evaluate((node) => node.removeAttribute("disabled"));
    expect(await page.evaluate(openAvailableLibraryTree)).toBe(false);
    await page.locator("#cover").evaluate((node) => node.remove());
    expect(await page.evaluate(openAvailableLibraryTree)).toBe(true);
    await expect(page.locator("#visible-filter")).toHaveAttribute("data-clicked", "yes");
    await expect(page.locator("button").first()).not.toHaveAttribute("data-clicked", "yes");
  });

  test("selects committed navigation instead of preparation order", async ({ page }) => {
    await page.route("**/", (route) =>
      route.fulfill({ contentType: "text/html", body: "<main>Ready</main>" }),
    );
    await page.goto("/");
    await page.evaluate(() => {
      history.replaceState({ key: "destination" }, "");
      performance.mark("printstash:navigation:3:start");
      performance.mark("printstash:navigation:4:start");
      performance.mark("printstash:navigation:current", {
        detail: { navigation: 4, historyKey: "previous", active: true },
      });
      performance.mark("printstash:navigation:current", {
        detail: { navigation: 3, historyKey: "destination", active: true },
      });
    });
    expect(await page.evaluate(`(${currentNavigationStart.toString()})()?.name`)).toBe(
      "printstash:navigation:3:start",
    );
    await page.evaluate(() => history.replaceState({ key: "next" }, ""));
    expect(await page.evaluate(currentNavigationStart)).toBeNull();
    await page.evaluate(() => {
      history.replaceState({ key: "destination" }, "");
      performance.mark("printstash:navigation:current", {
        detail: { navigation: 3, historyKey: "destination", active: false },
      });
    });
    expect(await page.evaluate(currentNavigationStart)).toBeNull();
  });

  test("completes the committed mobile search destination", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/?c=maraio");
    await page.getByRole("button", { name: "Filters", exact: true }).click();
    await expect.poll(() => phases(page, "complete")).toHaveLength(1);
    await page.keyboard.press("Escape");
    await page.getByRole("dialog", { name: "Filters", exact: true }).waitFor({ state: "hidden" });
    await page.locator("[data-model-search]").fill("skadis");
    await expect(page).toHaveURL(/q=skadis/);
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "Filters", exact: true }).click();
    await expect
      .poll(async () => {
        const name = await page.evaluate<string | null>(
          `(${currentNavigationStart.toString()})()?.name ?? null`,
        );
        if (!name) return false;
        return page.evaluate(
          (name) => performance.getEntriesByName(name.replace(/start$/, "complete")).length > 0,
          name,
        );
      })
      .toBe(true);
  });

  test("waits for the restored tree before completing", async ({ page }) => {
    const gate = Promise.withResolvers<void>();
    await page.addInitScript(() =>
      sessionStorage.setItem("ps-filter-expanded", JSON.stringify(["maraio"])),
    );
    await page.route("**/api/v1/outliner/restore", async (route) => {
      await gate.promise;
      await route.continue();
    });
    try {
      await page.goto("/?c=maraio", { waitUntil: "domcontentloaded" });
      await expect.poll(() => phases(page, "cards")).toHaveLength(1);
      expect(await phases(page, "complete")).toEqual([]);
      const released = await page.evaluate(() => performance.now());
      gate.resolve();
      await expect(
        page.locator('aside [role="button"][title="skadis_kitchen-roll_screw"]'),
      ).toBeVisible();
      await expect.poll(() => phases(page, "complete")).toHaveLength(1);
      expect((await phases(page, "complete"))[0]).toBeGreaterThan(released);
    } finally {
      gate.resolve();
    }
  });

  test("waits for visible image decoding", async ({ page }) => {
    await page.addInitScript(() => {
      const original = HTMLImageElement.prototype.decode;
      let release!: () => void;
      const pending = new Promise<void>((resolve) => {
        release = resolve;
      });
      Object.assign(window, { releaseReadinessDecode: release });
      HTMLImageElement.prototype.decode = async function () {
        await original.call(this);
        await pending;
      };
    });
    await page.goto("/?c=maraio", { waitUntil: "domcontentloaded" });
    await expect(page.locator("main [data-library-thumbnail] img").first()).toBeVisible();
    await expect.poll(() => phases(page, "tree")).toHaveLength(1);
    expect(await phases(page, "complete")).toEqual([]);
    await page.evaluate(() => window.releaseReadinessDecode());
    await expect.poll(() => phases(page, "complete")).toHaveLength(1);
    await expect(page.locator("main [data-library-thumbnail]").first()).toHaveAttribute(
      "data-library-thumbnail",
      "ready",
    );
  });

  test("does not complete after restoration fails", async ({ page }) => {
    await page.addInitScript(() =>
      sessionStorage.setItem("ps-filter-expanded", JSON.stringify(["maraio"])),
    );
    await page.route("**/api/v1/outliner/restore", (route) =>
      route.fulfill({ status: 503, json: { detail: "temporarily_unavailable" } }),
    );
    await page.goto("/?c=maraio");
    await expect.poll(() => phases(page, "failed")).toHaveLength(1);
    expect(await phases(page, "complete")).toEqual([]);
    await expect(page.getByRole("button", { name: "Retry", exact: true }).first()).toBeVisible();
  });

  test("does not complete after an expected thumbnail fails", async ({ page }) => {
    await page.route("**/api/v1/files/1/thumbnail", (route) =>
      route.fulfill({ status: 503, body: "unavailable" }),
    );
    await page.goto("/?c=maraio");
    await expect(page.locator('main [data-library-thumbnail="failed"]')).toBeVisible();
    await expect.poll(() => phases(page, "failed")).toHaveLength(1);
    expect(await phases(page, "complete")).toEqual([]);
  });

  test("admits only visible thumbnails while critical downloads are pending", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const models = Array.from({ length: 24 }, (_, index) =>
      aModelListItem({
        id: index + 100,
        name: `Viewport ${index}`,
        thumbnail_url: `/api/v1/files/${index + 100}/thumbnail`,
      }),
    );
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
    const gate = Promise.withResolvers<void>();
    const requests: number[] = [];
    await page.route("**/api/v1/files/*/thumbnail", async (route) => {
      requests.push(Number(new URL(route.request().url()).pathname.split("/")[4]));
      await gate.promise;
      await route.abort();
    });
    try {
      await page.goto("/?c=maraio", { waitUntil: "domcontentloaded" });
      await expect.poll(() => requests.length).toBe(2);
      const visible = await page.locator("main article").evaluateAll((cards) =>
        cards
          .filter((card) => {
            const rect = card.querySelector("[data-library-thumbnail]")?.getBoundingClientRect();
            return rect && rect.top < innerHeight && rect.bottom > 0;
          })
          .map((card) =>
            Number(
              new URL(
                card.querySelector<HTMLAnchorElement>('a[href^="/models/"]')!.href,
              ).pathname.split("/")[2],
            ),
          ),
      );
      expect(requests.every((id) => visible.includes(id))).toBe(true);
      expect(await phases(page, "complete")).toEqual([]);
      expect(requests).not.toContain(123);
    } finally {
      gate.resolve();
    }
  });

  test("restores the mobile tree before opening without declaring a hidden tree complete", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.addInitScript(() =>
      sessionStorage.setItem("ps-filter-expanded", JSON.stringify(["maraio"])),
    );
    await page.addInitScript(() => {
      window.completedDrawer = null;
      new PerformanceObserver((list) => {
        if (
          !list
            .getEntries()
            .some((entry) => /^printstash:navigation:\d+:complete$/.test(entry.name))
        )
          return;
        const drawer = document.querySelector('[role="dialog"][aria-label="Filters"]');
        if (drawer)
          window.completedDrawer = {
            left: drawer.getBoundingClientRect().left,
            running: drawer.getAnimations().filter((animation) => animation.playState === "running")
              .length,
          };
      }).observe({ type: "mark" });
    });
    const restored = page.waitForResponse("**/api/v1/outliner/restore");
    await page.goto("/?c=maraio", { waitUntil: "domcontentloaded" });
    await restored;
    await expect.poll(() => phases(page, "media")).toHaveLength(1);
    expect(await phases(page, "complete")).toEqual([]);
    await page.getByRole("button", { name: "Filters", exact: true }).click();
    await expect(
      page.locator('[role="dialog"] [role="button"][title="skadis_kitchen-roll_screw"]'),
    ).toBeVisible();
    await expect.poll(() => phases(page, "complete")).toHaveLength(1);
    expect(await page.evaluate(() => window.completedDrawer)).toEqual({ left: 0, running: 0 });
  });

  for (const width of [1440, 390]) {
    test(`keeps a deeply nested collection selectable at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      const nodes = Array.from({ length: 8 }, (_, index) => ({
        ...aCollectionNode({
          id: index + 1,
          name: `Nested ${index + 1}`,
          path: Array.from({ length: index + 1 }, (_, part) => `nested-${part + 1}`).join("/"),
          parent_id: index || null,
        }),
        direct_entry_count: 0,
        subtree_entry_count: 0,
        visible_child_count: index < 7 ? 1 : 0,
      }));
      await page.addInitScript(
        (paths) => sessionStorage.setItem("ps-filter-expanded", JSON.stringify(paths)),
        nodes.map((node) => node.path),
      );
      await page.route("**/api/v1/outliner/restore", (route) =>
        route.fulfill({
          json: {
            collections: [null, ...nodes.map((node) => node.id)].map((parent_id) => ({
              parent_id,
              page: {
                items: nodes.filter((node) => node.parent_id === parent_id),
                next_cursor: null,
                parent_direct_entry_count: 0,
                revealed: null,
              },
            })),
            entries: [],
          },
        }),
      );
      await page.route("**/api/v1/collections/lookup?**", (route) => {
        const path = new URL(route.request().url()).searchParams.get("path");
        const collection = nodes.find((node) => node.path === path)!;
        return route.fulfill({
          json: {
            collection,
            ancestors: nodes.filter((node) => collection.path.startsWith(node.path + "/")),
          },
        });
      });
      await page.goto("/");
      if (width === 390) await page.getByRole("button", { name: "Filters", exact: true }).click();
      const target = page
        .locator(width === 390 ? '[role="dialog"]' : "aside")
        .getByRole("button", { name: "Nested 8", exact: true });
      await target.scrollIntoViewIfNeeded();
      expect((await target.boundingBox())!.width).toBeGreaterThan(40);
      await target.click();
      await expect(page).toHaveURL(new RegExp("c=" + encodeURIComponent(nodes[7].path)));
    });
  }

  test("keeps the measurement clock across a document restart", async ({ page }) => {
    let documents = 0;
    await page.route("**/", (route) => {
      documents++;
      return route.fulfill({
        contentType: "text/html",
        body:
          documents === 1
            ? "<main><h1>Starting</h1></main><script>setTimeout(() => location.reload(), 80)</script>"
            : '<aside class="bg-sidebar" style="width:200px;height:100px">Tree</aside><main><h1>All Models</h1><input data-model-search></main>',
      });
    });
    await page.addInitScript(installObserver, {
      user: { id: 1 },
      locale: "en",
      target: {
        expanded: [],
        collection: null,
        titles: { en: "All Models", es: "Todos los modelos" },
        branches: [],
        leaves: [],
        entries: [],
        folders: [],
      },
    });
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => window.libraryObservation?.complete !== null);
    const observed = await page.evaluate(() => window.libraryObservation);
    expect(documents).toBe(2);
    expect(observed.started).toBeLessThan(0);
    expect(observed.complete).toBeGreaterThanOrEqual(80);
    expect(observed.errors).toEqual([]);
  });

  for (const event of ["click", "input"] as const) {
    test(`starts interaction timing at the actual ${event}`, async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.route("**/", (route) =>
        route.fulfill({
          contentType: "text/html",
          body: '<aside class="bg-sidebar" style="width:200px;height:100px">Tree</aside><main><h1>All Models</h1><input data-model-search><button>Next</button></main>',
        }),
      );
      const target = {
        expanded: [],
        collection: null,
        titles: { en: "All Models", es: "Todos los modelos" },
        branches: [],
        leaves: [],
        entries: [],
        folders: [],
      };
      await page.addInitScript(installObserver, { user: { id: 1 }, locale: "en", target });
      await page.goto("/");
      await page.waitForFunction(() => window.libraryObservation.complete !== null);
      const previous = await page.evaluate(() => window.libraryObservation.started);
      const control =
        event === "click" ? page.getByRole("button", { name: "Next" }) : page.locator("input");
      await control.evaluate(observeInteraction, { event, target });
      await page.evaluate(
        () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
      );
      expect(await page.evaluate(() => window.libraryObservation.started)).toBe(previous);
      const armed = await page.evaluate(() => performance.now());
      if (event === "click") await control.click();
      else await control.fill("query");
      expect(await page.evaluate(() => window.libraryObservation.started)).toBeGreaterThan(armed);
    });
  }

  for (const device of ["desktop", "mobile"]) {
    test(`the independent observer waits for usable ${device} library controls`, async ({
      page,
    }) => {
      await page.setViewportSize(
        device === "mobile" ? { width: 390, height: 844 } : { width: 1440, height: 900 },
      );
      await page.route("**/", (route) =>
        route.fulfill({
          contentType: "text/html",
          body: '<aside class="bg-sidebar" style="width:200px;height:100px">Tree</aside><main><h1>All Models</h1><input data-model-search hidden disabled><input data-model-search disabled><button aria-label="Filters" hidden>Filters</button><button disabled>Filters</button></main>',
        }),
      );
      await page.addInitScript(installObserver, {
        user: { id: 1 },
        locale: "en",
        target: {
          expanded: [],
          collection: null,
          titles: { en: "All Models", es: "Todos los modelos" },
          branches: [],
          leaves: [],
          entries: [],
          folders: [],
        },
      });
      await page.goto("/");
      await page.evaluate(
        () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
      );
      expect(await page.evaluate(() => window.libraryObservation.complete)).toBeNull();
      const enabled = await page.evaluate(() => {
        document.querySelector<HTMLInputElement>("[data-model-search]:not([hidden])")!.disabled =
          false;
        return performance.now();
      });
      if (device === "mobile") {
        await page.evaluate(
          () =>
            new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
        );
        expect(await page.evaluate(() => window.libraryObservation.complete)).toBeNull();
        await page.locator("button:not([hidden])").evaluate((button: HTMLButtonElement) => {
          button.disabled = false;
        });
      }
      await page.waitForFunction(() => window.libraryObservation.complete !== null);
      expect(await page.evaluate(() => window.libraryObservation.complete)).toBeGreaterThan(
        enabled,
      );
    });
  }
});
