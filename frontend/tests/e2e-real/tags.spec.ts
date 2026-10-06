/**
 * Deleting a tag that models are actually using.
 *
 * The confirmation step is the point. Removing a tag from the system removes it from
 * every model that carries it, which is not what "delete" looks like from a single model's
 * edit form — so the flow asks first, and this test is what keeps it asking.
 */
import { test, expect } from "./helpers";
import { clickModelAction, modelCard, uploadModel } from "./util";

test.describe("tags", () => {
  test("assigns a newly created tag from the model card", async ({ page }) => {
    const tag = `e2e-quick-tag-${Date.now()}`;
    const model = `e2e-quick-tagged-${Date.now()}`;

    await uploadModel(page, model);
    await page.getByRole("button", { name: `Add tags to ${model}` }).click();
    const dialog = page.getByRole("dialog", { name: "Model tags" });
    await dialog.getByLabel("Search or create a tag").fill(tag);
    await dialog.getByRole("option", { name: /Create tag/ }).click();
    await dialog.getByRole("button", { name: "Save tags" }).click();

    await expect(modelCard(page, model)).toContainText(tag);
  });

  test("reviews a conflicting quick-tag draft before replacing current tags", async ({ page }) => {
    const stamp = Date.now();
    const name = `e2e-tag-conflict-${stamp}`;
    const draftTag = `draft-${stamp}`;
    const remoteTag = `remote-${stamp}`;
    await uploadModel(page, name);
    const href = await modelCard(page, name).getAttribute("href");
    expect(href).toMatch(/^\/models\/\d+/);
    const id = Number(href?.match(/^\/models\/(\d+)/)?.[1]);
    expect(Number.isSafeInteger(id)).toBe(true);
    try {
      await page.getByRole("button", { name: `Add tags to ${name}` }).click();
      const dialog = page.getByRole("dialog", { name: "Model tags" });
      await dialog.getByLabel("Search or create a tag").fill(draftTag);
      await dialog.getByRole("option", { name: /Create tag/ }).click();
      const before = await (await page.request.get(`/api/v1/models/${id}`)).json();
      const competing = await page.request.patch(`/api/v1/models/${id}`, {
        headers: {
          "If-Match": `"model-${id}-v${before.edit_version}"`,
          "X-PrintStash-Edit-Contract": "conditional-v1",
        },
        data: { tags: [remoteTag] },
      });
      expect(competing.ok()).toBe(true);
      const latest = await competing.json();
      await dialog.getByRole("button", { name: "Save tags" }).click();
      await expect(dialog.getByRole("button", { name: "Save tags" })).toBeDisabled();
      await expect(dialog.getByRole("button", { name: `Remove ${draftTag}` })).toBeVisible();
      await dialog.getByRole("button", { name: "Review latest version" }).click();
      await expect(dialog.getByText(remoteTag, { exact: true })).toBeVisible();
      const savedResponse = page.waitForResponse(
        (response) =>
          response.url().endsWith("/models/batch/tags") && response.request().method() === "POST",
      );
      await dialog.getByRole("button", { name: "Save my draft against this version" }).click();
      const saved = await savedResponse;
      expect(saved.ok()).toBe(true);
      expect(saved.request().postDataJSON().expected_versions).toEqual({
        [id]: latest.edit_version,
      });
      await expect(dialog).toHaveCount(0);
      const persisted = await (await page.request.get(`/api/v1/models/${id}`)).json();
      expect(persisted.tags).toEqual([draftTag]);
      expect(persisted.edit_version).toBeGreaterThan(latest.edit_version);
    } finally {
      expect((await page.request.delete(`/api/v1/models/${id}`)).ok()).toBe(true);
    }
  });

  test("delete an assigned tag from model editing (with confirm)", async ({ page }) => {
    const tag = `e2e-assigned-${Date.now()}`;
    const model = `e2e-tagged-${Date.now()}`;

    await uploadModel(page, model, { tag });
    await modelCard(page, model).click();
    await clickModelAction(page, "Edit details");
    await page.getByRole("button", { name: `Remove ${tag}` }).click();
    await page.getByPlaceholder("Search or create — press Enter").fill(tag);
    const del = page.getByRole("button", { name: `Delete tag ${tag}` });
    await expect(del).toBeVisible();

    await del.click();
    await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
    await expect(del).toHaveCount(0);
  });
});
