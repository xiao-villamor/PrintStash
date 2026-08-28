import { expect, installMockApiHooks, test, type Locator, type Page } from "./helpers";

installMockApiHooks();

test("vault display choice survives reload", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Display" }).click();
  await page.getByRole("menuitem", { name: "List View" }).click();
  await expect(page.getByText("Thumb", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText("Thumb", { exact: true })).toBeVisible();
});

test("vault sort requests one globally sorted cursor page", async ({ page }) => {
  const pageRequests: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/models/page") pageRequests.push(url.search);
  });
  await page.goto("/");
  await expect(page.getByText("skadis_kitchen-roll_screw").first()).toBeVisible();

  await page.getByRole("button", { name: "Sort models" }).click();
  await Promise.all([
    page.waitForRequest((request) => {
      const url = new URL(request.url());
      return (
        url.pathname === "/api/v1/models/page" && url.searchParams.get("sort") === "success-desc"
      );
    }),
    page.getByRole("menuitem", { name: "Best success rate" }).click(),
  ]);
  await expect(page.getByText("skadis_kitchen-roll_screw").first()).toBeVisible();
  await page.waitForTimeout(200);

  expect(
    pageRequests.filter((query) => new URLSearchParams(query).get("sort") === "success-desc"),
  ).toHaveLength(1);
});

test("mobile vault skips the desktop outliner request", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const outlinerRequests: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/v1/models/outliner") {
      outlinerRequests.push(request.url());
    }
  });

  await page.goto("/");
  await expect(page.getByText("skadis_kitchen-roll_screw").first()).toBeVisible();
  await page.waitForTimeout(200);
  expect(outlinerRequests).toEqual([]);
});

async function gotoMobileVault(page: Page, width = 360): Promise<void> {
  await page.setViewportSize({ width, height: 844 });
  await page.goto("/");
}

function mobileMore(page: Page): Locator {
  return page.getByRole("main").getByRole("button", { name: "More", exact: true });
}

async function openMobileMore(page: Page): Promise<Locator> {
  await mobileMore(page).click();
  const menu = page.getByRole("menu").last();
  await expect(menu).toBeVisible();
  return menu;
}

async function expectTouchTarget(locator: Locator): Promise<void> {
  const box = await locator.boundingBox();
  expect(box).not.toBeNull();
  expect(box?.height ?? 0).toBeGreaterThanOrEqual(40);
}

test("mobile vault keeps the title and count readable with Upload as primary", async ({ page }) => {
  await gotoMobileVault(page);

  await expect(page.getByRole("heading", { name: "All Models" })).toBeVisible();
  await expect(page.getByText(/^\d+ models? total$/)).toBeVisible();
  const uploads = page.getByRole("button", { name: "Upload", exact: true });
  await expect(uploads).toBeVisible();
  expect(
    await uploads.evaluateAll(
      (elements) =>
        elements.filter((element) => {
          const box = element.getBoundingClientRect();
          return box.width > 0 && box.height > 0;
        }).length,
    ),
  ).toBe(1);
});

