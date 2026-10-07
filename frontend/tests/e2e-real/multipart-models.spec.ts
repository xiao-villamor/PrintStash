/** Multipart groupings link existing Models without taking ownership of their files. */
import type { MultipartModelRead } from "../../src/types";
import { openFilters, openLibraryTools } from "./util";
import { test, expect } from "./helpers";
import { createCollectionViaVault, modelCard, uploadModel } from "./util";

test.describe("multipart models", () => {
  test("preserves Models plus G-code after grouping deletion", async ({ page }) => {
    const stamp = Date.now();
    const base = `e2e-base-${stamp}`;
    const short = `e2e-handle-short-${stamp}`;
    const long = `e2e-handle-long-${stamp}`;
    const group = `e2e-multipart-${stamp}`;

    await uploadModel(page, base, { mesh: true, gcode: true });
    await uploadModel(page, short, { mesh: true, gcode: true });
    await uploadModel(page, long, { mesh: true, gcode: true });

    await page.goto("/");
    await openLibraryTools(page);
    await page.getByRole("button", { name: "New multipart set" }).first().click();
    await page.getByLabel("Name", { exact: true }).fill(group);
    await page.getByRole("button", { name: "Create multipart set" }).click();

    await page.getByRole("button", { name: "Add a part" }).click();
    await page.getByRole("button", { name: new RegExp(base) }).click();
    await page.getByRole("button", { name: "Add parts (1)" }).click();
    await page.getByRole("button", { name: "Add another part" }).click();
    await page.getByRole("button", { name: new RegExp(short) }).click();
    await page.getByRole("button", { name: "Add parts (1)" }).click();
    await page.locator("fieldset").nth(1).getByRole("button", { name: "Add variant" }).click();
    await page.getByRole("button", { name: new RegExp(long) }).click();
    await page.getByRole("button", { name: "Add variants (1)" }).click();
    await page
      .locator('input[type="file"][accept="image/png,image/jpeg,image/webp"]')
      .setInputFiles({
        name: "multipart-cover.png",
        mimeType: "image/png",
        buffer: Buffer.from(
          "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAYAAADED76LAAAAFklEQVR4nGOM6rn0nwEPYMInOXwUAADOOgLHyCTqtwAAAABJRU5ErkJggg==",
          "base64",
        ),
      });
    await expect(page.getByText("Uploaded from your computer")).toBeVisible();
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("Changes saved")).toBeVisible();
    const uploadedCover = page.getByRole("img", { name: group });
    await expect(uploadedCover).toBeVisible();
    await expect
      .poll(() =>
        uploadedCover.evaluate((element) =>
          element instanceof HTMLImageElement ? element.naturalWidth : 0,
        ),
      )
      .toBeGreaterThan(0);
    await expect(page.getByText("Choose one").first()).toBeVisible();

    await page.getByRole("button", { name: "Edit multipart set" }).click();
    await page.locator('input[type="file"][accept*=".pdf"]').setInputFiles({
      name: "assembly.md",
      mimeType: "text/markdown",
      buffer: Buffer.from("# Assembly\n\nFit the base before the handle."),
    });
    await expect(page.getByRole("link", { name: "assembly" })).toBeVisible();

    await page.goto("/");
    await expect(page.getByRole("link", { name: group })).toBeVisible();
    await page.getByRole("button", { name: `Add ${group} to favorites` }).click();
    await page.getByRole("button", { name: `Add tags to ${group}` }).click();
    const tagsDialog = page.getByRole("dialog");
    const setTag = `assembly-${stamp}`;
    await tagsDialog.getByRole("textbox", { name: "Tags to add" }).fill(setTag);
    await tagsDialog.getByRole("button", { name: "Create tag" }).click();
    await tagsDialog.getByRole("button", { name: "Save tags" }).click();
    await expect(page.getByText(setTag.toUpperCase())).toBeVisible();
    await expect(modelCard(page, base)).toBeVisible();
    await openFilters(page);
    await page
      .locator("aside")
      .getByRole("button", { name: "Multipart sets only", exact: true })
      .click();
    await expect(page).toHaveURL(/type=multipart/);
    await expect(modelCard(page, base)).toHaveCount(0);
    await page.goto("/?favorites=true");
    await expect(page.getByRole("link", { name: group })).toBeVisible();
    await page.goto("/?type=all");
    await expect(modelCard(page, base)).toBeVisible();
    await expect(modelCard(page, short)).toBeVisible();
    await expect(modelCard(page, long)).toBeVisible();

    await page.getByRole("link", { name: group }).click();
    const aggregateUrl = page.url();
    await page.getByRole("link", { name: new RegExp(short) }).click();
    await page.getByRole("tab", { name: "Revisions" }).click();
    await expect(page.getByText("Rev 1", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Recommended", { exact: true }).first()).toBeVisible();

    await page.goto(aggregateUrl);
    await page.getByRole("button", { name: "Edit multipart set" }).click();
    await expect(page.getByText("Uploaded from your computer")).toBeVisible();
    await page.getByRole("button", { name: "Delete multipart set" }).click();
    await expect(page.getByRole("dialog")).toContainText("Models, files and revisions stay");
    await page.getByRole("button", { name: "Delete set" }).click();
    await expect(page).toHaveURL(/\?type=all/);
    await expect(modelCard(page, base)).toBeVisible();
    await expect(modelCard(page, short)).toBeVisible();
    await expect(modelCard(page, long)).toBeVisible();
  });
  test("keeps a shared Model independently accessible", async ({ page }) => {
    const stamp = Date.now();
    const base = `e2e-shared-model-${stamp}`;
    const group = `e2e-first-set-${stamp}`;
    const secondGroup = `e2e-second-set-${stamp}`;
    await uploadModel(page, base, { mesh: true, gcode: true });

    await openLibraryTools(page);
    await page.getByRole("button", { name: "New multipart set" }).first().click();
    await expect(page.getByRole("dialog")).toContainText(
      "Adding a part references an existing Model. It remains reusable and can always be found in Everything.",
    );
    await page.getByLabel("Name", { exact: true }).fill(group);
    await page.getByRole("button", { name: "Create multipart set" }).click();
    await page.getByRole("button", { name: "Add a part" }).click();
    await page.getByRole("button", { name: new RegExp(base) }).click();
    await page.getByRole("button", { name: "Add parts (1)" }).click();
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("Changes saved")).toBeVisible();

    await page.goto("/?type=all");
    await openLibraryTools(page);
    await page.getByRole("button", { name: "New multipart set" }).first().click();
    await page.getByLabel("Name", { exact: true }).fill(secondGroup);
    await page.getByRole("button", { name: "Create multipart set" }).click();
    await page.getByRole("button", { name: "Add a part" }).click();
    await page.getByRole("button", { name: new RegExp(base) }).click();
    await page.getByRole("button", { name: "Add parts (1)" }).click();
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("Changes saved")).toBeVisible();

    await page.goto("/?type=all");
    await expect(modelCard(page, base)).toHaveCount(1);
    await expect(page.getByRole("link", { name: group, exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: secondGroup, exact: true })).toBeVisible();
    await modelCard(page, base).click();
    await expect(page.getByRole("heading", { name: base, exact: true })).toBeVisible();
    await page.getByRole("tab", { name: "Revisions" }).click();
    await expect(page.getByText("Rev 1", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Recommended", { exact: true }).first()).toBeVisible();
    await page.goBack();
    await expect(page).toHaveURL(/type=all/);
    await expect(modelCard(page, base)).toHaveCount(1);

    await openFilters(page);
    await page
      .locator("aside")
      .getByRole("button", { name: "Multipart sets only", exact: true })
      .click();
    await expect(page).toHaveURL(/type=multipart/);
    await expect(modelCard(page, base)).toHaveCount(0);
    await expect(page.getByRole("link", { name: secondGroup, exact: true })).toBeVisible();
    await page.getByRole("link", { name: group, exact: true }).click();
    const detailUrl = page.url();
    await expect(page.getByRole("heading", { name: group, exact: true })).toBeVisible();
    await page.goBack();
    await expect(page).toHaveURL(/type=multipart/);
    await expect(page.getByRole("link", { name: group, exact: true })).toBeVisible();
    await expect(modelCard(page, base)).toHaveCount(0);
    await page.goForward();
    await expect(page).toHaveURL(detailUrl);
    await expect(page.getByRole("heading", { name: group, exact: true })).toBeVisible();

    await page.getByRole("button", { name: "Edit multipart set" }).click();
    await page.getByRole("button", { name: "Delete multipart set" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Delete set" }).click();
    await page.getByRole("link", { name: secondGroup, exact: true }).click();
    await page.getByRole("button", { name: "Edit multipart set" }).click();
    await page.getByRole("button", { name: "Delete multipart set" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Delete set" }).click();
    await page.goto("/?type=all");
    await expect(modelCard(page, base)).toHaveCount(1);
  });
  test("builds multiple parts while browsing collections", async ({ page }) => {
    const stamp = Date.now();
    const folder = `picker-${stamp}`;
    const first = `base-${stamp}`;
    const second = `body-${stamp}`;
    const group = `kit-${stamp}`;

    await createCollectionViaVault(page, folder);
    await uploadModel(page, first, { collection: folder });
    await uploadModel(page, second, { collection: folder });
    await page.goto("/");
    await openLibraryTools(page);
    await page.getByRole("button", { name: "New multipart set" }).first().click();
    await page.getByLabel("Name", { exact: true }).fill(group);
    await page.getByRole("button", { name: "Create multipart set" }).click();
    await page.getByRole("button", { name: "Add a part" }).click();
    const picker = page.getByRole("dialog", { name: "Choose existing models" });
    await picker.getByRole("button", { name: folder, exact: true }).click();
    await picker.getByRole("button", { name: new RegExp(first) }).click();
    await picker.getByRole("textbox", { name: "Search existing models" }).fill(second);
    await picker.getByRole("button", { name: new RegExp(second) }).click();
    await picker.getByRole("button", { name: "Add parts (2)" }).click();
    await expect(page.getByLabel("Part name", { exact: true })).toHaveCount(2);
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("Changes saved")).toBeVisible();
    await page.reload();
    await expect(page.getByRole("link", { name: new RegExp(first) })).toBeVisible();
    await expect(page.getByRole("link", { name: new RegExp(second) })).toBeVisible();

    await page.getByRole("button", { name: "Edit multipart set" }).click();
    await page.getByRole("button", { name: "Delete multipart set" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Delete set" }).click();
    await page.goto(`/?c=${folder}`);
    const sidebar = page.locator("aside");
    const label = sidebar.getByRole("button", { name: folder, exact: true });
    await label.hover();
    await label.locator("xpath=following-sibling::button[@title='Delete collection']").click();
    await sidebar.getByRole("button", { name: "Delete", exact: true }).click();
    await expect(label).toHaveCount(0);
  });
});

test.describe("Multipart auxiliary editing", () => {
  test("recovers auxiliary conflicts without rebasing the composition draft", async ({ page }) => {
    const api = `http://127.0.0.1:${process.env.PLAYWRIGHT_REAL_API_PORT ?? 8410}`;
    const name = `e2e-multipart-review-${Date.now()}`;
    const created = await page.request.post(`${api}/api/v1/multipart-models`, {
      data: { name, description: null, collection_id: null },
    });
    expect(created.status(), await created.text()).toBe(201);
    const initial: MultipartModelRead = await created.json();
    const path = `/api/v1/multipart-models/${initial.id}`;
    try {
      // ── Open a composition draft, then race a second editor's tag write ──
      await page.goto(`/multipart-models/${initial.id}`);
      await page.getByRole("button", { name: "Edit multipart set" }).click();
      await page.getByRole("textbox", { name: "Name", exact: true }).fill("My unsaved composition");
      await page.getByRole("button", { name: "Add tags" }).click();
      // The dialog label reflects the composition draft's title at opening.
      const tagDialog = page.getByRole("dialog");
      await tagDialog.getByLabel("Tags to add").fill("Local review tag");
      await tagDialog.getByRole("button", { name: "Create tag" }).click();
      const competing = await page.request.put(`${api}${path}/tags`, {
        headers: {
          "If-Match": `"multipart-${initial.id}-v${initial.edit_version}"`,
          "X-PrintStash-Edit-Contract": "conditional-v1",
        },
        data: { tags: ["Other editor tag"] },
      });
      expect(competing.ok(), await competing.text()).toBe(true);
      const latest: MultipartModelRead = await competing.json();
      const tagConflict = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname === `${path}/tags` &&
          response.request().method() === "PUT",
      );
      await tagDialog.getByRole("button", { name: "Save tags" }).click();
      expect((await tagConflict).status()).toBe(412);
      await expect(tagDialog.getByRole("button", { name: "Save tags" })).toBeDisabled();
      await tagDialog.getByRole("button", { name: "Review latest version" }).click();
      await expect(tagDialog.getByRole("region", { name: "Latest saved version" })).toContainText(
        "Other editor tag",
      );
      const tagConfirmed = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname === `${path}/tags` &&
          response.request().method() === "PUT",
      );
      await tagDialog.getByRole("button", { name: "Save my draft against this version" }).click();
      const tagReceipt = await tagConfirmed;
      expect(tagReceipt.ok()).toBe(true);
      expect(await tagReceipt.request().headerValue("If-Match")).toBe(
        `"multipart-${initial.id}-v${latest.edit_version}"`,
      );
      await expect(tagDialog).toHaveCount(0);
      await expect(page.getByRole("textbox", { name: "Name", exact: true })).toHaveValue(
        "My unsaved composition",
      );

      // ── The old composition base also requires explicit cover review ──
      const coverConflict = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname === `${path}/cover` &&
          response.request().method() === "PUT",
      );
      await page.getByLabel("Upload image", { exact: true }).setInputFiles({
        name: "reviewed-cover.png",
        mimeType: "image/png",
        buffer: Buffer.from(
          "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAYAAADED76LAAAAFklEQVR4nGOM6rn0nwEPYMInOXwUAADOOgLHyCTqtwAAAABJRU5ErkJggg==",
          "base64",
        ),
      });
      expect((await coverConflict).status()).toBe(412);
      await page.getByRole("button", { name: "Review latest version" }).click();
      const coverConfirmed = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname === `${path}/cover` &&
          response.request().method() === "PUT",
      );
      await page
        .getByRole("dialog")
        .getByRole("button", { name: "Save my draft against this version" })
        .click();
      const coverReceipt = await coverConfirmed;
      expect(coverReceipt.ok()).toBe(true);
      const tagged: MultipartModelRead = await tagReceipt.json();
      expect(await coverReceipt.request().headerValue("If-Match")).toBe(
        `"multipart-${initial.id}-v${tagged.edit_version}"`,
      );
      await expect(page.getByRole("dialog")).toHaveCount(0);
      await expect(page.getByText("Uploaded from your computer")).toBeVisible();

      // ── Auxiliary confirmation never silently rebases the composition ──
      const compositionConflict = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname === path && response.request().method() === "PUT",
      );
      await page.getByRole("button", { name: "Save changes" }).click();
      const rejected = await compositionConflict;
      expect(rejected.status()).toBe(412);
      expect(await rejected.request().headerValue("If-Match")).toBe(
        `"multipart-${initial.id}-v${initial.edit_version}"`,
      );
      await expect(page.getByRole("textbox", { name: "Name", exact: true })).toHaveValue(
        "My unsaved composition",
      );
      const persisted: MultipartModelRead = await (await page.request.get(`${api}${path}`)).json();
      expect(persisted.name).toBe(name);
      expect(persisted.tags).toEqual(["Local review tag"]);
      expect(persisted.cover_image_uploaded).toBe(true);
      await page.reload();
      await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
    } finally {
      const removed = await page.request.delete(`${api}${path}`);
      expect(removed.ok(), await removed.text()).toBe(true);
    }
  });
});
