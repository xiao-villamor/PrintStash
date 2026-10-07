/** Real-browser acceptance of coherent Library snapshots and private retirement. */
import { expect, test, type Page } from "@playwright/test";
import { useMockApi } from "./_setup";
import {
  aCollectionNode,
  aCollectionPage,
  aModelListItem,
  aModel,
} from "../../src/test-support/factories";

useMockApi();

declare global {
  interface Window {
    __librarySnapshotViolations: string[];
    __libraryHistoryVisits: string[];
  }
}

type SnapshotRead = "browse" | "more" | "lookup" | "children" | "readme";

function holdResponse() {
  return {
    started: Promise.withResolvers<void>(),
    release: Promise.withResolvers<void>(),
    finished: Promise.withResolvers<void>(),
  };
}

/** Gates control HTTP completion only; the production application owns all state. */
async function snapshotApi(page: Page) {
  const collections = ["A", "B", "C"].map((label, index) =>
    aCollectionNode({
      id: 501 + index,
      name: `Snapshot ${label}`,
      slug: `snapshot-${label.toLowerCase()}`,
      path: `snapshot-${label.toLowerCase()}`,
      display_path: `Snapshot ${label}`,
      tags: [`tag-snapshot-${label}`],
      has_readme: true,
      child_count: 1,
      descendant_count: 1,
      model_count: 2,
    }),
  );
  const gates = new Map<string, ReturnType<typeof holdResponse>>();
  const state = { denied: false };
  await page.addInitScript((rows) => {
    localStorage.setItem(
      "ps-recent-folders-labelled",
      JSON.stringify(rows.map((row) => [row.path, row.name])),
    );
  }, collections);
  await page.route(
    /\/api\/v1\/(collections\/(lookup|children|\d+\/readme)|models\/browse\?)/,
    async (route) => {
      const url = new URL(route.request().url());
      const row = collections.find((candidate) =>
        url.pathname.endsWith("/lookup")
          ? candidate.path === url.searchParams.get("path")
          : url.pathname.endsWith("/children")
            ? String(candidate.id) === url.searchParams.get("parent_id")
            : url.pathname.endsWith("/readme")
              ? url.pathname.endsWith(`/${candidate.id}/readme`)
              : candidate.path === url.searchParams.get("collection"),
      );
      if (!row) return route.fallback();
      const kind = url.pathname.endsWith("/lookup")
        ? "lookup"
        : url.pathname.endsWith("/children")
          ? "children"
          : url.pathname.endsWith("/readme")
            ? "readme"
            : url.searchParams.has("cursor")
              ? "more"
              : "browse";
      const gate = gates.get(`${row.path}:${kind}`);
      // Capture the authority when the request arrives, before its response is held.
      const denied = state.denied;
      gate?.started.resolve();
      if (gate) await gate.release.promise;
      try {
        if (denied && kind === "lookup") {
          await route.fulfill({ status: 404, json: { detail: "collection_not_found" } });
        } else if (kind === "lookup") {
          await route.fulfill({ json: { collection: row, ancestors: [] } });
        } else if (kind === "children") {
          await route.fulfill({
            json: aCollectionPage(
              denied
                ? []
                : [
                    aCollectionNode({
                      id: row.id + 10,
                      name: `${row.name} child`,
                      slug: "child",
                      path: `${row.path}/child`,
                      display_path: `${row.name}/child`,
                      parent_id: row.id,
                    }),
                  ],
            ),
          });
        } else if (kind === "readme") {
          await route.fulfill({ json: { readme: denied ? null : `README ${row.name}` } });
        } else {
          await route.fulfill({
            json: {
              items: denied
                ? []
                : [
                    {
                      kind: "model",
                      model: aModelListItem({
                        id: row.id + (kind === "more" ? 100 : 0),
                        name: `${row.name} ${kind === "more" ? "late card" : "card"}`,
                        collection: row.path,
                        collection_id: row.id,
                      }),
                    },
                  ],
              total: denied ? 0 : 2,
              next_cursor: !denied && kind === "browse" ? "held-next-page" : null,
              browse_revision: "r1",
              authorization_revision: denied ? "a2" : "a1",
            },
          });
        }
      } finally {
        gate?.finished.resolve();
      }
    },
  );
  return {
    state,
    hold(path: string, kind: SnapshotRead) {
      const gate = holdResponse();
      gates.set(`${path}:${kind}`, gate);
      return gate;
    },
  };
}

