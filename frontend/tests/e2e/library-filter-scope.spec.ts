/*
 * A folder that only holds subfolders has no models of its own, so filters that
 * looked at its direct models alone offered nothing at all. Facets describe the
 * whole subtree, and an active filter searches it too; an explicitly scoped folder
 * still lists only its direct models.
 */
import { expect, test, type Page } from "@playwright/test";

import { useMockApi } from "./_setup";
import { aCollectionNode, aCollectionPage, aModelListItem } from "../../src/test-support/factories";
import { collapseNavigationSidebar } from "../e2e-real/util";

useMockApi();

async function folderOfFolders(page: Page, { withModels = false } = {}) {
  const parent = aCollectionNode({
    id: 1,
    name: "parent",
    path: "parent",
    child_count: 1,
    model_count: withModels ? 2 : 0,
  });
  const child = aCollectionNode({
    id: 2,
    name: "child",
    path: "parent/child",
    parent_id: parent.id,
    model_count: withModels ? 1 : 0,
  });
  const requests: {
    kind: "browse" | "facets";
    collection: string | null;
    direct: string | null;
    filtered: boolean;
  }[] = [];
  await page.route("**/api/v1/collections/lookup?**", (route) => {
    const params = new URL(route.request().url()).searchParams;
    const isChild = params.get("path") === child.path || params.get("id") === String(child.id);
    return route.fulfill({
      json: { collection: isChild ? child : parent, ancestors: isChild ? [parent] : [] },
    });
  });
  await page.route("**/api/v1/collections/children?**", (route) => {
    const id = new URL(route.request().url()).searchParams.get("parent_id");
    return route.fulfill({
      json: aCollectionPage(id === String(parent.id) ? [child] : id === null ? [parent] : []),
    });
  });
  await page.route("**/api/v1/models/facets?**", (route) => {
    const url = new URL(route.request().url());
    requests.push({
      kind: "facets",
      collection: url.searchParams.get("collection"),
      direct: url.searchParams.get("direct"),
      filtered: url.searchParams.has("file_type"),
    });
    return route.fulfill({
      json: {
        file_type: [{ value: "stl", count: 7 }],
        revision_status: [],
        material_type: [],
        slicer: [],
        printer_model: [],
        print_outcome: [],
        storage: [],
        printed: [],
      },
    });
  });
  await page.route("**/api/v1/models/browse?**", (route) => {
    const url = new URL(route.request().url());
    const collection = url.searchParams.get("collection");
    const direct = url.searchParams.get("direct");
    requests.push({
      kind: "browse",
      collection,
      direct,
      filtered: url.searchParams.has("file_type"),
    });
    const items = [parent, child]
      .filter((folder) => {
        if (!withModels) return false;
        if (folder.path === collection) return true;
        return (
          direct !== "true" && (collection === null || folder.path.startsWith(`${collection}/`))
        );
      })
      .map((folder) => ({
        kind: "model",
        model: aModelListItem({
          id: folder.id,
          name: `Model in ${folder.name}`,
          collection_id: folder.id,
          collection: folder.path,
          collection_label: folder.name,
        }),
      }));
    return route.fulfill({
      json: {
        items,
        total: items.length,
        next_cursor: null,
        browse_revision: "r1",
        authorization_revision: "a1",
      },
    });
  });
  return requests;
}

