/** Library inspection keeps native navigation, focused outcomes and Escape ownership observable. */
import { expect, test, type Page } from "@playwright/test";
import { apiPort, collectPageProblems, useMockApi } from "./_setup";
import { aModelListItem } from "../../src/test-support/factories";

useMockApi();

declare global {
  interface Window {
    paneCardAnimations: number;
    paneCardsCrossingCells: string[];
  }
}

async function inspect(page: Page) {
  await page.goto("/?c=maraio&type=all");
  const card = page
    .getByRole("main")
    .getByRole("link", { name: "skadis_kitchen-roll_screw", exact: true });
  await card.click();
  const drawer = page.locator("#model-drawer");
  await expect(drawer).toBeVisible();
  await expect(drawer.getByRole("button", { name: "OK", exact: true })).toBeEnabled();
  return { card, drawer };
}

async function libraryCard(page: Page) {
  const model = aModelListItem({
    id: 1,
    name: "Bracket",
    recommended_revision_status: "needs_test",
  });
  await page.route("**/api/v1/models/browse?**", (route) =>
    route.fulfill({
      json: {
        items: [{ kind: "model", model }],
        total: 1,
        next_cursor: null,
        browse_revision: "r1",
        authorization_revision: "a1",
      },
    }),
  );
  await page.goto("/");
  const card = page.locator("main article").filter({ hasText: "Bracket" });
  await expect(card).toBeVisible();
  return card;
}

async function records(page: Page) {
  const response = await page.request.get(`http://127.0.0.1:${apiPort}/api/v1/models/1/print-jobs`);
  expect(response.ok()).toBe(true);
  return response.json();
}