test("mobile vault keeps Filters, sort, and More in one equal-height row", async ({ page }) => {
  await gotoMobileVault(page);

  const filters = page.getByRole("button", { name: "Filters", exact: true });
  const sort = page.getByRole("button", { name: "Sort models", exact: true });
  const more = mobileMore(page);

  await expect(filters).toBeVisible();
  await expect(sort).toBeVisible();
  await expect(more).toBeVisible();
  await expectTouchTarget(filters);
  await expectTouchTarget(sort);
  await expectTouchTarget(more);
  const boxes = await Promise.all([filters.boundingBox(), sort.boundingBox(), more.boundingBox()]);
  expect(boxes.every((box) => box !== null)).toBe(true);
  expect(new Set(boxes.map((box) => Math.round(box?.height ?? 0))).size).toBe(1);
  expect(new Set(boxes.map((box) => Math.round(box?.y ?? 0))).size).toBe(1);
  expect(boxes[0]?.x).toBeLessThan(boxes[1]?.x ?? 0);
  expect(boxes[1]?.x).toBeLessThan(boxes[2]?.x ?? 0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});

test("mobile More exposes secondary actions with valid checked states", async ({ page }) => {
  await gotoMobileVault(page);

  const menu = await openMobileMore(page);
  const favoritesItem = menu.getByRole("menuitemcheckbox", { name: "Favorites" });
  const savedViewsItem = menu.getByRole("menuitem", { name: /Saved views/ });
  const selectItem = menu.getByRole("menuitemcheckbox", { name: "Select" });
  const displayItem = menu.getByRole("menuitem", { name: "Display" });
  await expect(favoritesItem).toBeVisible();
  await expect(savedViewsItem).toBeVisible();
  await expect(selectItem).toBeVisible();
  await expect(displayItem).toBeVisible();
  await expect(favoritesItem).toHaveAttribute("aria-checked", "false");
  await expect(selectItem).toHaveAttribute("aria-checked", "false");

  await favoritesItem.click();
  await expect(page).toHaveURL(/[?&]favorites=true/);
  await openMobileMore(page);
  await expect(page.getByRole("menuitemcheckbox", { name: "Favorites" })).toHaveAttribute(
    "aria-checked",
    "true",
  );

  await page.getByRole("menuitemcheckbox", { name: "Select" }).click();
  await openMobileMore(page);
  await expect(page.getByRole("menuitemcheckbox", { name: "Done" })).toHaveAttribute(
    "aria-checked",
    "true",
  );

  await page.getByRole("menuitem", { name: "Display" }).click();
  await expect(page.getByRole("menuitem", { name: "List View" })).toBeVisible();
  await page.getByRole("menuitem", { name: "List View" }).click();
  await expect(page.getByText("Thumb", { exact: true })).toBeVisible();
});

test("mobile Saved Views owns picker keys, restores focus, and applies a view", async ({
  page,
}) => {
  await page.route("**/api/v1/saved-views", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify([
        {
          id: 7,
          name: "Ready to print",
          filters: {
            collection: null,
            direct: true,
            tag: [],
            q: "skadis",
            printer_id: null,
            printer_presence: null,
            favorites: false,
          },
          created_at: "2026-06-04T00:24:22.000000",
          updated_at: "2026-06-04T00:24:22.000000",
        },
      ]),
    });
  });
  await gotoMobileVault(page);

  const menu = await openMobileMore(page);
  const savedViewsItem = menu.getByRole("menuitem", { name: /Saved views/ });
  await savedViewsItem.click();
  const dialog = page.getByRole("dialog");
  const search = page.getByRole("textbox", { name: "Find a saved view" });
  await expect(dialog).toBeVisible();
  await expect(search).toBeFocused();
  await search.press("ArrowDown");
  await expect(search).toBeFocused();
  await search.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(savedViewsItem).toBeFocused();

  await savedViewsItem.click();
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Ready to print", exact: true }).click();
  await expect(page).toHaveURL(/[?&]q=skadis/);
  await expect(dialog).toBeHidden();
});

/** Computed animation-delay of every direct child of the staggered model grid. */
async function gridDelays(page: Page) {
  await page.goto("/");
  const grid = page.locator(".stagger-children").first();
  await expect(grid.locator("> *").first()).toBeAttached();
  return grid.evaluate((el) =>
    Array.from(el.children).map((c) => getComputedStyle(c).animationDelay),
  );
}

test("grid cards enter on a capped stagger", async ({ page }) => {
  const delays = await gridDelays(page);
  expect(delays.length).toBeGreaterThan(1);

  expect(delays[0]).toBe("0s");
  expect(delays[1]).toBe("0.03s");
  // The cap is the point: a full 60-card page must still land inside the 300ms
  // UI budget rather than marching in for two seconds.
  for (const delay of delays) {
    expect(Number.parseFloat(delay)).toBeLessThanOrEqual(0.27);
  }
});

test("reduced motion drops the grid stagger entirely", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });

  // The stagger rules are :nth-child (specificity 0,2,0); a naive
  // `.stagger-children > *` override loses to them and the grid keeps marching in.
  for (const delay of await gridDelays(page)) {
    expect(delay).toBe("0s");
  }
});

test("header and recent-folder menus stay above adjacent vault surfaces", async ({ page }) => {
  await page.addInitScript(() =>
    localStorage.setItem("ps-recent-folders", JSON.stringify(["maraio"])),
  );
  await page.goto("/");

  const headerZ = await page
    .locator("header")
    .evaluate((element) => Number(getComputedStyle(element).zIndex));
  const stickyZ = await page
    .locator(".sticky.top-0")
    .evaluate((element) => Number(getComputedStyle(element).zIndex));
  expect(headerZ).toBeGreaterThan(stickyZ);

  await page.getByRole("button", { name: "Recent" }).click();
  const menuBox = await page.getByRole("menu").boundingBox();
  const sidebarBox = await page.locator("aside").boundingBox();
  expect(menuBox).not.toBeNull();
  expect(sidebarBox).not.toBeNull();
  expect(menuBox!.x).toBeGreaterThanOrEqual(sidebarBox!.x + sidebarBox!.width);
});