async function releaseResponse(page: Page, gate: ReturnType<typeof holdResponse>) {
  gate.release.resolve();
  await gate.finished.promise;
  // Give delivered HTTP and React commits a rendering opportunity, without a timer sleep.
  await page.evaluate(
    () =>
      new Promise<void>((resolve) => {
        requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
      }),
  );
}

async function visitRecent(page: Page, label: string) {
  await page.getByRole("main").getByRole("button", { name: "Recent", exact: true }).click();
  await page.getByRole("menuitem", { name: `Snapshot ${label}`, exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`c=snapshot-${label.toLowerCase()}`));
}

async function expectSnapshot(page: Page, label: string) {
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { name: `Snapshot ${label}`, exact: true })).toBeVisible();
  await expect(
    main.getByRole("navigation").getByRole("button", { name: `Snapshot ${label}`, exact: true }),
  ).toBeVisible();
  await expect(main.getByText(`tag-snapshot-${label}`, { exact: true })).toBeVisible();
  await expect(main.getByText(`README Snapshot ${label}`, { exact: true })).toBeVisible();
  await expect(main.getByText(`Snapshot ${label} child`, { exact: true })).toBeVisible();
  await expect(
    main.getByRole("link", { name: new RegExp(`Snapshot ${label} card`) }),
  ).toBeVisible();
}

/** Observe committed DOM throughout the race, not only its final state. */
async function watchSnapshots(page: Page, mode: "coherent" | "retired") {
  await page.evaluate((observationMode) => {
    window.__librarySnapshotViolations = [];
    window.__libraryHistoryVisits = [];
    window.addEventListener("popstate", () => window.__libraryHistoryVisits.push(location.href));
    const inspect = () => {
      const main = document.querySelector("main");
      const heading = main?.querySelector("h1")?.textContent ?? "";
      // Recent-menu labels are navigation choices, not displayed snapshot metadata.
      const surfaces = Array.from(
        main?.querySelectorAll("h1, nav > span > button, span[title], p, a") ?? [],
      )
        .map((node) => node.textContent ?? "")
        .join(" ");
      if (observationMode === "retired") {
        if (/Snapshot [ABC]|tag-snapshot-[ABC]/.test(surfaces))
          window.__librarySnapshotViolations.push(surfaces);
      } else {
        const label = heading.match(/Snapshot ([ABC])/)?.[1];
        if (label === "B") window.__librarySnapshotViolations.push(surfaces);
        if (
          label &&
          ["A", "B", "C"].some(
            (other) =>
              other !== label &&
              (surfaces.includes(`Snapshot ${other}`) ||
                surfaces.includes(`tag-snapshot-${other}`)),
          )
        )
          window.__librarySnapshotViolations.push(surfaces);
      }
    };
    new MutationObserver(inspect).observe(document.body, {
      subtree: true,
      childList: true,
      characterData: true,
    });
    inspect();
  }, mode);
}

async function expectPrivateRetired(page: Page) {
  await expect(page.getByRole("heading", { name: /^Snapshot [ABC]$/ })).toHaveCount(0);
  await expect(
    page.getByRole("main").getByRole("link", { name: /Snapshot [ABC].*card/ }),
  ).toHaveCount(0);
  await expect(page.getByText(/^README Snapshot [ABC]$/)).toHaveCount(0);
  await expect(page.getByText(/^tag-snapshot-[ABC]$/)).toHaveCount(0);
}

