/**
 * The model page: its history, its tags, and getting its files back out.
 *
 * The tag editor is the interesting one — Cancel must discard and Save must persist, and
 * a form that saves on Cancel silently rewrites somebody's library. The download test is
 * here because it goes through the browser's own save machinery, which nothing below this
 * tier can exercise. Heading quick rename must reach the same persistent editor and
 * keep cancelled name drafts out of the saved Model.
 */
import { test, expect } from "./helpers";
import { clickModelAction, modelCard, uploadGcodeModel } from "./util";

test.describe("model detail", () => {
  test("reads a Model's detail once when opening it", async ({ page }) => {
    const name = `e2e-single-read-${Date.now()}`;
    await uploadGcodeModel(page, name);
    // Derivatives of a fresh upload refresh the Model on their own; measure a settled one.
    await expect
      .poll(async () => {
        const jobs: { state: string }[] = await (await page.request.get("/api/v1/jobs")).json();
        return jobs.filter((job) => !["completed", "failed", "cancelled"].includes(job.state))
          .length;
      })
      .toBe(0);
    const acknowledged: string[] = [];
    page.on("websocket", (socket) =>
      socket.on("framereceived", (frame) => {
        const notice: { type?: string; channel?: string } = JSON.parse(String(frame.payload));
        if (notice.type === "subscribed" && notice.channel) acknowledged.push(notice.channel);
      }),
    );
    await page.goto("/");
    const card = modelCard(page, name);
    await expect(card).toBeVisible();
    await page.waitForLoadState("networkidle");
    const id = (await card.getAttribute("href"))?.match(/\/models\/(\d+)/)?.[1];
    if (!id) throw new Error("Missing Model card destination");
    // Answered reads: the development build's double mount cancels one request unanswered.
    let reads = 0;
    page.on("response", (response) => {
      if (new URL(response.url()).pathname === `/api/v1/models/${id}`) reads += 1;
    });
    acknowledged.length = 0;

    await card.click();
    await expect(page.getByRole("heading", { name })).toBeVisible();
    // The acknowledgement is what used to trigger a second read; wait for it, then for quiet.
    await expect.poll(() => acknowledged).toContain(`model:${id}`);
    await page.waitForLoadState("networkidle");

    expect(reads).toBe(1);
    expect((await page.request.delete(`/api/v1/models/${id}`)).status()).toBe(204);
  });

  test("warms a Model's detail while the pointer rests on its card", async ({ page }) => {
    const name = `e2e-open-intent-${Date.now()}`;
    await uploadGcodeModel(page, name);
    await page.goto("/");
    const card = modelCard(page, name);
    await expect(card).toBeVisible();
    await page.waitForLoadState("networkidle");
    const href = await card.getAttribute("href");
    const id = href?.match(/\/models\/(\d+)/)?.[1];
    if (!id) throw new Error("Missing Model card destination");

    const warmed = page.waitForResponse(
      (response) => new URL(response.url()).pathname === `/api/v1/models/${id}`,
    );
    await card.hover();

    expect((await warmed).status()).toBe(200);
    await expect(page).toHaveURL(/\/(\?.*)?$/);
    expect((await page.request.delete(`/api/v1/models/${id}`)).status()).toBe(204);
  });

  test("quick-renames a Model from its detail heading", async ({ page }) => {
    const name = `e2e-quick-rename-${Date.now()}`;
    const renamed = `${name}-renamed`;
    await uploadGcodeModel(page, name);
    await modelCard(page, name).click();

    // ── Double-click the heading and persist the new name ──
    await page.getByRole("heading", { name, exact: true }).dblclick();
    const input = page.getByPlaceholder("Model name");
    await expect(input).toBeFocused();
    await expect(input).toHaveValue(name);
    await input.fill(renamed);
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("heading", { name: renamed, exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("heading", { name: renamed, exact: true })).toBeVisible();

    // ── Use the pen with the keyboard, then discard the draft ──
    const renameButton = page.getByRole("button", {
      name: `Rename ${renamed}`,
      exact: true,
    });
    await renameButton.focus();
    await renameButton.press("Enter");
    await expect(input).toBeFocused();
    await input.fill(`${renamed}-discarded`);
    const cancel = page.getByRole("button", { name: "Cancel", exact: true });
    await cancel.focus();
    await cancel.press("Enter");
    await expect(input).toHaveCount(0);
    await page.reload();
    await expect(page.getByRole("heading", { name: renamed, exact: true })).toBeVisible();

    // ── Remove the Model and its Artifacts from the shared test database ──
    await clickModelAction(page, "Delete model");
    await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
    await page.goto("/settings");
    await page.getByRole("button", { name: "Trash", exact: true }).click();
    await page
      .getByRole("checkbox", { name: `Select deleted model ${renamed}`, exact: true })
      .locator("..")
      .getByRole("button", { name: "Delete", exact: true })
      .click();
    await page.getByRole("button", { name: "Delete forever", exact: true }).click();
    await expect(page.getByText(renamed, { exact: true })).toHaveCount(0);
  });

  test("log a manual print against an ad-hoc printer; it shows in history", async ({ page }) => {
    const name = `e2e-hist-${Date.now()}`;
    const printerName = `E2E Rig ${Date.now()}`;
    await uploadGcodeModel(page, name);

    await modelCard(page, name).click();
    await expect(page.getByRole("heading", { name })).toBeVisible();
    await page.getByRole("tab", { name: /History/ }).click();
    await page.getByRole("button", { name: /Add Record/ }).click();

    // Free-text printer name path: pick "Other (not listed)…" and type a name.
    await page
      .locator("select")
      .filter({ has: page.getByRole("option", { name: /Other \(not listed\)/ }) })
      .selectOption("__adhoc__");
    await page.getByPlaceholder("e.g. Garage Prusa MK4").fill(printerName);

    // The G-code revision defaults to the model's only revision; result defaults
    // to Completed. Save and confirm the record renders.
    await page.getByRole("button", { name: /^Save$/ }).click();
    await expect(page.getByText(printerName)).toBeVisible();
  });

  test("edit a model's tags: Cancel discards, Save persists", async ({ page }) => {
    const stamp = Date.now();
    const name = `e2e-edit-${stamp}`;
    const dropTag = `e2e-drop-${stamp}`;
    const keepTag = `e2e-keep-${stamp}`;
    await uploadGcodeModel(page, name);

    await modelCard(page, name).click();
    await expect(page.getByRole("heading", { name })).toBeVisible();

    const tagInput = page.getByPlaceholder("Search or create — press Enter");

    // Cancel discards: inline-create a tag in edit mode, then cancel.
    await page.getByRole("button", { name: "Model actions" }).click();
    await page.getByRole("menuitem", { name: "Edit details" }).click();
    await tagInput.fill(dropTag);
    await tagInput.press("Enter");
    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByText(dropTag, { exact: true })).toHaveCount(0);

    // Save persists: inline-create a different tag and save — it shows as a chip.
    await page.getByRole("button", { name: "Model actions" }).click();
    await page.getByRole("menuitem", { name: "Edit details" }).click();
    await tagInput.fill(keepTag);
    await tagInput.press("Enter");
    await page.getByRole("button", { name: /^Save$/ }).click();
    await expect(page.getByText(keepTag, { exact: true })).toBeVisible();
  });

  test("download a G-code revision", async ({ page }) => {
    const name = `e2e-dl-${Date.now()}`;
    await uploadGcodeModel(page, name);

    await modelCard(page, name).click();
    await expect(page.getByRole("heading", { name })).toBeVisible();
    await page.getByRole("tab", { name: "Revisions" }).click();

    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByTitle("Download").first().click(),
    ]);
    expect(download.suggestedFilename()).toBe(`${name}.gcode`);
  });
});