test.describe("filters in a folder of folders", () => {
  for (const layout of ["Grid", "List"]) {
    test(`shows a single compact child entry in ${layout.toLowerCase()} view`, async ({ page }) => {
      await folderOfFolders(page, { withModels: true });
      await page.goto("/?c=parent&scope=here");
      await collapseNavigationSidebar(page);
      await page.getByRole("button", { name: `${layout} View`, exact: true }).click();

      const main = page.getByRole("main");
      await expect(main.getByText("Model in parent", { exact: true })).toBeVisible();
      await expect(main.getByText("child", { exact: true })).toHaveCount(1);
      await expect(main.getByRole("link", { name: "Open collection" })).toHaveCount(0);
      await expect(main.getByText("Model in child", { exact: true })).toHaveCount(0);
    });
  }

  test("includes descendant models in the main grid when asked", async ({ page }) => {
    await folderOfFolders(page, { withModels: true });
    await page.goto("/?c=parent&scope=here");
    const main = page.getByRole("main");
    await main.getByRole("button", { name: "Include subfolders", exact: true }).click();

    await expect(main.getByText("Model in parent", { exact: true })).toBeVisible();
    await expect(main.getByText("Model in child", { exact: true })).toBeVisible();
    await expect(main.getByRole("link", { name: "Open collection" })).toHaveCount(0);
  });

  test("opens a child collection from its compact card", async ({ page }) => {
    await folderOfFolders(page, { withModels: true });
    await page.goto("/?c=parent&scope=here");
    const main = page.getByRole("main");
    await main.getByRole("button", { name: /^child\b/ }).click();

    await expect(main.getByRole("heading", { name: "child", exact: true })).toBeVisible();
    await expect(main.getByText("Model in child", { exact: true })).toBeVisible();
    await expect(main.getByText("Model in parent", { exact: true })).toHaveCount(0);
  });

  test("distinguishes hovering from an enabled subfolder scope in dark mode", async ({
    page,
  }, testInfo) => {
    await page.addInitScript(() => localStorage.setItem("printstash.theme", "dark"));
    await folderOfFolders(page);
    await page.goto("/?c=parent&scope=here");
    await collapseNavigationSidebar(page);

    const scope = page.getByRole("button", { name: "Include subfolders", exact: true });
    await expect(scope).toBeEnabled();
    await expect(scope).toHaveAttribute("aria-pressed", "false");
    const restingFill = await scope.evaluate((button) => getComputedStyle(button).backgroundColor);
    await scope.screenshot({ path: testInfo.outputPath("scope-rest.png"), animations: "disabled" });

    await scope.hover();
    await scope.screenshot({
      path: testInfo.outputPath("scope-hover.png"),
      animations: "disabled",
    });
    const hoverFill = await scope.evaluate((button) => getComputedStyle(button).backgroundColor);

    await scope.click();
    await expect(scope).toHaveAttribute("aria-pressed", "true");
    await expect(scope).toBeEnabled();
    await scope.screenshot({
      path: testInfo.outputPath("scope-enabled-hover.png"),
      animations: "disabled",
    });
    const enabledHoverFill = await scope.evaluate(
      (button) => getComputedStyle(button).backgroundColor,
    );

    await page.mouse.move(0, 0);
    await scope.screenshot({
      path: testInfo.outputPath("scope-enabled.png"),
      animations: "disabled",
    });
    await expect(scope).toHaveCSS("background-color", "rgb(54, 64, 78)");
    expect(restingFill).toBe("rgb(19, 23, 30)");
    expect(hoverFill).toBe("rgb(35, 42, 53)");
    expect(enabledHoverFill).toBe("rgb(54, 64, 78)");
  });

  test("preserves the subfolder scope after keyboard activation and reload", async ({ page }) => {
    await folderOfFolders(page);
    await page.goto("/?c=parent&scope=here");
    const scope = page.getByRole("button", { name: "Include subfolders", exact: true });

    await expect(scope).toBeEnabled();
    await scope.press("Space");
    await expect(page).toHaveURL(/scope=all/);
    await page.reload();
    await expect(scope).toHaveAttribute("aria-pressed", "true");
    await expect(scope).toBeEnabled();
    await scope.press("Space");
    await expect(scope).toHaveAttribute("aria-pressed", "false");
  });

  test("an explicit current-folder scope lists only its own models", async ({ page }) => {
    const requests = await folderOfFolders(page);
    await page.goto("/?c=parent&scope=here");
    await expect(page.getByRole("heading", { name: "parent", exact: true })).toBeVisible();

    const parentBrowse = () =>
      requests.filter((r) => r.kind === "browse" && r.collection === "parent");
    await expect.poll(() => parentBrowse().length).toBeGreaterThan(0);
    expect(parentBrowse().map((r) => r.direct)).not.toContain(null);
    await page.getByRole("button", { name: "Filters", exact: true }).click();
    // The filter picker uses subtree facets independently of the scoped status counts.
    await expect
      .poll(() =>
        requests.some((r) => r.kind === "facets" && r.collection === "parent" && r.direct === null),
      )
      .toBe(true);
  });

  test("applying a filter in a folder of folders searches its whole subtree", async ({ page }) => {
    const requests = await folderOfFolders(page);
    await page.goto("/?c=parent&file_type=stl");
    await expect(page.getByRole("heading", { name: "parent", exact: true })).toBeVisible();

    await expect.poll(() => requests.some((r) => r.kind === "browse" && r.filtered)).toBe(true);
    const filtered = requests.filter((r) => r.kind === "browse" && r.filtered);
    expect(filtered.every((r) => r.direct === null)).toBe(true);
  });

  test("subfolders are listed until a filter turns the view into a list of matches", async ({
    page,
  }) => {
    await folderOfFolders(page);
    const childFolder = page.getByRole("main").getByRole("button", { name: /child/ });

    await page.goto("/?c=parent");
    await expect(childFolder).toBeVisible();

    await page.goto("/?c=parent&file_type=stl");
    await expect(page.getByRole("heading", { name: "parent", exact: true })).toBeVisible();
    await expect(childFolder).toHaveCount(0);
  });
});
