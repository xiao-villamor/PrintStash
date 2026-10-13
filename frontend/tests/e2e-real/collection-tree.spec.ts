/** A nested destination appears through the lazy sidebar before a real move persists. */
import { test, expect } from "./helpers";
import { modelCard, openLibraryTools, uploadModel } from "./util";

test.describe("collection tree", () => {
  test("moves a Model into a newly expanded nested folder", async ({ page }) => {
    const stamp = Date.now();
    const parent = `e2e-tree-parent-${stamp}`;
    const child = `e2e-tree-child-${stamp}`;
    const model = `e2e-tree-model-${stamp}`;
    let parentId: number | null = null;
    let childId: number | null = null;

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
      const parentRow = sidebar.getByRole("button", { name: parent, exact: true });
      const childRow = sidebar.getByRole("button", { name: child, exact: true });
      await expect(parentRow).toBeVisible();
      await expect(childRow).toHaveCount(0);
      await parentRow.locator("xpath=preceding-sibling::button[@aria-label='Expand']").click();
      await expect(childRow).toBeVisible();

      await openLibraryTools(page);
      await page.getByRole("button", { name: "Select", exact: true }).click();
      await page.getByRole("checkbox", { name: `Select ${model}` }).check();
      await page.locator("div.fixed.bottom-4").getByRole("button", { name: "Move" }).click();
      const moveDialog = page.getByRole("dialog", { name: /^Move \d+ item/ });
      await moveDialog.getByPlaceholder("Find destination").fill(child);
      await moveDialog.getByRole("option", { name: new RegExp(`^${parent}/${child} `) }).click();
      await moveDialog.getByRole("button", { name: "Move here" }).click();

      await childRow.click();
      await expect(modelCard(page, model)).toBeVisible();
      const models = await (await page.request.get(`/api/v1/models?q=${model}`)).json();
      expect(models.find((item: { name: string }) => item.name === model)?.collection).toBe(
        `${parent}/${child}`,
      );
      expect(wholeTreeRequests).toEqual([]);
    } finally {
      const models = await (await page.request.get(`/api/v1/models?q=${model}`)).json();
      const created = models.find((item: { name: string }) => item.name === model);
      if (created) {
        expect((await page.request.delete(`/api/v1/models/${created.id}`)).status()).toBe(204);
        expect((await page.request.delete(`/api/v1/models/${created.id}/purge`)).status()).toBe(
          200,
        );
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
