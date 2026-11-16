/*
 * A folder that only holds subfolders has no models of its own, so filters that
 * looked at its direct models alone offered nothing at all. Facets describe the
 * whole subtree, and an active filter searches it too; an explicitly scoped folder
 * still lists only its direct models.
 */
import { expect, test, type Page } from "@playwright/test";

import { useMockApi } from "./_setup";
import { aCollectionNode } from "../../src/test-support/factories";

useMockApi();

async function folderOfFolders(page: Page) {
  const requests: { kind: "browse" | "facets"; direct: string | null; filtered: boolean }[] = [];
  await page.route("**/api/v1/collections/lookup?**", (route) =>
    route.fulfill({
      json: {
        collection: aCollectionNode({
          id: 1,
          name: "parent",
          path: "parent",
          has_readme: false,
          child_count: 1,
        }),
        ancestors: [],
      },
    }),
  );
  await page.route("**/api/v1/collections/children?**", (route) =>
    route.fulfill({
      json: {
        items: [aCollectionNode({ id: 2, name: "child", path: "parent/child", has_readme: false })],
        next_cursor: null,
      },
    }),
  );
  await page.route("**/api/v1/models/facets?**", (route) => {
    const url = new URL(route.request().url());
    requests.push({
      kind: "facets",
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
    requests.push({
      kind: "browse",
      direct: url.searchParams.get("direct"),
      filtered: url.searchParams.has("file_type"),
    });
    return route.fulfill({
      json: {
        items: [],
        total: 0,
        next_cursor: null,
        browse_revision: "r1",
        authorization_revision: "a1",
      },
    });
  });
  return requests;
}

test.describe("filters in a folder of folders", () => {
  test("an explicit current-folder scope lists only its own models", async ({ page }) => {
    const requests = await folderOfFolders(page);
    await page.goto("/?c=parent&scope=here");
    await expect(page.getByRole("heading", { name: "parent", exact: true })).toBeVisible();

    await expect.poll(() => requests.some((r) => r.kind === "browse")).toBe(true);
    expect(requests.filter((r) => r.kind === "browse").every((r) => r.direct === "true")).toBe(
      true,
    );
    await page.getByRole("button", { name: "Filters", exact: true }).click();
    // Facets never ask for direct models only, so options exist below this folder.
    await expect.poll(() => requests.some((r) => r.kind === "facets")).toBe(true);
    expect(requests.filter((r) => r.kind === "facets").every((r) => r.direct === null)).toBe(true);
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
