/** Collection card context actions persist through the real API. */
import { randomUUID } from "node:crypto";
import { expect, test } from "./helpers";
import { collapseNavigationSidebar, modelCard, stlFor, uploadModel } from "./util";
import type { DerivativeRead, ModelRead } from "../../src/types/models";

const coverImage = Buffer.from(
  "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000a49444154789c6360000002000154a24f6f0000000049454e44ae426082",
  "hex",
);

test.describe("collection card", () => {
  test("moves a gallery card into a collection using a native drag", async ({ page }) => {
    const name = `e2e-gallery-move-${randomUUID().slice(0, 8)}`;
    const folder = `${name}-folder`;
    const created = await page.request.post("/api/v1/collections", { data: { name: folder } });
    expect(created.status()).toBe(201);
    const { id } = await created.json();
    let modelId: string | undefined;
    try {
      await uploadModel(page, name);
      const source = modelCard(page, name);
      const href = await source.getAttribute("href");
      modelId = href?.match(/\/models\/(\d+)/)?.[1];
      if (!modelId) throw new Error("Missing Model card destination");
      await collapseNavigationSidebar(page);

      const target = page.getByRole("main").locator(`[data-collection-path="${folder}"]`);
      await source.locator("[data-model-preview]").dragTo(target);
      await expect(page.getByText("Moved", { exact: true })).toBeVisible();
      const moved = await page.request.get(`/api/v1/models/${modelId}`);
      expect(moved.status()).toBe(200);
      expect((await moved.json()).collection).toBe(folder);

      await target.getByRole("button", { name: new RegExp(`^${folder}\\b`) }).click();
      await page.reload();
      await expect(modelCard(page, name)).toBeVisible();
    } finally {
      if (modelId) {
        expect((await page.request.delete(`/api/v1/models/${modelId}`)).status()).toBe(204);
        expect((await page.request.delete(`/api/v1/models/${modelId}/purge`)).status()).toBe(200);
      }
      expect((await page.request.delete(`/api/v1/collections/${id}`)).status()).toBe(204);
    }
  });

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

  test("recreates a model thumbnail from the collection context menu", async ({ page }) => {
    const name = `e2e-thumbnails-${randomUUID().slice(0, 8)}`;
    const created = await page.request.post("/api/v1/collections", { data: { name } });
    expect(created.status()).toBe(201);
    const collection = await created.json();
    const imported = await page.request.post("/api/v1/ingest/model", {
      multipart: {
        file: { name: `${name}.stl`, mimeType: "text/plain", buffer: Buffer.from(stlFor(name)) },
        model_name: name,
        collection: name,
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
    const readModel = async (): Promise<ModelRead> =>
      (await page.request.get(`/api/v1/models/${modelId}`)).json();
    const readThumbnail = async () => {
      const model = await readModel();
      const file = model.files[0];
      const derivatives: DerivativeRead[] = await (
        await page.request.get(`/api/v1/files/${file.id}/derivatives`)
      ).json();
      const thumbnail = derivatives.find((item) => item.kind === "thumbnail");
      return thumbnail?.state === "ready" ? thumbnail.updated_at : null;
    };
    try {
      await expect.poll(readThumbnail, { timeout: 45_000 }).toBeTruthy();
      const before = await readThumbnail();
      await page.goto("/");
      await collapseNavigationSidebar(page);
      const card = page.locator(`[data-collection-path="${name}"]`);
      const button = card.getByRole("button").first();
      await button.click({ button: "right" });
      const menu = page.getByRole("menu", { name: "Collection actions" });
      const queued = page.waitForResponse(
        (response) =>
          response.url().endsWith(`/collections/${collection.id}/thumbnails/regenerate`) &&
          response.request().method() === "POST",
      );
      await menu.getByRole("menuitem", { name: "Recreate thumbnails" }).click();
      expect((await queued).status()).toBe(202);
      expect(await (await queued).json()).toEqual({ queued: 1 });
      await expect(button).toBeFocused();
      await expect(page.getByText("1 thumbnail queued")).toBeVisible();
      await expect
        .poll(
          async () => {
            const thumbnail = await readThumbnail();
            return Boolean(thumbnail && thumbnail !== before);
          },
          { timeout: 45_000 },
        )
        .toBe(true);
      await page.reload();
      const thumbnailUrl = (await readModel()).thumbnail_url;
      expect(thumbnailUrl).toBeTruthy();
      expect((await page.request.get(thumbnailUrl!)).ok()).toBe(true);
    } finally {
      await page.request.delete(`/api/v1/models/${modelId}`);
      await page.request.delete(`/api/v1/collections/${collection.id}`);
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
      const save = cover.getByRole("button", { name: "Save cover", exact: true });
      await expect(save).toBeDisabled();
      await cover.getByLabel("Cover image").setInputFiles({
        name: "not-an-image.txt",
        mimeType: "text/plain",
        buffer: Buffer.from("not an image"),
      });
      await expect(cover.getByRole("alert")).toContainText("Choose a PNG");
      await expect(save).toBeDisabled();
      await cover.getByLabel("Cover image").setInputFiles({
        name: "corrupt.png",
        mimeType: "image/png",
        buffer: Buffer.from("invalid PNG"),
      });
      await expect(cover.getByRole("alert")).toHaveText("This image couldn’t be opened.");
      await expect(save).toBeDisabled();
      const choose = cover.getByRole("button", { name: "Choose image", exact: true });
      await choose.focus();
      const [chooser] = await Promise.all([
        page.waitForEvent("filechooser"),
        choose.press("Enter"),
      ]);
      await chooser.setFiles({
        name: "cover.png",
        mimeType: "image/png",
        buffer: coverImage,
      });
      await expect(preview.getByRole("img", { name: "New cover image" })).toBeVisible();
      await expect(preview.getByRole("status")).toContainText("cover.png");
      await expect(save).toBeEnabled();
      await preview.getByRole("button", { name: "Clear image" }).click();
      await expect(choose).toBeFocused();
      await expect(save).toBeDisabled();

      const dropped = await page.evaluateHandle((bytes) => {
        const transfer = new DataTransfer();
        transfer.items.add(
          new File([new Uint8Array(bytes)], "dropped-cover.png", { type: "image/png" }),
        );
        return transfer;
      }, Array.from(coverImage));
      await preview.dispatchEvent("drop", { dataTransfer: dropped });
      await dropped.dispose();
      await expect(preview.getByRole("status")).toContainText("dropped-cover.png");
      await save.click();
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