test.describe("Library snapshots", () => {
  test("keeps one coherent snapshot during rapid collection navigation", async ({ page }) => {
    const api = await snapshotApi(page);
    const bBrowse = api.hold("snapshot-b", "browse");
    const bLookup = api.hold("snapshot-b", "lookup");
    const bChildren = api.hold("snapshot-b", "children");
    const cBrowse = api.hold("snapshot-c", "browse");
    const cLookup = api.hold("snapshot-c", "lookup");
    const cChildren = api.hold("snapshot-c", "children");
    await page.goto("/?c=snapshot-a&type=all&sort=name-asc");
    await expectSnapshot(page, "A");
    await watchSnapshots(page, "coherent");
    await visitRecent(page, "B");
    await Promise.all([bBrowse.started.promise, bLookup.started.promise]);
    await releaseResponse(page, bLookup);
    await bChildren.started.promise;
    await expectSnapshot(page, "A");
    await visitRecent(page, "C");
    await Promise.all([cBrowse.started.promise, cLookup.started.promise]);
    await releaseResponse(page, cBrowse);
    await expectSnapshot(page, "A");
    await releaseResponse(page, cLookup);
    await cChildren.started.promise;
    await expectSnapshot(page, "A");
    await releaseResponse(page, cChildren);
    await expectSnapshot(page, "C");
    await releaseResponse(page, bBrowse);
    await releaseResponse(page, bChildren);
    await expectSnapshot(page, "C");
    expect(await page.evaluate(() => window.__librarySnapshotViolations)).toEqual([]);
  });

  test("retires private Library history when signing out", async ({ page }) => {
    const api = await snapshotApi(page);
    const late = api.hold("snapshot-c", "more");
    const logout = holdResponse();
    await page.route("**/api/v1/auth/logout", async (route) => {
      logout.started.resolve();
      await logout.release.promise;
      try {
        await route.fulfill({ status: 204 });
      } finally {
        logout.finished.resolve();
      }
    });
    await page.goto("/?c=snapshot-a&type=all&sort=name-asc");
    await expectSnapshot(page, "A");
    await visitRecent(page, "C");
    await expectSnapshot(page, "C");
    await page.getByRole("button", { name: "Load more", exact: true }).click();
    await late.started.promise;
    await page.getByRole("button", { name: /tester/ }).click();
    await page.getByRole("menuitem", { name: "Log out", exact: true }).click();
    await logout.started.promise;
    await expectPrivateRetired(page);
    await watchSnapshots(page, "retired");
    await releaseResponse(page, late);
    await releaseResponse(page, logout);
    await expect(page).toHaveURL(/\/login/);
    await page.goBack();
    await expect(page).toHaveURL(/\/login/);
    // The logout control can add a login entry after the auth guard replaced C.
    // Traverse that entry too so this assertion really attempts the old A history.
    if (
      !(await page.evaluate(() =>
        window.__libraryHistoryVisits.some((url) => url.includes("c=snapshot-a")),
      ))
    ) {
      await page.goBack();
      await expect(page).toHaveURL(/\/login/);
    }
    expect(
      await page.evaluate(() =>
        window.__libraryHistoryVisits.some((url) => url.includes("c=snapshot-a")),
      ),
    ).toBe(true);
    await expectPrivateRetired(page);
    expect(await page.evaluate(() => window.__librarySnapshotViolations)).toEqual([]);
  });

  test("retires private Library history when access changes", async ({ page }) => {
    await page.clock.install();
    const api = await snapshotApi(page);
    const late = api.hold("snapshot-c", "more");
    const revalidation = holdResponse();
    await page.route("**/api/v1/models/browse/revision", (route) =>
      route.fulfill({
        json: { browse_revision: "r1", authorization_revision: api.state.denied ? "a2" : "a1" },
      }),
    );
    await page.route("**/api/v1/auth/me", async (route) => {
      if (!api.state.denied) return route.fallback();
      revalidation.started.resolve();
      await revalidation.release.promise;
      try {
        await route.fallback();
      } finally {
        revalidation.finished.resolve();
      }
    });
    await page.goto("/?c=snapshot-a&type=all&sort=name-asc");
    await expectSnapshot(page, "A");
    await visitRecent(page, "C");
    await expectSnapshot(page, "C");
    await page.getByRole("button", { name: "Load more", exact: true }).click();
    await late.started.promise;
    api.state.denied = true;
    await page.clock.fastForward(31_000);
    await revalidation.started.promise;
    await expectPrivateRetired(page);
    await watchSnapshots(page, "retired");
    await releaseResponse(page, late);
    await releaseResponse(page, revalidation);
    await page.goBack();
    await expect(page).toHaveURL(/c=snapshot-a/);
    await expectPrivateRetired(page);
    expect(await page.evaluate(() => window.__librarySnapshotViolations)).toEqual([]);
  });

  for (const deferScrollNotification of [false, true]) {
    test(`keeps independent reading positions for repeated Library URLs${deferScrollNotification ? " before scroll notification" : ""}`, async ({
      page,
    }) => {
      await snapshotApi(page);
      const models = Array.from({ length: 40 }, (_, index) =>
        aModelListItem({
          id: 100 + index,
          name: `History card ${index}`,
          collection: "snapshot-a",
          collection_id: 501,
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
      await page.goto("/?c=snapshot-a&type=all&sort=date-desc");
      const first = page.locator('main [data-library-entry="/models/110"]');
      await first.scrollIntoViewIfNeeded();
      const firstTop = await first.evaluate((node) => node.getBoundingClientRect().top);
      const firstKey = await page.evaluate(() => history.state.key);
      const href = page.url();
      await page.route("**/api/v1/models/110", (route) =>
        route.fulfill({
          json: aModel({ id: 110, name: "History card 10", collection: "snapshot-a" }),
        }),
      );
      await first.click();
      await expect(
        page.getByRole("heading", { name: "History card 10", exact: true }),
      ).toBeVisible();
      // The header's Library link pushes a new visit; detail Back would reuse the old visit.
      await page.getByRole("link", { name: "PrintStash", exact: true }).click();
      await expect(page).toHaveURL(href);
      await expect(page.getByRole("heading", { name: "Snapshot A", exact: true })).toBeVisible();
      const second = page.locator('main [data-library-entry="/models/130"]');
      // History traversal can beat the next scroll notification. Hold only that
      // notification until Back; the real browser still changes the viewport.
      if (deferScrollNotification)
        await page.evaluate(() => {
          const main = document.querySelector("main");
          const deferScroll = (event: Event) => {
            if (event.target instanceof Element && main?.contains(event.target))
              event.stopImmediatePropagation();
          };
          window.addEventListener("scroll", deferScroll, true);
          window.addEventListener(
            "popstate",
            () => window.removeEventListener("scroll", deferScroll, true),
            { once: true },
          );
        });
      await second.scrollIntoViewIfNeeded();
      const secondTop = await second.evaluate((node) => node.getBoundingClientRect().top);
      const secondKey = await page.evaluate(() => history.state.key);
      expect(secondKey).not.toBe(firstKey);

      await page.goBack();
      await expect(
        page.getByRole("heading", { name: "History card 10", exact: true }),
      ).toBeVisible();
      await page.goBack();
      await expect(page).toHaveURL(href);
      expect(await page.evaluate(() => history.state.key)).toBe(firstKey);
      await expect
        .poll(() => first.evaluate((node) => node.getBoundingClientRect().top))
        .toBeCloseTo(firstTop, 0);
      await page.goForward();
      await expect(
        page.getByRole("heading", { name: "History card 10", exact: true }),
      ).toBeVisible();
      await page.goForward();
      await expect(page).toHaveURL(href);
      expect(await page.evaluate(() => history.state.key)).toBe(secondKey);
      await expect
        .poll(() => second.evaluate((node) => node.getBoundingClientRect().top))
        .toBeCloseTo(secondTop, 0);
    });
  }
});
