/** Collection card context actions persist through the real API. */
import { randomUUID } from "node:crypto";
import { expect, test } from "./helpers";
import { collapseNavigationSidebar, stlFor } from "./util";

const coverImage = Buffer.from(
  "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000a49444154789c6360000002000154a24f6f0000000049454e44ae426082",
  "hex",
);

test.describe("collection card", () => {
  test("renames a collection from its card", async ({ page }) => {
    const original = `e2e-rename-${randomUUID().slice(0, 8)}`;
    const renamed = `${original}-renamed`;
    const created = await page.request.post("/api/v1/collections", { data: { name: original } });
    expect(created.status()).toBe(201);
    const { id } = await created.json();

    try {
      await page.goto("/");
      await collapseNavigationSidebar(page);
      const card = page.locator(`[data-collection-path="${original}"]`);
      await card.getByRole("button").first().click({ button: "right" });
      const menu = page.getByRole("menu", { name: "Collection actions" });
      await menu.getByRole("menuitem", { name: "Rename" }).click();
      const input = card.getByRole("textbox", { name: "Collection name", exact: true });
      await expect(input).toBeFocused();
      await expect(page.getByRole("dialog")).toHaveCount(0);
      await input.fill(`${renamed}-cancelled`);
      await input.press("Escape");
      await expect(card.getByRole("button").first()).toBeFocused();
      await card.getByRole("button").first().click({ button: "right" });
      await menu.getByRole("menuitem", { name: "Rename" }).click();
      await expect(input).toHaveValue(original);
      await input.fill(renamed);
      await input.press("Enter");
      await expect(input).toBeHidden();
      await page.reload();
      await expect(page.locator(`[data-collection-path="${renamed}"]`)).toBeVisible();
      await expect(page).toHaveURL((url) => !url.searchParams.has("c"));
    } finally {
      await page.request.delete(`/api/v1/collections/${id}`);
    }
  });

  test("persists collection cover choices from its card", async ({ page }) => {
    const suffix = randomUUID().slice(0, 8);
    const original = `e2e-cover-${suffix}`;
    const name = `cover-model-${suffix}`;
    const created = await page.request.post("/api/v1/collections", { data: { name: original } });
    expect(created.status()).toBe(201);
    const { id } = await created.json();
    const coverUrl = `/api/v1/collections/${id}/cover`;

    try {
      const imported = await page.request.post("/api/v1/ingest/model", {
        multipart: {
          file: {
            name: `${name}.stl`,
            mimeType: "text/plain",
            buffer: Buffer.from(stlFor(name)),
          },
          model_name: name,
          collection: original,
        },
      });
      expect(imported.status(), await imported.text()).toBe(202);
      const { job_id } = await imported.json();
      let modelId: number | undefined;
      await expect
        .poll(
          async () => {
            const job = await (await page.request.get(`/api/v1/jobs/${job_id}`)).json();
            modelId = job.model_id;
            return job.state;
          },
          { timeout: 45_000 },
        )
        .toBe("completed");
      expect(modelId).toBeGreaterThan(0);

      await page.goto("/");
      await collapseNavigationSidebar(page);
      const card = page.locator(`[data-collection-path="${original}"]`);
      const menu = page.getByRole("menu", { name: "Collection actions" });
      const cardButton = card.getByRole("button").first();

      const cover = page.getByRole("dialog", { name: "Edit collection cover" });
      await cardButton.focus();
      await cardButton.press("Shift+F10");
      await menu.getByRole("menuitem", { name: "Edit cover" }).press("Enter");
      await cover.getByRole("tab", { name: "Choose models" }).click();
      const preview = cover.getByRole("region", { name: "Cover preview" });
      await expect(preview.getByRole("status")).toHaveText("0 of 4 selected");
      const search = cover.getByRole("searchbox", { name: "Search models" });
      await search.fill(name);
      await cover.getByRole("checkbox", { name, exact: true }).check();
      await expect(preview.getByRole("status")).toHaveText("1 of 4 selected");
      await search.fill("no model matches this search");
      await expect(cover.getByText("No models found")).toBeVisible();
      await expect(preview.getByRole("status")).toHaveText("1 of 4 selected");
      await cover.getByRole("button", { name: "Save cover", exact: true }).click();
      await expect(cover).toBeHidden();
      await page.reload();
      expect(await (await page.request.get(coverUrl)).json()).toEqual({
        kind: "models",
        model_ids: [modelId],
      });

      await cardButton.click({ button: "right" });
      await menu.getByRole("menuitem", { name: "Edit cover" }).click();
      await expect(cover.getByRole("checkbox", { name, exact: true })).toBeChecked();
      await cover.getByRole("tab", { name: "Upload image" }).click();
      await cover.getByLabel("Cover image").setInputFiles({
        name: "cover.png",
        mimeType: "image/png",
        buffer: coverImage,
      });
      await cover.getByRole("button", { name: "Save cover", exact: true }).click();
      await expect(cover).toBeHidden();
      await page.reload();
      expect(await (await page.request.get(coverUrl)).json()).toMatchObject({ kind: "image" });
      const previewImage = card.locator("[data-library-preview] img");
      await expect
        .poll(() =>
          previewImage.evaluate((img) => img instanceof HTMLImageElement && img.naturalWidth),
        )
        .toBe(1);

      await cardButton.click({ button: "right" });
      await menu.getByRole("menuitem", { name: "Edit cover" }).click();
      await cover.getByRole("tab", { name: "Automatic" }).click();
      await cover.getByRole("button", { name: "Save cover", exact: true }).click();
      await expect(cover).toBeHidden();
      expect(await (await page.request.get(coverUrl)).json()).toEqual({ kind: "automatic" });
    } finally {
      await page.request.delete(`/api/v1/collections/${id}?recursive=true`);
    }
  });
});
