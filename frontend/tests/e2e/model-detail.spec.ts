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

useMockApi();

test.describe("model detail route", () => {
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

    await dialog.locator(`input[accept=".gcode,.g,.gco"]`).setInputFiles({
      name: "stronger-walls.gcode",
      mimeType: "text/plain",
      buffer: Buffer.from("; generated by OrcaSlicer\nG28\n"),
    });
    await expect(dialog.getByText("stronger-walls.gcode")).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Add revision" })).toBeEnabled();
  });
});
