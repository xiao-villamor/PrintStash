/** A nested destination appears through the lazy sidebar before a real move persists. */
import { test, expect } from "./helpers";
import { modelCard, openLibraryTools, uploadModel } from "./util";

test.describe("collection tree", () => {
  test("reports a pending folder drop at the library root until the move is confirmed", async ({
    page,
  }, testInfo) => {
    await page.addInitScript(() => localStorage.setItem("printstash.theme", "dark"));
    const stamp = Date.now();
    const parent = `e2e-drop-parent-${stamp}`;
    const child = `e2e-drop-child-${stamp}`;
    const parentResponse = await page.request.post("/api/v1/collections", {
      data: { name: parent },
    });
    expect(parentResponse.status()).toBe(201);
    const parentId: number = (await parentResponse.json()).id;
    let childId: number | null = null;
    try {
      const childResponse = await page.request.post("/api/v1/collections", {
        data: { name: child, parent_id: parentId },
      });
      expect(childResponse.status()).toBe(201);
      childId = (await childResponse.json()).id;
      await page.goto(`/?c=${parent}`);

      const sidebar = page.locator("#collections-pane");
      const root = sidebar.getByRole("button", { name: "All Models", exact: true });
      const source = sidebar.getByRole("treeitem", { name: child, exact: true });
      const sourceRow = sidebar.locator(`[data-tree-node="${parent}/${child}"]`);
      const movedRow = sidebar.locator(`[data-tree-node="${child}"]`);
      const release = Promise.withResolvers<void>();
      const browseReads: string[] = [];
      await page.route(`**/api/v1/collections/${childId}`, async (route) => {
        if (route.request().method() === "PATCH") await release.promise;
        await route.continue();
      });

      await expect(source).toBeVisible();
      const currentHeading = page
        .getByRole("main")
        .getByRole("heading", { name: parent, exact: true });
      const heading = await currentHeading.evaluateHandle((element) => element);
      page.on("request", (request) => {
        const url = new URL(request.url());
        if (
          url.pathname === "/api/v1/models/browse" &&
          url.searchParams.get("collection") === parent
        ) {
          browseReads.push(request.url());
        }
      });
      const from = await source.boundingBox();
      const to = await root.boundingBox();
      if (!from || !to) throw new Error("Could not locate collection drag targets");
      try {
        await page.mouse.move(from.x + 30, from.y + from.height / 2);
        await page.mouse.down();
        await page.mouse.move(from.x + 45, from.y + from.height / 2, { steps: 3 });
        await page.mouse.move(to.x + 30, to.y + to.height / 2, { steps: 6 });
        await expect(root.locator("..")).toHaveAttribute("data-drop-active", "true");
        await sidebar.screenshot({ path: testInfo.outputPath("root-drop-hover.png") });
        await page.mouse.up();
        await expect(root).toHaveAttribute("aria-busy", "true");
        await expect(
          sidebar.getByText(`Moving ${child} to All Models…`, { exact: true }),
        ).toBeVisible();
        await expect(sourceRow).toBeVisible();
        await expect(movedRow).toHaveCount(0);
        await sidebar.screenshot({ path: testInfo.outputPath("root-drop-pending.png") });
      } finally {
        release.resolve();
      }

      await expect(movedRow).toBeVisible();
      await expect(root).not.toHaveAttribute("aria-busy");
      await expect(page.getByText("Moved", { exact: true })).toBeVisible();
      expect(await heading.evaluate((element) => element.isConnected)).toBe(true);
      expect(browseReads).toHaveLength(1);
      await page.reload();
      await expect(movedRow).toBeVisible();
      await expect(sourceRow).toHaveCount(0);
    } finally {
      if (childId !== null) {
        expect((await page.request.delete(`/api/v1/collections/${childId}`)).status()).toBe(204);
      }
      expect((await page.request.delete(`/api/v1/collections/${parentId}`)).status()).toBe(204);
    }
  });

  test("moves a Model into a newly expanded nested folder", async ({ page }) => {
    const stamp = Date.now();
    const parent = `e2e-tree-parent-${stamp}`;
    const child = `e2e-tree-child-${stamp}`;
    const model = `e2e-tree-model-${stamp}`;
    let parentId: number | null = null;
    let childId: number | null = null;
    let modelId: string | null = null;

    try {
      // Create the hierarchy through the real API so the browser starts with an
      // unopened folder, rather than a tree expanded by the creation flow.
      const parentResponse = await page.request.post("/api/v1/collections", {
        data: { name: parent },
      });
      expect(parentResponse.status()).toBe(201);
      parentId = (await parentResponse.json()).id;
      const childResponse = await page.request.post("/api/v1/collections", {
        data: { name: child, parent_id: parentId },
      });
      expect(childResponse.status()).toBe(201);
      childId = (await childResponse.json()).id;
      await uploadModel(page, model);
      const href = await modelCard(page, model).getAttribute("href");
      const id = href?.match(/\/models\/(\d+)/)?.[1];
      if (!id) throw new Error("Missing Model card destination");
      modelId = id;

      // The first visit fetches only the root level. Expanding the parent must
      // reveal its child without requesting the deprecated whole-tree endpoint.
      const wholeTreeRequests: string[] = [];
      page.on("request", (request) => {
        if (new URL(request.url()).pathname === "/api/v1/collections") {
          wholeTreeRequests.push(request.url());
        }
      });
      await page.goto("/");
      const sidebar = page.locator("aside");
      const parentRow = sidebar.getByRole("treeitem", { name: parent, exact: true });
      const childRow = sidebar.getByRole("treeitem", { name: child, exact: true });
      await expect(parentRow).toBeVisible();
      await expect(childRow).toHaveCount(0);
      await parentRow.focus();
      await parentRow.press("ArrowRight");
      await expect(childRow).toBeVisible();

      await openLibraryTools(page);

      await modelCard(page, model).hover();
      await page.getByRole("checkbox", { name: `Select ${model}` }).check();
      await page.getByRole("button", { name: "Move", exact: true }).click();
      const moveDialog = page.getByRole("dialog", { name: /^Move \d+ item/ });
      await moveDialog.getByPlaceholder("Find destination").fill(child);
      await moveDialog.getByRole("option", { name: new RegExp(`^${parent}/${child} `) }).click();
      await moveDialog.getByRole("button", { name: "Move here" }).click();

      await childRow.click();
      await expect(modelCard(page, model)).toBeVisible();
      const moved = await page.request.get(`/api/v1/models/${modelId}`);
      expect(moved.status()).toBe(200);
      expect((await moved.json()).collection).toBe(`${parent}/${child}`);
      expect(wholeTreeRequests).toEqual([]);
    } finally {
      // A search result is not an ownership record. Clean up the exact Model
      // we created, even if a later name-filtered search omits it.
      if (modelId !== null) {
        expect((await page.request.delete(`/api/v1/models/${modelId}`)).status()).toBe(204);
        expect((await page.request.delete(`/api/v1/models/${modelId}/purge`)).status()).toBe(200);
      }
      if (childId !== null) {
        expect((await page.request.delete(`/api/v1/collections/${childId}`)).status()).toBe(204);
      }
      if (parentId !== null) {
        expect((await page.request.delete(`/api/v1/collections/${parentId}`)).status()).toBe(204);
      }
    }
  });
});
