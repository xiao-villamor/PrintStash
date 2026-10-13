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
import { expect, test } from "@playwright/test";

import { collectPageProblems, useMockApi } from "./_setup";
import type { ModelRead } from "../../src/types/models";

useMockApi();

test.describe("model detail route", () => {
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

    await expect(page.getByRole("button", { name: "Model actions" })).toBeVisible();
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
    await page.getByRole("button", { name: "Model actions" }).click();
    await page.getByRole("menuitem", { name: /Edit details/ }).click();
    await page.getByPlaceholder("Model name").fill("Saved draft");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(
      page.getByText("The save was not confirmed. Review the latest version before retrying."),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeDisabled();

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
    await page.getByRole("button", { name: "Model actions" }).click();
    await page.getByRole("menuitem", { name: /Edit details/ }).click();
    await page.getByPlaceholder("Model name").fill("My browser draft");
    await page.getByRole("button", { name: "Save", exact: true }).click();

    await expect(page.getByPlaceholder("Model name")).toHaveValue("My browser draft");
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
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

  test("keeps viewer controls reachable inside the phone preview", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });

    await page.goto("/models/1");

    const preview = page.getByLabel("3D model preview");
    const previewBox = await preview.boundingBox();
    const controls = [
      page.getByRole("button", { name: "Fit to view" }),
      page.getByRole("button", { name: "Screenshot" }),
      page.getByRole("button", { name: "Build plate grid" }),
      page.getByTitle("Zoom in"),
      page.getByTitle("Zoom out"),
      page.getByTitle("Reset view"),
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

    const viewingLabel = page.getByText(/Viewing:/).locator("..");
    await expect(viewingLabel).toBeVisible();
    const modeControls = [
      page.getByRole("button", { name: "Solid" }),
      page.getByRole("button", { name: "X-Ray" }),
      page.getByRole("button", { name: "Wire" }),
      page.getByTitle("Zoom in"),
      page.getByTitle("Zoom out"),
      page.getByTitle("Reset view"),
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
    await expect(page).toHaveURL((url) => url.pathname === "/models/1");
    await expect(preview).toBeVisible();
    await expect(loadingPreview).toHaveCount(0);

    await page.goBack();
    await expect(page).toHaveURL(libraryUrl);
    await modelLink.click();
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
    await expect(page.getByText("Printed OK").first()).toBeVisible();
    await expect(page.getByText("1/1 online")).toBeVisible();
    await expect(page.getByText("This page could not be found")).toHaveCount(0);

    const html = await page.content();
    expect(html).not.toContain("NEXT_HTTP_ERROR_FALLBACK;404");
    expect(html).not.toContain('printerId":"$NaN');
    expect(problems).toEqual([]);
  });

  test("Source tab displays and replaces a private representative cover", async ({ page }) => {
    await page.goto("/models/1");
    await page.getByRole("tab", { name: "Source" }).click();

    await expect(page.getByRole("heading", { name: "Source", exact: true })).toBeVisible();
    await expect(page.getByTestId("source-identity-panel")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Captured metadata" })).toBeVisible();
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
      localStorage.setItem("ps-model-detail-sidebar-width", "400");
    });
    await page.goto("/models/1");
    await page.getByRole("tab", { name: "Source" }).click();

    const sidebar = page.getByTestId("model-detail-sidebar");
    const sourceUrlLabel = page.getByText("Source URL", { exact: true });
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
    const [sidebarBox, sourceUrlLabelBox, sourceUrlBox, descriptionBox, useDescriptionBox] =
      await Promise.all([
        sidebar.boundingBox(),
        sourceUrlLabel.boundingBox(),
        sourceUrl.boundingBox(),
        description.boundingBox(),
        useDescription.boundingBox(),
      ]);

    expect(sidebarBox?.width).toBeCloseTo(400, 0);
    expect(sourceUrlBox!.y).toBeGreaterThan(sourceUrlLabelBox!.y + sourceUrlLabelBox!.height);
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

    await page.getByRole("button", { name: "Model actions" }).click();
    await expect(page.getByRole("menuitem", { name: "Share" })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "Edit details" })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "Delete model" })).toBeVisible();
    await page.keyboard.press("Escape");

    await page.getByRole("button", { name: "Send to printer" }).last().click();
    const dialog = page.getByRole("dialog", { name: "Send to printer" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("checkbox", { name: "Select ender" })).toBeChecked();
    await expect(dialog.getByLabel("G-code revision")).toBeVisible();
    await expect(dialog.getByRole("checkbox", { name: "Start print immediately" })).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Send to printer" })).toBeEnabled();
    const scrollRegion = dialog.getByTestId("send-dialog-scroll-region");
    const [scrollBox, revisionBox] = await Promise.all([
      scrollRegion.boundingBox(),
      dialog.getByLabel("G-code revision").boundingBox(),
    ]);
    expect(scrollBox).not.toBeNull();
    expect(revisionBox).not.toBeNull();
    expect(revisionBox!.x - scrollBox!.x).toBeGreaterThanOrEqual(2);
  });

  test("model detail tabs fit and details sidebar width persists", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto("/models/1");

    const sidebar = page.getByTestId("model-detail-sidebar");
    const tablist = sidebar.getByRole("tablist");
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
    await page.getByRole("button", { name: "Add", exact: true }).click();

    const dialog = page.getByRole("dialog", { name: "Add G-code revision" });
    await expect(dialog).toBeVisible();
    await expect(
      dialog.getByRole("button", { name: "Choose G-code or drop it here" }),
    ).toBeVisible();
    await expect(dialog.getByLabel(/Revision label/)).toBeVisible();
    await expect(dialog.getByLabel(/Notes/)).toBeVisible();
    await expect(dialog.getByRole("checkbox", { name: "Mark as recommended" })).toBeVisible();
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
