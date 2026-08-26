import { expect, installMockApiHooks, test, type Page } from "./helpers";

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
