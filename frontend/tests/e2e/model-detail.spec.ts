/*
 * The model detail route, assembled — every tab, dialog and panel on one page.
 *
 * Against a mocked API this cannot prove the data is right, and that is not what
 * breaks. What breaks is the page: a route that 404s after a router change, a
 * details sidebar that forgets its width, a send dialog that outgrew its space,
 * a tab strip that overflows, a file picker that lost its labels. Each is a
 * property of the whole page and none of them need a real server to see.
 *
 * Two rows are about safety rather than layout. The source cover is private, so
 * it must load through the authenticated route rather than as a public URL; and
 * the print-history panel offers a download only for evidence we actually hold —
 * exact, partial and basic look alike in the DOM and mean different things.
 *
 * The minimum-width row exists because the details panel is resizable: readable
 * at the default width and unreadable at the smallest one is a bug users hit by
 * dragging, and nothing else would catch it.
 */
import { expect, test, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";

import { collectPageProblems, useMockApi } from "./_setup";
import type { ModelRead } from "../../src/types/models";
import { aDerivative, aMetadata, aModel, aRevision } from "../../src/test-support/factories";

useMockApi();

async function openToolpath(page: Page) {
  const gcode = await readFile(
    new URL("../../../backend/tests/fixtures/real_orca_ender3_benchy.gcode", import.meta.url),
    "utf8",
  );
  await page.route("**/api/v1/files/2/toolpath", (route) =>
    route.fulfill({ contentType: "text/plain", body: gcode }),
  );
  await page.goto("/models/1");
  const preview = page.getByRole("region", { name: "Preview", exact: true });
  await preview.getByRole("button", { name: "Layers", exact: true }).click();
  await expect(preview.getByRole("button", { name: "Screenshot", exact: true })).toBeEnabled();
  return preview;
}

test.describe("model detail route", () => {
  test("renders prototype card actions outside the navigation link", async ({ page }) => {
    const problems = await collectPageProblems(page);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.route("**/api/v1/models/1", async (route) => {
      const response = await route.fetch();
      const model: ModelRead = await response.json();
      model.files = model.files.map((file) =>
        file.file_type === "stl"
          ? {
              ...file,
              metadata: aMetadata({ bbox_x_mm: 210, bbox_y_mm: 110, bbox_z_mm: 40 }),
            }
          : file,
      );
      await route.fulfill({ response, json: model });
    });
    await page.goto("/");
    const link = page.getByRole("link", { name: "skadis_kitchen-roll_screw", exact: true });
    const card = link.locator("xpath=ancestor::article");
    await expect(card.getByText("210 × 110 × 40 mm", { exact: true })).toBeVisible();
    await expect(card.getByText("Rev 1", { exact: true })).toBeVisible();
    const thumbnail = link.getByRole("img", { name: "skadis_kitchen-roll_screw", exact: true });
    await expect(thumbnail).toBeVisible();
    const thumbnailScale = await thumbnail.evaluate(
      (image) =>
        image.getBoundingClientRect().width / image.parentElement!.getBoundingClientRect().width,
    );
    expect(thumbnailScale).toBeCloseTo(0.3, 2);
    await expect(card.getByText("OK 2× on ender", { exact: true })).toBeVisible();
    const print = card.getByRole("button", { name: "Print Rev 1", exact: true });
    const download = card.getByRole("button", { name: "Download Rev 1", exact: true });
    const printBox = await print.boundingBox();
    const downloadBox = await download.boundingBox();
    expect(printBox?.width).toBe(28);
    expect(downloadBox?.y).toBe(printBox?.y);
    expect(await print.evaluate((button) => button.closest("a"))).toBeNull();
    await expect(card.getByText("On printer", { exact: true })).toHaveCount(0);
    await print.click();
    await expect(
      page.getByRole("dialog", { name: "Send Rev 1 to printer", exact: true }),
    ).toBeVisible();
    await page.keyboard.press("Escape");
    const completed = page.waitForEvent("download");
    await download.click();
    expect((await completed).suggestedFilename()).toMatch(/\.gcode$/);
    await expect(page).toHaveURL((url) => url.pathname === "/");
    await link.focus();
    await page.keyboard.press("Shift+F10");
    await expect(
      page.getByRole("menuitem", { name: "Edit tags for skadis_kitchen-roll_screw", exact: true }),
    ).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(link).toBeFocused();
    expect(problems).toEqual([]);
  });

  test("keeps the prototype detail header free of added icon controls", async ({ page }) => {
    await page.goto("/models/1");
    const title = page.getByRole("heading", { name: "skadis_kitchen-roll_screw", exact: true });
    const header = title.locator("xpath=ancestor::header");
    await expect(header.getByRole("button", { name: "Favorite", exact: true })).toBeVisible();
    await expect(header.getByRole("button", { name: "Share", exact: true })).toBeVisible();
    await expect(header.locator("svg")).toHaveCount(0);
    const rename = header.getByRole("button", {
      name: "Rename skadis_kitchen-roll_screw",
      exact: true,
    });
    await rename.focus();
    await page.keyboard.press("Shift+F10");
    await expect(page.getByRole("menuitem", { name: "Edit details", exact: true })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(rename).toBeFocused();
  });

  test("downloads a rendered toolpath capture", async ({ page }) => {
    const preview = await openToolpath(page);
    const completed = page.waitForEvent("download");

    await preview.getByRole("button", { name: "Screenshot", exact: true }).click();

    const download = await completed;
    expect(download.suggestedFilename()).toMatch(/\.png$/);
    const path = await download.path();
    expect(path).not.toBeNull();
    const image = await readFile(path!);
    expect(image.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a");
    expect(image.length).toBeGreaterThan(1000);
  });

  test("changes the rendered toolpath camera to the top view", async ({ page }) => {
    const preview = await openToolpath(page);
    const canvas = preview.locator("canvas");
    const before = await canvas.screenshot();

    await preview.getByRole("button", { name: "Top", exact: true }).click();

    await expect(preview.getByRole("button", { name: "Top", exact: true })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect.poll(async () => (await canvas.screenshot()).equals(before)).toBe(false);
  });

  test("toggles the details panel from a focused search field", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/models/1");
    const details = page.getByTestId("model-detail-sidebar");
    await expect(details).toBeVisible();
    await page.locator("header [data-model-search]").focus();

    await page.keyboard.press("Control+Alt+b");

    await expect(details).toBeHidden();
    await page.keyboard.press("Control+Alt+b");
    await expect(details).toBeVisible();
  });

  test("opens the focused revision failure note from the keyboard", async ({ page }) => {
    await page.goto("/models/1");
    await page.getByRole("tab", { name: /^Revisions/ }).click();
    await page.getByRole("button", { name: "Rev 1", exact: true }).focus();

    await page.keyboard.press("2");

    const record = page.getByRole("dialog", { name: "Add print record" });
    await expect(record).toBeVisible();
    await expect(record.getByRole("radio", { name: "Failed", exact: true })).toBeChecked();
    await expect(record.getByRole("textbox", { name: "Error (opt.)" })).toBeFocused();
    await expect(
      record.getByRole("group", { name: "G-code Revision", exact: true }).getByRole("radio"),
    ).toBeChecked();
  });

  for (const width of [320, 1920]) {
    test(`shows a current source access failure after processing succeeded at ${width}px`, async ({
      page,
    }, testInfo) => {
      await page.setViewportSize({ width, height: 1080 });
      const failedFile = aRevision({
        id: 7,
        file_type: "stl",
        original_filename: "linked-already-processed.stl",
        is_external: true,
      });
      const healthyFile = aRevision({
        id: 8,
        file_type: "stl",
        original_filename: "another-source.stl",
      });
      await page.route("**/api/v1/models/1", (route) =>
        route.fulfill({ json: aModel({ files: [failedFile, healthyFile] }) }),
      );
      await page.route(/\/api\/v1\/files\/[78]\/derivatives$/, (route) =>
        route.fulfill({
          json: [
            aDerivative({ kind: "thumbnail", state: "ready" }),
            aDerivative({ kind: "metadata", state: "ready" }),
          ],
        }),
      );
      let reads = 0;
      await page.route("**/api/v1/files/7/stl", (route) => {
        reads++;
        return route.fulfill({
          status: 409,
          json: { detail: "storage_authentication_failed" },
        });
      });
      await page.goto("/models/1");
      await expect(
        page
          .getByRole("region", { name: "Preview", exact: true })
          .getByText(/provider rejected authentication/),
      ).toBeVisible();
      // StrictMode may start an aborted read during mount. Opening Files
      // must reuse the observed failure without adding a storage read.
      const readsBeforeFiles = reads;
      const pane = page.getByTestId("model-detail-sidebar");
      await page.getByRole("tab", { name: /Files/ }).click();
      const card = pane.getByRole("article", { name: failedFile.original_filename });
      const message = card.getByText(/provider rejected authentication/);
      await expect(message).toBeVisible();
      await expect(
        pane
          .getByRole("article", { name: healthyFile.original_filename })
          .getByText(/provider rejected authentication/),
      ).toHaveCount(0);
      await card.evaluate((element) => element.scrollIntoView({ block: "center" }));
      const bounds = (await card.boundingBox())!;
      const text = (await message.boundingBox())!;
      expect(text.x).toBeGreaterThanOrEqual(bounds.x);
      expect(text.x + text.width).toBeLessThanOrEqual(bounds.x + bounds.width);
      expect(await card.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(
        true,
      );
      await card.screenshot({ path: testInfo.outputPath("processed-source-access-error.png") });
      expect(reads).toBe(readsBeforeFiles);
    });
  }

  for (const width of [320, 390, 1920]) {
    test(`keeps failed source controls inside the details panel at ${width}px`, async ({
      page,
    }) => {
      await page.setViewportSize({ width, height: 1080 });
      const filename = "linked_source_with_a_long_unbroken_filename_for_a_detailed_model.3mf";
      const file = aRevision({
        id: 7,
        file_type: "3mf",
        original_filename: filename,
        is_external: true,
      });
      await page.route("**/api/v1/models/1", (route) =>
        route.fulfill({ json: aModel({ files: [file] }) }),
      );
      await page.route("**/api/v1/files/7/derivatives", (route) =>
        route.fulfill({
          json: (["metadata", "thumbnail", "viewer_stl"] as const).map((kind) =>
            aDerivative({
              kind,
              state: "failed",
              failure_reason: "storage_authentication_failed",
              retryable: true,
            }),
          ),
        }),
      );
      await page.route("**/api/v1/files/7/stl", (route) =>
        route.fulfill({ status: 422, json: { detail: "storage_authentication_failed" } }),
      );
      await page.goto("/models/1");
      const pane = page.getByTestId("model-detail-sidebar");
      if (width === 1920) {
        const resize = page.getByRole("separator", { name: "Resize details panel", exact: true });
        await resize.focus();
        await resize.press("Home");
        await expect.poll(async () => (await pane.boundingBox())!.width).toBeCloseTo(400, 0);
      }
      await page.getByRole("tab", { name: /Files/ }).click();
      const card = pane.getByRole("article", { name: filename });
      const explanation = card.getByText(/provider rejected authentication/);
      await expect(explanation).toBeVisible();
      await page.evaluate(() => document.fonts.ready);
      const download = card.getByRole("button", { name: "Download", exact: true });
      const headerBounds = await download.boundingBox();
      const explanationBounds = await explanation.boundingBox();
      expect(headerBounds).not.toBeNull();
      expect(explanationBounds).not.toBeNull();
      expect(explanationBounds!.y).toBeGreaterThanOrEqual(headerBounds!.y + headerBounds!.height);
      for (const control of [
        explanation,
        card.getByText(filename, { exact: true }),
        card.getByRole("button", { name: "Open in slicer", exact: true }),
        download,
        card.getByRole("button", { name: `Actions for ${filename}`, exact: true }),
        ...(await card.getByRole("button", { name: /^Retry / }).all()),
      ]) {
        await control.scrollIntoViewIfNeeded();
        const bounds = await control.boundingBox();
        const frame = await card.boundingBox();
        expect(bounds).not.toBeNull();
        expect(frame).not.toBeNull();
        expect(bounds!.x).toBeGreaterThanOrEqual(frame!.x);
        expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(frame!.x + frame!.width);
        expect(
          await control.evaluate((element) => element.scrollWidth <= element.clientWidth + 1),
        ).toBe(true);
      }
      await expect(card.getByRole("button", { name: /^Retry / })).toHaveCount(3);
      for (const retry of await card.getByRole("button", { name: /^Retry / }).all()) {
        const style = await retry.evaluate((element) => {
          const style = getComputedStyle(element);
          return { border: parseFloat(style.borderTopWidth), background: style.backgroundColor };
        });
        expect(style.border).toBeGreaterThan(0);
        expect(style.background).not.toBe("rgba(0, 0, 0, 0)");
      }
      await card.screenshot({ path: test.info().outputPath(`source-files-${width}.png`) });
      expect(await pane.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(
        true,
      );
    });
  }

  test("anchors the title editor without shifting header actions at 1080p", async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto("/models/1");
    const title = page.getByRole("heading", { name: "skadis_kitchen-roll_screw", exact: true });
    const tags = page.getByRole("button", { name: "Edit tags for skadis_kitchen-roll_screw" });
    const favorite = page.getByRole("button", { name: "Favorite", exact: true });
    const actions = page.getByRole("button", { name: "Share", exact: true });
    await expect(tags).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    const header = title.locator("xpath=ancestor::header");
    const originalHeader = await header.boundingBox();
    const originalActions = await Promise.all(
      [tags, favorite, actions].map((item) => item.boundingBox()),
    );

    await page.getByRole("button", { name: "Rename skadis_kitchen-roll_screw" }).click();
    const editor = page.getByRole("dialog", {
      name: "Rename skadis_kitchen-roll_screw",
      exact: true,
    });
    await expect(editor.getByRole("textbox", { name: "Model name", exact: true })).toBeFocused();
    await expect(title).toBeVisible();
    await expect(editor).not.toHaveAttribute("aria-modal", "true");
    await expect(page.getByPlaceholder("Optional description")).toHaveCount(0);
    const titleBox = await title.boundingBox();
    const editorBox = await editor.boundingBox();
    expect(titleBox).not.toBeNull();
    expect(editorBox).not.toBeNull();
    expect(editorBox!.y).toBeGreaterThanOrEqual(titleBox!.y + titleBox!.height);
    expect(editorBox!.y - titleBox!.y - titleBox!.height).toBeLessThanOrEqual(32);
    expect(Math.abs(editorBox!.x - titleBox!.x)).toBeLessThanOrEqual(1);
    expect(editorBox!.width).toBeLessThan(480);
    expect(editorBox!.height).toBeLessThan(200);
    expect(await header.boundingBox()).toEqual(originalHeader);
    for (const [index, action] of [tags, favorite, actions].entries()) {
      await expect(action).toBeVisible();
      await expect(action).toBeEnabled();
      expect(await action.boundingBox()).toEqual(originalActions[index]);
    }

    // An ordinary header action remains usable while the title editor is open.
    await actions.click();
    await expect(editor).toHaveCount(0);
    await expect(
      page.getByRole("dialog", { name: "Share skadis_kitchen-roll_screw", exact: true }),
    ).toBeVisible();
  });

  test("keeps missing-name feedback inside the compact title editor", async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto("/models/1");
    const title = page.getByRole("heading", { name: "skadis_kitchen-roll_screw", exact: true });
    await page.evaluate(() => document.fonts.ready);
    const originalHeader = await title.locator("xpath=ancestor::header").boundingBox();
    await title.dblclick();
    const editor = page.getByRole("dialog", {
      name: "Rename skadis_kitchen-roll_screw",
      exact: true,
    });
    await editor.getByRole("textbox", { name: "Model name", exact: true }).fill("");
    await expect(editor.getByRole("alert")).toContainText("Model name is required.");
    await expect(editor.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
    await expect(editor.getByRole("button", { name: "Cancel", exact: true })).toBeEnabled();
    expect(await title.locator("xpath=ancestor::header").boundingBox()).toEqual(originalHeader);
  });

  for (const width of [320, 390]) {
    test(`keeps the compact title editor inside a ${width}px viewport`, async ({ page }) => {
      await page.setViewportSize({ width, height: 844 });
      await page.goto("/models/1");
      const rename = page.getByRole("button", { name: "Rename skadis_kitchen-roll_screw" });
      await rename.click();
      const editor = page.getByRole("dialog", {
        name: "Rename skadis_kitchen-roll_screw",
        exact: true,
      });
      const name = editor.getByRole("textbox", { name: "Model name", exact: true });
      await name.fill("A long model title that still needs reachable editing controls");
      await page.evaluate(() => document.fonts.ready);
      for (const control of [
        editor,
        name,
        editor.getByRole("button", { name: "Cancel", exact: true }),
        editor.getByRole("button", { name: "Save", exact: true }),
      ]) {
        await expect(control).toBeInViewport();
        const bounds = await control.boundingBox();
        expect(bounds).not.toBeNull();
        expect(bounds!.x).toBeGreaterThanOrEqual(0);
        expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
      }
      await expect(page.getByRole("button", { name: "Favorite", exact: true })).toBeVisible();
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      ).toBe(true);
      await name.press("Escape");
      await expect(editor).toHaveCount(0);
      await expect(rename).toBeFocused();
      await expect(
        page.getByRole("heading", { name: "skadis_kitchen-roll_screw", exact: true }),
      ).toBeVisible();
    });
  }

  for (const width of [320, 1280]) {
    test(`wraps the full nested collection path in model information at ${width}px`, async ({
      page,
    }) => {
      const collectionLabel = "PrintStash/Organization/Gridfinity/" + "NestedCollection".repeat(20);
      await page.setViewportSize({ width, height: 900 });
      await page.route("**/api/v1/models/1", (route) =>
        route.fulfill({ json: aModel({ id: 1, collection_label: collectionLabel }) }),
      );
      await page.goto("/models/1");
      const sidebar = page.getByRole("region", { name: "About this model" });
      const path = sidebar.getByText(collectionLabel, { exact: true });
      await expect(path).toBeVisible();
      await path.scrollIntoViewIfNeeded();

      const bounds = await sidebar.evaluate((element) => ({
        content: element.scrollWidth,
        width: element.clientWidth,
      }));
      const pathBounds = await path.evaluate((element) => ({
        content: element.scrollWidth,
        width: element.clientWidth,
        height: element.getBoundingClientRect().height,
        lineHeight: Number.parseFloat(getComputedStyle(element).lineHeight),
      }));
      expect(bounds.content).toBeLessThanOrEqual(bounds.width + 1);
      expect(pathBounds.content).toBeLessThanOrEqual(pathBounds.width + 1);
      expect(pathBounds.height).toBeGreaterThan(pathBounds.lineHeight * 2);
    });
  }

  test("recovers a failed Model read without reloading the route", async ({ page }) => {
    let available = false;
    await page.route("**/api/v1/models/1", async (route) => {
      if (!available)
        await route.fulfill({ status: 503, json: { detail: "temporarily_unavailable" } });
      else await route.continue();
    });
    await page.goto("/models/1");
    await expect(page.getByText("Couldn’t load this model")).toBeVisible();
    available = true;

    await page.getByRole("button", { name: "Retry", exact: true }).click();

    await expect(page.getByRole("button", { name: "Share", exact: true })).toBeVisible();
    await expect(page.getByText("Couldn’t load this model")).toHaveCount(0);
    await expect(page).toHaveURL((url) => url.pathname === "/models/1");
  });

  test("confirms an ambiguous Model save before retry", async ({ page }) => {
    let responseLost = false;
    let writes = 0;
    await page.route("**/api/v1/models/1", async (route) => {
      if (route.request().method() === "PATCH") {
        writes += 1;
        responseLost = true;
        await route.abort("failed");
        return;
      }
      const response = await route.fetch();
      // SAFETY: the mock API's Model detail fixture has the complete ModelRead contract.
      const model = (await response.json()) as ModelRead;
      await route.fulfill({
        json: {
          ...model,
          name: responseLost ? "Saved draft" : model.name,
          edit_version: responseLost ? 7 : 1,
        },
      });
    });
    await page.goto("/models/1");
    await page.getByRole("heading", { level: 1 }).click({ button: "right" });
    await page.getByRole("menuitem", { name: /Edit details/ }).click();
    await page.getByRole("textbox", { name: "Name (required)" }).fill("Saved draft");
    await page.getByRole("button", { name: "Save changes", exact: true }).click();
    await expect(
      page.getByText("The save was not confirmed. Review the latest version before retrying."),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Save changes", exact: true })).toBeDisabled();

    await page.getByRole("button", { name: "Review latest version" }).click();
    await expect(page.getByRole("dialog", { name: "Latest saved version" })).toContainText(
      "Saved draft",
    );
    await page.getByRole("button", { name: "Use latest version" }).click();
    await page.getByRole("button", { name: "Cancel", exact: true }).click();

    await expect(page.getByRole("heading", { name: "Saved draft", exact: true })).toBeVisible();
    expect(writes).toBe(1);
  });

  test("reviews a conflicting Model before intentional retry", async ({ page }) => {
    let conflicted = false;
    let saved = false;
    const versions: (string | undefined)[] = [];
    await page.route("**/api/v1/models/1", async (route) => {
      if (route.request().method() === "PATCH") {
        versions.push(route.request().headers()["if-match"]);
        if (!conflicted) {
          conflicted = true;
          await route.fulfill({ status: 412, json: { detail: "edit_conflict" } });
          return;
        }
        saved = true;
      }
      const response = await route.fetch({ method: "GET" });
      // SAFETY: this route reads the repository's complete ModelRead mock fixture.
      const model = (await response.json()) as ModelRead;
      await route.fulfill({
        json: {
          ...model,
          name: saved ? "My browser draft" : conflicted ? "Other editor" : model.name,
          edit_version: saved ? 8 : conflicted ? 7 : 1,
        },
      });
    });
    await page.goto("/models/1");
    await page.getByRole("heading", { level: 1 }).click({ button: "right" });
    await page.getByRole("menuitem", { name: /Edit details/ }).click();
    await page.getByRole("textbox", { name: "Name (required)" }).fill("My browser draft");
    await page.getByRole("button", { name: "Save changes", exact: true }).click();

    await expect(page.getByRole("textbox", { name: "Name (required)" })).toHaveValue(
      "My browser draft",
    );
    await expect(page.getByRole("button", { name: "Save changes", exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "Review latest version" }).click();
    await expect(page.getByRole("dialog", { name: "Latest saved version" })).toContainText(
      "Other editor",
    );
    expect(versions).toEqual(['"model-1-eaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-v1"']);
    await page.getByRole("button", { name: "Save my draft against this version" }).click();

    await expect(
      page.getByRole("heading", { name: "My browser draft", exact: true }),
    ).toBeVisible();
    expect(versions).toEqual([
      '"model-1-eaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-v1"',
      '"model-1-eaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-v7"',
    ]);
  });

  test("restores a trashed source within its original Model", async ({ page }) => {
    let removed = false;
    let original: ModelRead | null = null;
    const modelBody = () => {
      if (!original) throw new Error("Mock Model response has not loaded");
      const model = original;
      const files = model.files;
      return removed
        ? {
            ...model,
            files: files.filter((file) => file.id !== 1),
            trashed_source_files: [{ id: 1, original_filename: "skadis_kitchen-roll_screw.stl" }],
            thumbnail_url: null,
          }
        : { ...model, trashed_source_files: [] };
    };
    await page.route("**/api/v1/models/1", async (route) => {
      const response = await route.fetch();
      // SAFETY: the in-repository mock API returns the ModelRead fixture at this URL.
      original = (await response.json()) as ModelRead;
      await route.fulfill({ response, json: modelBody() });
    });
    await page.route("**/api/v1/models/1/files/1", async (route) => {
      expect(route.request().method()).toBe("DELETE");
      removed = true;
      await route.fulfill({ json: modelBody() });
    });
    await page.route("**/api/v1/models/1/files/1/restore", async (route) => {
      expect(route.request().method()).toBe("POST");
      removed = false;
      await route.fulfill({ json: modelBody() });
    });

    await page.goto("/models/1");
    await page.getByRole("tab", { name: /Files/ }).click();
    await page.getByRole("button", { name: "Actions for skadis_kitchen-roll_screw.stl" }).click();
    await page.getByRole("menuitem", { name: "Move to trash" }).click();
    await page
      .getByRole("dialog", { name: "Move source file to trash?" })
      .getByRole("button", { name: "Move to trash" })
      .click();

    await expect(
      page.getByText(
        "No source files for this Model. Revisions and print history remain available.",
      ),
    ).toBeVisible();
    await expect(page.getByRole("heading", { name: "Trashed source files" })).toBeVisible();
    await page.reload();
    await page.getByRole("tab", { name: /Revisions/ }).click();
    await expect(
      page.getByText("skadis_kitchen-roll_screw_PLA_30m12s.gcode").first(),
    ).toBeVisible();
    await page.getByRole("tab", { name: /Files/ }).click();
    await page.getByRole("button", { name: "Restore" }).click();
    await expect(page.getByText("skadis_kitchen-roll_screw.stl").first()).toBeVisible();
    await expect(page.getByRole("heading", { name: "Trashed source files" })).toHaveCount(0);
  });

  test("explains failed outputs by their cause instead of the network", async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    // Native workers that could not start (issue #567) fail metadata and the
    // 3D preview together, with a code the reader should never see.
    await page.route("**/api/v1/files/1/derivatives", (route) =>
      route.fulfill({
        json: ["metadata", "viewer_stl"].map((kind) => ({
          kind,
          recipe_version: 1,
          state: "failed",
          attempts: 1,
          failure_reason: "worker_failed",
          updated_at: new Date().toISOString(),
          retryable: true,
        })),
      }),
    );

    await page.goto("/models/1");
    await page.getByRole("tab", { name: /Files/ }).click();

    await expect(page.getByText("Metadata, 3D preview failed")).toBeVisible();
    await expect(page.getByText(/stopped unexpectedly on the server/)).toHaveCount(1);
    await expect(page.getByText(/reaching the server/)).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Retry 3D preview" })).toBeVisible();
    await page
      .getByText("Metadata, 3D preview failed")
      .locator("xpath=ancestor::li")
      .screenshot({ path: test.info().outputPath("derivative-failures.png") });
  });

  for (const width of [320, 390]) {
    test(`keeps a failed preview readable at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 844 });
      await page.route("**/api/v1/files/*/stl", (route) =>
        route.fulfill({ status: 422, json: { detail: "storage_authentication_failed" } }),
      );
      await page.goto("/models/1");
      const panel = page.getByRole("region", { name: "Preview", exact: true });
      const cause = panel.getByText(/provider rejected authentication/);
      await expect(cause).toBeVisible();
      await cause.scrollIntoViewIfNeeded();
      const labelBox = await panel
        .getByRole("heading", { name: "Model preview", exact: true })
        .locator("..")
        .boundingBox();
      const statusBox = await cause.boundingBox();
      expect(labelBox).not.toBeNull();
      expect(statusBox).not.toBeNull();
      expect(statusBox!.y).toBeGreaterThanOrEqual(labelBox!.y + labelBox!.height);
      expect(
        await cause.evaluate((element) => {
          const box = element.getBoundingClientRect();
          const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
          return hit === element || element.contains(hit);
        }),
      ).toBe(true);
      await expect(panel.getByRole("button", { name: "Solid", exact: true })).toBeDisabled();
      await expect(panel.getByRole("button", { name: "Fit to view", exact: true })).toBeDisabled();
      await expect(panel.getByRole("button", { name: "Zoom in", exact: true })).toBeDisabled();
      expect(
        await panel.evaluate((element) => element.scrollWidth <= element.clientWidth + 1),
      ).toBe(true);
      const panelBox = await panel.boundingBox();
      for (const content of [
        panel.getByRole("status"),
        ...(await panel.getByRole("button").all()),
      ]) {
        const box = await content.boundingBox();
        expect(box).not.toBeNull();
        expect(box!.y).toBeGreaterThanOrEqual(panelBox!.y);
        expect(box!.y + box!.height).toBeLessThanOrEqual(panelBox!.y + panelBox!.height);
      }
      await panel.screenshot({ path: test.info().outputPath(`failed-preview-${width}.png`) });
    });
  }

  test("shows mesh overlays only for a ready 3D preview", async ({ page }) => {
    await page.route("**/api/v1/models/1", (route) =>
      route.fulfill({
        json: aModel({
          files: [
            aRevision({
              id: 1,
              file_type: "stl",
              original_filename: "cube.stl",
              metadata: aMetadata({ bbox_x_mm: 10, bbox_y_mm: 20, bbox_z_mm: 30 }),
            }),
            aRevision({
              id: 2,
              file_type: "gcode",
              metadata: aMetadata({ bbox_x_mm: 10, bbox_y_mm: 20, bbox_z_mm: 30 }),
            }),
          ],
        }),
      }),
    );
    await page.route("**/api/v1/files/2/toolpath", (route) =>
      route.fulfill({ status: 202, json: { state: "queued" } }),
    );
    await page.goto("/models/1");
    const panel = page.getByRole("region", { name: "Preview", exact: true });
    await expect(panel.getByRole("button", { name: "Screenshot", exact: true })).toBeEnabled();
    await expect(panel.getByRole("button", { name: "Isometric", exact: true })).toBeVisible();
    await expect(panel.getByText("10×20×30 mm", { exact: true })).toBeVisible();
    await panel.getByRole("button", { name: "Layers", exact: true }).click();
    await expect(panel.getByRole("button", { name: "Isometric", exact: true })).toHaveCount(0);
    await expect(panel.getByText("10×20×30 mm", { exact: true })).toHaveCount(0);
    await panel.getByRole("button", { name: "3D model", exact: true }).click();
    await expect(panel.getByRole("button", { name: "Screenshot", exact: true })).toBeEnabled();
    await expect(panel.getByRole("button", { name: "Isometric", exact: true })).toBeVisible();
    await expect(panel.getByText("10×20×30 mm", { exact: true })).toBeVisible();
  });

  test("keeps viewer controls reachable inside the phone preview", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });

    await page.goto("/models/1");

    const preview = page.getByRole("region", { name: "Preview", exact: true });
    await page.getByRole("button", { name: "Screenshot", exact: true }).waitFor();
    await expect(page.getByRole("button", { name: "Screenshot", exact: true })).toBeEnabled();
    const previewBox = await preview.boundingBox();
    const controls = [
      page.getByRole("button", { name: "Fit to view" }),
      page.getByRole("button", { name: "Screenshot" }),
      page.getByRole("button", { name: "Build plate grid" }),
      page.getByRole("button", { name: "Zoom in", exact: true }),
      page.getByRole("button", { name: "Zoom out", exact: true }),
      page.getByRole("button", { name: "Isometric", exact: true }),
    ];
    const controlBoxes = await Promise.all(controls.map((control) => control.boundingBox()));

    expect(previewBox).not.toBeNull();
    for (const box of controlBoxes) {
      expect(box).not.toBeNull();
      expect(box!.x).toBeGreaterThanOrEqual(previewBox!.x);
      expect(box!.x + box!.width).toBeLessThanOrEqual(previewBox!.x + previewBox!.width);
      expect(box!.y).toBeGreaterThanOrEqual(previewBox!.y);
      expect(box!.y + box!.height).toBeLessThanOrEqual(previewBox!.y + previewBox!.height);
      expect(Math.min(box!.width, box!.height)).toBeGreaterThanOrEqual(44);
    }
  });

  test("keeps the viewing label clear of mode controls on a phone", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });

    await page.goto("/models/1");

    await expect(page.getByRole("button", { name: "Screenshot", exact: true })).toBeEnabled();
    const viewingLabel = page
      .getByRole("heading", { name: "Model preview", exact: true })
      .locator("..");
    await expect(viewingLabel).toBeVisible();
    const modeControls = [
      page.getByRole("button", { name: "Solid" }),
      page.getByRole("button", { name: "X-Ray" }),
      page.getByRole("button", { name: "Wireframe" }),
      page.getByRole("button", { name: "Zoom in", exact: true }),
      page.getByRole("button", { name: "Zoom out", exact: true }),
      page.getByRole("button", { name: "Isometric", exact: true }),
    ];
    const labelBox = await viewingLabel.boundingBox();
    const modeBoxes = await Promise.all(modeControls.map((control) => control.boundingBox()));

    expect(labelBox).not.toBeNull();
    for (const box of modeBoxes) {
      expect(box).not.toBeNull();
      const overlaps =
        labelBox!.x < box!.x + box!.width &&
        labelBox!.x + labelBox!.width > box!.x &&
        labelBox!.y < box!.y + box!.height &&
        labelBox!.y + labelBox!.height > box!.y;
      expect(overlaps).toBe(false);
    }
  });

  test("keeps a stable touch surface for the 3D preview", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });

    await page.goto("/models/1");

    const preview = page.getByLabel("3D model preview");
    const previewBox = await preview.boundingBox();
    const touchAction = await preview.evaluate((canvas) => getComputedStyle(canvas).touchAction);

    expect(previewBox).not.toBeNull();
    expect(touchAction).toBe("none");
    expect(previewBox!.height).toBeGreaterThanOrEqual(300);
  });

  test("cached 3D preview clears its loading indicator", async ({ page }) => {
    const modelLink = page.getByRole("link", { name: /skadis_kitchen-roll_screw/ }).first();
    const preview = page.getByLabel("3D model preview");
    const loadingPreview = page.getByRole("status", { name: "Loading 3D preview" });

    await page.goto("/");
    await expect(page).toHaveURL(/\/\?type=all&sort=date-desc$/);
    const libraryUrl = page.url();
    await modelLink.click();
    await page.getByRole("link", { name: "Open page", exact: true }).click();
    await expect(page).toHaveURL((url) => url.pathname === "/models/1");
    await expect(preview).toBeVisible();
    await expect(loadingPreview).toHaveCount(0);

    await page.goBack();
    await expect(page).toHaveURL(libraryUrl);
    await modelLink.click();
    await page.getByRole("link", { name: "Open page", exact: true }).click();
    await expect(page).toHaveURL((url) => url.pathname === "/models/1");
    await expect(preview).toBeVisible();

    await expect(loadingPreview).toHaveCount(0);
  });

  test("model detail route renders data and hydrates printer integrations", async ({ page }) => {
    const problems = await collectPageProblems(page);

    await page.goto("/models/1");

    await expect(page.getByRole("heading", { name: "skadis_kitchen-roll_screw" })).toBeVisible();
    await expect(page.getByText("Creality Ender-3 V3 SE").first()).toBeVisible();
    await expect(page.getByRole("link", { name: /source model/i })).toHaveAttribute(
      "href",
      /printables\.com\/model\/123-skadis-kitchen-roll-screw/,
    );
    await expect(
      page
        .getByRole("region", { name: "Recorded prints" })
        .getByRole("row", { name: "ender Successful 2" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Send to printer", exact: true }).last().click();
    await expect(
      page
        .getByRole("dialog", { name: "Send Rev 1 to printer" })
        .getByRole("checkbox", { name: "Select ender" }),
    ).toBeChecked();
    await expect(page.getByText("This page could not be found")).toHaveCount(0);

    const html = await page.content();
    expect(html).not.toContain("NEXT_HTTP_ERROR_FALLBACK;404");
    expect(html).not.toContain('printerId":"$NaN');
    expect(problems).toEqual([]);
  });

  test("Source tab displays and replaces a private representative cover", async ({ page }) => {
    await page.goto("/models/1");
    await page.getByRole("tab", { name: "Source info" }).click();

    await expect(
      page.getByRole("heading", { name: "Source information", exact: true }),
    ).toBeVisible();
    await expect(page.getByTestId("source-identity-panel")).toBeVisible();
    await expect(page.getByRole("region", { name: "Captured metadata" })).toBeVisible();
    await expect(page.getByRole("img", { name: /private representative cover/i })).toBeVisible();
    await page.getByLabel("Replace cover").setInputFiles({
      name: "replacement.png",
      mimeType: "image/png",
      buffer: Buffer.from("replacement"),
    });
    const dialog = page.getByRole("dialog", { name: "Replace private cover?" });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Replace cover" }).click();
    await expect(page.getByRole("img", { name: /private representative cover/i })).toBeVisible();
  });

  test("Source tab keeps metadata readable at the minimum details-panel width", async ({
    page,
  }) => {
    await page.addInitScript(() => {
      localStorage.setItem("ps-model-detail-inspector-preferred-width", "400");
    });
    await page.goto("/models/1");
    await page.getByRole("tab", { name: "Source info" }).click();

    const sidebar = page.getByTestId("model-detail-sidebar");
    const sourceHeading = page.getByRole("heading", { name: "Source information", exact: true });
    const sourceUrl = page.getByRole("link", {
      name: "https://www.printables.com/model/41-capture-bracket",
    });
    const description = page.getByText(
      "New balloon-powered speedboat with an inflation adapter and twin nozzles for straight, long-lasting fun.",
      { exact: true },
    );
    const useDescription = page.getByRole("button", { name: "Use source description" });

    await expect(sidebar).toBeVisible();
    await expect(description).toBeVisible();
    const [sidebarBox, sourceHeadingBox, sourceUrlBox, descriptionBox, useDescriptionBox] =
      await Promise.all([
        sidebar.boundingBox(),
        sourceHeading.boundingBox(),
        sourceUrl.boundingBox(),
        description.boundingBox(),
        useDescription.boundingBox(),
      ]);

    expect(sidebarBox?.width).toBeCloseTo(400, 0);
    expect(sourceUrlBox!.y).toBeGreaterThan(sourceHeadingBox!.y + sourceHeadingBox!.height);
    expect(sourceUrlBox!.x + sourceUrlBox!.width).toBeLessThanOrEqual(
      sidebarBox!.x + sidebarBox!.width,
    );
    expect(descriptionBox!.width).toBeGreaterThan(240);
    expect(useDescriptionBox!.y).toBeGreaterThan(descriptionBox!.y + descriptionBox!.height);
  });

  test("print history explains exact, partial, and basic evidence with safe download", async ({
    page,
  }) => {
    await page.goto("/models/1");
    await page.getByRole("tab", { name: /History/ }).click();

    const evidence = page.getByTestId("print-job-reproducibility");
    await expect(evidence).toHaveCount(3);
    await expect(
      evidence.getByTestId("reproducibility-level").filter({ hasText: "Exactly reproducible" }),
    ).toHaveCount(1);
    await expect(
      evidence.getByTestId("reproducibility-level").filter({ hasText: "Partially reproducible" }),
    ).toHaveCount(1);
    await expect(
      evidence.getByTestId("reproducibility-level").filter({ hasText: "External/basic evidence" }),
    ).toHaveCount(1);
    await expect(page.getByText(/Error code: bambu_ftps_unavailable/)).toBeVisible();
    await expect(
      page.getByText("The printer cache is unavailable.", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText("Bambu project label", { exact: true })).toBeVisible();
    await expect(evidence.nth(0).getByText("Archived artifact", { exact: true })).toHaveCount(1);
    await expect(evidence.getByRole("button", { name: /download archived artifact/i })).toHaveCount(
      1,
    );
    await expect(evidence.nth(0).getByRole("button", { name: /preview toolpath/i })).toHaveCount(1);
    await expect(evidence.getByRole("link", { name: "Open model detail" })).toHaveCount(1);

    const [download] = await Promise.all([
      page.waitForEvent("download"),
      evidence.getByRole("button", { name: /download archived artifact/i }).click(),
    ]);
    expect(download.suggestedFilename()).toBe("benchy.gcode");

    const [toolpathRequest] = await Promise.all([
      page.waitForRequest((request) => request.url().endsWith("/api/v1/files/2/toolpath-preview")),
      evidence
        .nth(0)
        .getByRole("button", { name: /preview toolpath/i })
        .click(),
    ]);
    expect(toolpathRequest.method()).toBe("GET");
    const toolpathDialog = page.getByRole("dialog", { name: "Toolpath preview" });
    await expect(toolpathDialog).toBeVisible();
    await expect(toolpathDialog.getByText(/Layer 1 \/ 1/)).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(toolpathDialog).toHaveCount(0);
  });

  test("model detail uses focused send dialog and compact actions", async ({ page }) => {
    await page.goto("/models/1");

    await page.getByRole("heading", { level: 1 }).click({ button: "right" });
    await expect(page.getByRole("menuitem", { name: "Share" })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "Edit details" })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "Delete model" })).toBeVisible();
    await page.keyboard.press("Escape");

    await page.getByRole("button", { name: "Send to printer" }).last().click();
    const dialog = page.getByRole("dialog", { name: "Send Rev 1 to printer" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("checkbox", { name: "Select ender" })).toBeChecked();
    await expect(dialog.getByRole("group", { name: "G-code revision", exact: true })).toBeVisible();
    await expect(dialog.getByRole("checkbox", { name: "Start print immediately" })).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Send to printer" })).toBeEnabled();
    const scrollRegion = dialog.getByTestId("send-dialog-scroll-region");
    const [scrollBox, revisionBox] = await Promise.all([
      scrollRegion.boundingBox(),
      dialog.getByRole("group", { name: "G-code revision", exact: true }).boundingBox(),
    ]);
    expect(scrollBox).not.toBeNull();
    expect(revisionBox).not.toBeNull();
    expect(revisionBox!.x - scrollBox!.x).toBeGreaterThanOrEqual(2);
  });

  test("saves a revision failure note with Control Enter", async ({ page }) => {
    await page.goto("/models/1");
    await page.getByRole("tab", { name: /^Revisions/ }).click();
    await page.getByRole("button", { name: "Rev 1", exact: true }).focus();
    await page.keyboard.press("2");
    const dialog = page.getByRole("dialog", { name: "Add print record", exact: true });
    await expect(dialog.getByRole("radio", { name: "Failed", exact: true })).toBeChecked();
    await dialog.getByRole("radio", { name: "ender", exact: true }).check();
    await dialog.getByRole("textbox", { name: "Error (opt.)" }).fill("Corner lifted");
    const saved = page.waitForRequest(
      (request) => request.method() === "POST" && request.url().endsWith("/models/1/print-jobs"),
    );
    await page.keyboard.press("Control+Enter");
    expect((await saved).postDataJSON()).toMatchObject({
      file_id: 2,
      printer_id: 3,
      state: "failed",
      error: "Corner lifted",
    });
    await expect(dialog).toHaveCount(0);
  });

  test("keeps an inspected revision independent of its recommendation", async ({ page }) => {
    const first = aRevision({
      id: 11,
      version: 1,
      gcode_revision_number: 1,
      is_recommended: true,
      original_filename: "recommended.gcode",
      metadata: aMetadata({ material_type: "PLA", printer_model: "Prusa MK4" }),
    });
    const second = aRevision({
      id: 12,
      version: 2,
      gcode_revision_number: 2,
      original_filename: "inspected.gcode",
      metadata: aMetadata({ material_type: "PETG", printer_model: "Prusa MK4" }),
    });
    let writes = 0;
    await page.route("**/api/v1/models/1", (route) =>
      route.fulfill({ json: aModel({ files: [first, second] }) }),
    );
    page.on("request", (request) => {
      if (
        ["PATCH", "PUT", "POST"].includes(request.method()) &&
        /\/files\/(11|12)/.test(request.url())
      )
        writes++;
    });
    await page.goto("/models/1");
    await page.getByRole("tab", { name: "Print settings", exact: true }).click();
    const picker = page.getByRole("region", { name: "Inspect revision" });
    await picker.getByRole("button", { name: "Rev 2", exact: true }).click();
    await expect(picker.getByRole("button", { name: "Rev 2", exact: true })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect(
      page.getByTestId("model-detail-sidebar").getByText("PETG", { exact: true }),
    ).toBeVisible();
    await page.getByRole("tab", { name: "Print settings", exact: true }).click();
    await expect(
      page.getByTestId("model-detail-sidebar").getByText("PETG", { exact: true }),
    ).toBeVisible();
    await page.getByRole("tab", { name: "Overview", exact: true }).click();
    await page.getByRole("button", { name: "Send to printer", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Send Rev 2 to printer", exact: true });
    await expect(dialog).toBeVisible();
    await expect(
      dialog
        .getByRole("group", { name: "G-code revision", exact: true })
        .getByRole("radio", { name: /^Rev 2/ }),
    ).toBeChecked();
    await page.keyboard.press("Escape");
    await page.getByRole("tab", { name: "Print settings", exact: true }).click();
    await picker.getByRole("button", { name: "Return to recommended revision" }).click();
    await expect(picker.getByRole("button", { name: /Rev 1/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(writes).toBe(0);
  });

  for (const viewport of [
    { width: 1920, height: 1080 },
    { width: 1908, height: 905 },
    { width: 390, height: 844 },
  ]) {
    test(`keeps source facts beside a persistent preview at ${viewport.width}×${viewport.height}`, async ({
      page,
    }) => {
      await page.setViewportSize(viewport);
      await page.goto("/models/1");
      const preview = page.getByRole("region", { name: "Preview", exact: true });
      const original = await preview.elementHandle();
      const about = page.getByRole("region", { name: "About this model" });
      await expect(about).toBeVisible();
      await page.getByRole("tab", { name: "Print settings", exact: true }).click();
      await expect(about).toBeVisible();
      expect(await original!.evaluate((node) => node.isConnected)).toBe(true);
      await page.getByRole("tab", { name: "Overview", exact: true }).click();
      const tabs = (await page.getByRole("tablist").boundingBox())!;
      const viewer = (await preview.boundingBox())!;
      const inspector = (await page.getByTestId("model-detail-sidebar").boundingBox())!;
      expect(tabs.y + tabs.height).toBeLessThanOrEqual(viewer.y);
      if (viewport.width > 767) {
        expect(tabs.width).toBeGreaterThan(viewer.width + inspector.width);
        expect(inspector.y + inspector.height).toBeLessThanOrEqual(viewport.height);
      }
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      ).toBe(true);
    });
  }

  test("keeps an unresized inspector proportional to its available workspace", async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto("/models/1");
    const inspector = page.getByTestId("model-detail-sidebar");
    const layout = page.locator(".model-detail-layout");
    await expect
      .poll(async () =>
        Math.abs(
          (await inspector.boundingBox())!.width -
            Math.min(1000, ((await layout.boundingBox())!.width - 40) * 0.45),
        ),
      )
      .toBeLessThan(2);
    const wide = (await inspector.boundingBox())!.width;
    await page.setViewportSize({ width: 1440, height: 1000 });
    await expect.poll(async () => (await inspector.boundingBox())!.width).toBeLessThan(wide - 100);
    expect(
      await page.evaluate(() => localStorage.getItem("ps-model-detail-inspector-preferred-width")),
    ).toBeNull();
  });

  test("model detail tabs fit and details sidebar width persists", async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto("/models/1");

    const sidebar = page.getByTestId("model-detail-sidebar");
    const tablist = page.getByRole("tablist");
    const lastTab = tablist.getByRole("tab", { name: /History/ });
    const [tablistBox, lastTabBox] = await Promise.all([
      tablist.boundingBox(),
      lastTab.boundingBox(),
    ]);
    expect(tablistBox).not.toBeNull();
    expect(lastTabBox).not.toBeNull();
    expect(lastTabBox!.x + lastTabBox!.width).toBeLessThanOrEqual(
      tablistBox!.x + tablistBox!.width + 1,
    );

    const initialWidth = (await sidebar.boundingBox())!.width;
    const resizeHandle = page.getByRole("separator", { name: "Resize details panel" });
    const handleBox = await resizeHandle.boundingBox();
    expect(handleBox).not.toBeNull();
    await page.mouse.move(handleBox!.x + handleBox!.width / 2, handleBox!.y + 100);
    await page.mouse.down();
    await page.mouse.move(handleBox!.x - 120, handleBox!.y + 100, { steps: 5 });
    await page.mouse.up();

    await expect
      .poll(async () => (await sidebar.boundingBox())!.width)
      .toBeGreaterThan(initialWidth + 100);
    const resizedWidth = (await sidebar.boundingBox())!.width;
    await page.reload();
    await expect
      .poll(async () => (await sidebar.boundingBox())!.width)
      .toBeCloseTo(resizedWidth, 0);
  });

  test("add revision modal uses designed file picker and labeled fields", async ({ page }) => {
    await page.goto("/models/1");
    await page.getByRole("tab", { name: /Revisions/ }).click();
    await page.getByRole("button", { name: "Add revision", exact: true }).click();

    const dialog = page.getByRole("dialog", { name: "Add G-code revision" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Choose file", exact: true })).toBeVisible();
    await expect(dialog.getByLabel(/Revision label/)).toBeVisible();
    await expect(dialog.getByLabel(/Notes/)).toBeVisible();
    await expect(
      dialog.getByRole("radio", { name: "Make this Revision recommended" }),
    ).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Add revision" })).toBeDisabled();

    await dialog.locator(`input[accept=".gcode,.g,.gco,.bgcode"]`).setInputFiles({
      name: "stronger-walls.gcode",
      mimeType: "text/plain",
      buffer: Buffer.from("; generated by OrcaSlicer\nG28\n"),
    });
    await expect(dialog.getByText("stronger-walls.gcode")).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Add revision" })).toBeEnabled();
  });
});