test.describe("Library inline inspector", () => {
  for (const details of ["closed", "open"] as const) {
    test(`keeps the ${details} inspector stable during background refresh`, async ({
      page,
    }, testInfo) => {
      await page.clock.install();
      const model = aModelListItem({ id: 1, name: "skadis_kitchen-roll_screw" });
      const release = Promise.withResolvers<void>();
      let changed = false;
      let refreshReads = 0;
      await page.route("**/api/v1/models/browse/revision", (route) =>
        route.fulfill({
          json: { browse_revision: changed ? "r2" : "r1", authorization_revision: "a1" },
        }),
      );
      await page.route("**/api/v1/models/browse?**", async (route) => {
        if (changed) {
          refreshReads++;
          await release.promise;
        }
        await route.fulfill({
          json: {
            items: [{ kind: "model", model }],
            total: 1,
            next_cursor: null,
            browse_revision: changed ? "r2" : "r1",
            authorization_revision: "a1",
          },
        });
      });
      await page.goto("/?type=all&sort=date-desc");
      const toggle = page.getByRole("button", { name: "Show or hide details", exact: true });
      await expect(toggle).toBeVisible();
      if (details === "open") await toggle.click();
      await expect(toggle).toHaveAttribute("aria-pressed", String(details === "open"));
      await page.clock.runFor(1_000);
      await page.screenshot({ path: testInfo.outputPath("before-refresh.png") });
      const originalToggle = await toggle.evaluateHandle((node) => node);
      const originalBounds = await toggle.boundingBox();
      const modelCard = page.locator('[data-library-entry="/models/1"]').first();
      const cardBounds = await modelCard.boundingBox();
      const revision = page.waitForResponse("**/api/v1/models/browse/revision");
      changed = true;

      await page.clock.fastForward(10_000);
      await revision;
      await page.clock.runFor(1_000);
      await expect.poll(() => refreshReads).toBe(1);

      expect(await originalToggle.evaluate((node) => node.isConnected)).toBe(true);
      await expect(toggle).toBeVisible();
      expect(await toggle.boundingBox()).toEqual(originalBounds);
      await expect(toggle).toHaveAttribute("aria-pressed", String(details === "open"));
      expect(await modelCard.boundingBox()).toEqual(cardBounds);
      await expect(
        page.getByText("The library has changed. Refresh before loading more results."),
      ).toBeHidden();
      await page.screenshot({ path: testInfo.outputPath("during-refresh.png") });
      const updated = page.waitForResponse("**/api/v1/models/browse?**");
      release.resolve();
      await updated;
      await page.clock.runFor(1_000);
      expect(await originalToggle.evaluate((node) => node.isConnected)).toBe(true);
      await expect(toggle).toHaveAttribute("aria-pressed", String(details === "open"));
      expect(await toggle.boundingBox()).toEqual(originalBounds);
      expect(await modelCard.boundingBox()).toEqual(cardBounds);
      await expect(page.getByRole("button", { name: "Refresh library", exact: true })).toBeHidden();
    });
  }

  test("animates visible cards only on Details transitions", async ({ page }) => {
    const models = Array.from({ length: 92 }, (_, index) =>
      aModelListItem({ id: index + 1, name: `Part ${index + 1}` }),
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
    await page.addInitScript(() => {
      window.paneCardAnimations = 0;
      window.paneCardsCrossingCells = [];
      const animate = Element.prototype.animate;
      Element.prototype.animate = function (...args) {
        if (this instanceof HTMLElement && this.matches("main [data-library-key]")) {
          window.paneCardAnimations++;
          const [frames] = args;
          const transform = Array.isArray(frames) ? frames[0]?.transform : undefined;
          const match = /^translate\(([-.\d]+)px, [-.\d]+px\) scale\(([-.\d]+),/.exec(
            String(transform ?? ""),
          );
          if (match && this.parentElement) {
            const distance = Math.abs(Number(match[1]));
            const spacing = Number.parseFloat(getComputedStyle(this.parentElement).columnGap);
            const originalWidth = Number(match[2]) * this.offsetWidth;
            if (distance > originalWidth + spacing + 1)
              window.paneCardsCrossingCells.push(this.dataset.libraryKey ?? "");
          }
        }
        const animation = animate.apply(this, args);
        if (this.id === "model-drawer" && this.getAttribute("data-state") === "open")
          animation.pause();
        return animation;
      };
    });
    await page.goto("/?type=all&sort=date-desc");
    await expect(page.getByRole("heading", { name: "Part 92", exact: true })).toBeVisible();
    const details = page.getByRole("button", { name: "Show or hide details", exact: true });
    const sidebar = page.getByRole("button", { name: "Toggle sidebar", exact: true });
    const drawer = page.locator("#model-drawer");

    const animations = () => page.evaluate(() => window.paneCardAnimations);
    const beforeOpeningDetails = await animations();

    await details.click();
    await expect(drawer).toBeVisible();
    await expect.poll(animations).toBeGreaterThan(beforeOpeningDetails);
    const readLayout = () =>
      page.evaluate(() => ({
        paneWidth: document.getElementById("model-drawer")?.offsetWidth,
        models: [
          ...document.querySelectorAll<HTMLElement>(".library-model-content .library-virtual-row"),
        ].map((row) => [row.clientWidth, row.style.gridTemplateColumns]),
      }));
    const openingLayout = await readLayout();
    await drawer.evaluate(
      (element) =>
        new Promise<void>((resolve) => {
          for (const animation of element.getAnimations()) animation.finish();
          requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
        }),
    );
    expect(await readLayout()).toEqual(openingLayout);
    const beforeClosingDetails = await animations();
    await details.click();
    await expect(drawer).toBeHidden();
    await expect.poll(animations).toBeGreaterThan(beforeClosingDetails);

    const beforeClosingSidebar = await animations();
    await sidebar.click();
    await expect(sidebar).toHaveAttribute("aria-expanded", "false");
    await page.waitForFunction(() => !document.documentElement.hasAttribute("data-pane-moving"));
    expect(await animations()).toBe(beforeClosingSidebar);
    await sidebar.click();
    await expect(sidebar).toHaveAttribute("aria-expanded", "true");
    await page.waitForFunction(() => !document.documentElement.hasAttribute("data-pane-moving"));
    expect(await animations()).toBe(beforeClosingSidebar);
    expect(await page.evaluate(() => window.paneCardsCrossingCells)).toEqual([]);
  });

  test("returns from the explicit full page to the same inspector", async ({ page }) => {
    const problems = await collectPageProblems(page);
    const { drawer } = await inspect(page);
    const origin = page.url();
    expect(new URL(origin).pathname).toBe("/");
    await drawer.getByRole("link", { name: "Open page", exact: true }).click();
    await expect(page).toHaveURL(/\/models\/1(?:\?|$)/);
    await page.getByRole("link", { name: "Back", exact: true }).click();
    await expect(page).toHaveURL(origin);
    await expect(drawer).toBeVisible();
    await expect(
      drawer.getByRole("heading", { name: "skadis_kitchen-roll_screw", exact: true }),
    ).toBeVisible();
    expect(problems).toEqual([]);
  });

  test("opens the full page from the card corner on hover", async ({ page }) => {
    const card = await libraryCard(page);
    await card.hover();

    await card.getByRole("link", { name: "Open full page", exact: true }).click();

    await expect(page).toHaveURL(/\/models\/1(?:\?|$)/);
  });

  test("hides the card's full-page action until hover", async ({ page }) => {
    const card = await libraryCard(page);

    await page.mouse.move(0, 0);

    await expect(card.getByRole("link", { name: "Open full page", exact: true })).toHaveCSS(
      "opacity",
      "0",
    );
  });

  test("keeps the status in the image corner", async ({ page }) => {
    const card = await libraryCard(page);
    await page.mouse.move(0, 0);

    const status = await card.locator(".library-card-status").boundingBox();
    const frame = await card.boundingBox();

    expect(status!.y - frame!.y).toBeLessThanOrEqual(8);
  });

  test("moves the status clear of the full-page action on hover", async ({ page }) => {
    const card = await libraryCard(page);
    await card.hover();
    const action = card.getByRole("link", { name: "Open full page", exact: true });

    await expect
      .poll(async () => {
        const status = await card.locator(".library-card-status").boundingBox();
        const corner = await action.boundingBox();
        return status!.x + status!.width <= corner!.x;
      })
      .toBe(true);
  });

  test("shows no drag cursor around the card selection control", async ({ page }) => {
    const card = await libraryCard(page);
    await card.hover();
    const box = await card.getByRole("checkbox").boundingBox();

    const cursor = await page.evaluate(
      ({ x, y }) => getComputedStyle(document.elementFromPoint(x, y)!).cursor,
      { x: box!.x - 2, y: box!.y - 2 },
    );

    expect(cursor).toBe("default");
  });

  test("persists one completed outcome while a numeric key is held", async ({ page }) => {
    const problems = await collectPageProblems(page);
    const posts: unknown[] = [];
    page.on("request", (request) => {
      if (
        request.method() === "POST" &&
        new URL(request.url()).pathname === "/api/v1/models/1/print-jobs"
      )
        posts.push(request.postDataJSON());
    });
    const { drawer } = await inspect(page);
    await drawer.getByRole("tab", { name: "Print", exact: true }).focus();
    const receipt = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === "/api/v1/models/1/print-jobs",
    );
    await page.keyboard.down("1");
    expect((await receipt).ok()).toBe(true);
    await expect(drawer.getByRole("tabpanel", { name: /History/ })).toBeVisible();
    await drawer.getByRole("tab", { name: "Print", exact: true }).click();
    await expect(drawer.getByRole("button", { name: "OK", exact: true })).toBeEnabled();
    await page.keyboard.down("1");
    await page.keyboard.up("1");
    expect(posts).toEqual([
      expect.objectContaining({ file_id: 2, printer_id: 3, state: "completed", error: null }),
    ]);
    expect(await records(page)).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: 4,
          file_id: 2,
          printer_id: 3,
          state: "completed",
          source: "manual",
        }),
      ]),
    );
    expect(problems).toEqual([]);
  });

  test("persists the focused failure note through Mod Enter", async ({ page }) => {
    const problems = await collectPageProblems(page);
    const { drawer } = await inspect(page);
    await drawer.getByRole("tab", { name: "Print", exact: true }).focus();
    await page.keyboard.press("2");
    const note = drawer.getByRole("textbox", { name: /What went wrong/ });
    await expect(note).toBeFocused();
    await note.fill("Corner lifted at layer 112");
    const receipt = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === "/api/v1/models/1/print-jobs",
    );
    await note.press("Control+Enter");
    expect((await receipt).ok()).toBe(true);
    await expect(note).toBeHidden();
    await drawer.getByRole("tab", { name: /History/ }).click();
    await expect(
      drawer
        .getByRole("tabpanel", { name: /History/ })
        .getByText("Corner lifted at layer 112", { exact: true }),
    ).toBeVisible();
    expect(await records(page)).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: 4,
          file_id: 2,
          printer_id: 3,
          state: "failed",
          error: "Corner lifted at layer 112",
        }),
      ]),
    );
    expect(problems).toEqual([]);
  });

  test("returns Escape focus to the model card after a pointer opening", async ({ page }) => {
    await page.goto("/?c=maraio&type=all");
    const sidebar = page.getByRole("button", { name: "Toggle sidebar", exact: true });
    await sidebar.click();
    await expect(sidebar).toHaveAttribute("aria-expanded", "false");
    const card = page
      .getByRole("main")
      .getByRole("link", { name: "skadis_kitchen-roll_screw", exact: true });
    await card.click();
    const drawer = page.locator("#model-drawer");
    await expect(drawer).toBeVisible();

    await page.keyboard.press("Escape");

    await expect(drawer).toBeHidden();
    await expect(card).toBeFocused();
  });

  test("returns Escape focus to the inspected card when details opened from the workspace", async ({
    page,
  }) => {
    await page.goto("/?c=maraio&type=all");
    const sidebar = page.getByRole("button", { name: "Toggle sidebar", exact: true });
    await sidebar.click();
    await expect(sidebar).toHaveAttribute("aria-expanded", "false");
    const main = page.getByRole("main");
    const card = main.getByRole("link", { name: "skadis_kitchen-roll_screw", exact: true });
    await expect(card).toBeVisible();
    await main.focus();
    await page.keyboard.press("Control+Alt+b");
    const drawer = page.locator("#model-drawer");
    await expect(drawer).toBeVisible();

    await page.keyboard.press("Escape");

    await expect(drawer).toBeHidden();
    await expect(card).toBeFocused();
  });

  test("cancels failure before closing details before clearing selection", async ({ page }) => {
    const problems = await collectPageProblems(page);
    await page.goto("/?c=maraio&type=all");
    const card = page
      .getByRole("main")
      .getByRole("link", { name: "skadis_kitchen-roll_screw", exact: true });
    await card.hover();
    const selection = page.getByRole("checkbox", {
      name: "Select skadis_kitchen-roll_screw",
      exact: true,
    });
    await selection.check();
    await page.keyboard.press("Control+Alt+b");
    const drawer = page.locator("#model-drawer");
    await expect(drawer).toBeVisible();
    await expect(drawer.getByRole("button", { name: "OK", exact: true })).toBeEnabled();
    await drawer.getByRole("tab", { name: "Print", exact: true }).focus();
    await page.keyboard.press("2");
    const note = drawer.getByRole("textbox", { name: /What went wrong/ });
    await expect(note).toBeFocused();
    await note.fill("Unsaved note");
    await page.keyboard.press("Escape");
    await expect(note).toBeHidden();
    await expect(drawer).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
    await expect(selection).toBeChecked();
    await page.keyboard.press("Escape");
    await expect(selection).not.toBeChecked();
    expect(await records(page)).toHaveLength(3);
    expect(problems).toEqual([]);
  });
});
