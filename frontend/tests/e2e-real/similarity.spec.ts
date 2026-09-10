/** Standalone similarity retains the independently printable Models after human review. */
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { silhouetteOverlap } from "../similarity-pixels";
import { test, expect } from "./helpers";
import { modelCard } from "./util";

const API = `http://127.0.0.1:${process.env.PLAYWRIGHT_REAL_API_PORT ?? 8410}`;

test.use({ trace: "retain-on-failure" });

test.afterEach(async ({ page }) => {
  await page.request.patch(`${API}/api/v1/similarity/settings`, { data: { enabled: false } });
});

/** Translate a copy of the repository's binary STL; preserve its normals. */
function calibrationCube(offset: number) {
  const bytes = readFileSync(new URL("../../../testdata/Calibration Cube.stl", import.meta.url));
  const faces = bytes.readUInt32LE(80);
  for (let face = 0; face < faces; face++)
    for (let vertex = 0; vertex < 3; vertex++) {
      const at = 84 + face * 50 + 12 + vertex * 12;
      bytes.writeFloatLE(bytes.readFloatLE(at) + offset, at);
    }
  return bytes;
}

test.describe("Standalone similarity", () => {
  test("@critical reviews similar Models without grouping or changing Artifacts", async ({
    page,
  }, testInfo) => {
    test.setTimeout(300_000);
    const prefix = `similarity-${Date.now()}`;
    await page.goto("/settings");
    // Development tooling is outside the preview; its floating button can obscure
    // just one canvas when Playwright scrolls it into view for a pixel comparison.
    await page.addStyleTag({
      content:
        '[aria-label="Open Tanstack query devtools"], [title="Open Tanstack query devtools"] { display: none !important; }',
    });
    await page.getByRole("button", { name: "Maintenance", exact: true }).click();
    const settings = page.getByRole("form", { name: "Similar models" });
    const enabled = settings.getByRole("checkbox", { name: "Enable similarity analysis" });
    if (!(await enabled.isChecked())) await enabled.click();
    await settings.getByRole("button", { name: "Save settings" }).click();
    await expect(page.getByText("Similarity settings saved")).toBeVisible();
    for (const [label, width, height] of [
      ["desktop", 1280, 800],
      ["mobile", 390, 844],
    ] as const) {
      await page.setViewportSize({ width, height });
      await settings
        .locator("..")
        .screenshot({ path: testInfo.outputPath(`settings-${label}.png`) });
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      ).toBe(true);
    }
    await page.setViewportSize({ width: 1280, height: 800 });
    const models: { id: number; name: string; hashes: string[] }[] = [];
    try {
      for (let index = 0; index < 2; index++) {
        const name = `${prefix}-${index}`;
        const mesh = calibrationCube(index * 40);
        // A distinct STL header avoids content deduplication across failed attempts.
        Buffer.from(name).copy(mesh, 0, 0, 80);
        const revision = Buffer.concat([
          readFileSync(
            new URL("../../../testdata/Calibration Cube_PLA_19m6s.gcode", import.meta.url),
          ),
          Buffer.from(`\n; Similarity fixture: ${name}\n`),
        ]);
        await page.goto("/");
        await page.getByRole("button", { name: "Upload", exact: true }).click();
        const dialog = page.getByRole("dialog", { name: "Upload model" });
        await dialog
          .locator('input[accept=".stl,.3mf,.obj,.step,.stp"]')
          .setInputFiles({ name: `${name}.stl`, mimeType: "model/stl", buffer: mesh });
        await dialog
          .locator('input[accept=".gcode,.g,.gco,.bgcode"]')
          .setInputFiles({ name: `${name}.gcode`, mimeType: "text/plain", buffer: revision });
        await page.getByPlaceholder("e.g. Bracket v2").fill(name);
        await page.getByRole("button", { name: /upload to vault/i }).click();
        await expect(dialog).toHaveCount(0);
        await page.getByRole("button", { name: "Notifications" }).click();
        const task = page.getByText(`Upload ${name}`, { exact: true }).locator("..");
        await expect(task.getByText("completed", { exact: true })).toBeVisible({ timeout: 60_000 });
        await page.getByRole("button", { name: "Notifications" }).click();
        await page.goto("/");
        const card = modelCard(page, name);
        await expect(card).toBeVisible();
        const href = await card.getAttribute("href");
        models.push({
          id: Number(href!.split("/").at(-1)),
          name,
          hashes: [mesh, revision]
            .map((bytes) => createHash("sha256").update(bytes).digest("hex"))
            .sort(),
        });
      }
      await page.goto(`/models/${models[0].id}`);
      await page.getByRole("tab", { name: "Similar", exact: true }).click();
      await page.getByRole("button", { name: "Find similar", exact: true }).click();
      const pair = page.getByRole("listitem").filter({ hasText: models[1].name });
      await expect(pair.getByRole("link", { name: "Compare" })).toBeVisible({ timeout: 60_000 });
      await test.step("Similarity Saved View survives navigation", async () => {
        const viewName = `Review similar ${Date.now()}`;
        await page.goto("/");
        await page.getByRole("checkbox", { name: "Has similar candidates" }).click();
        await expect(page).toHaveURL(/has_similar_candidates=yes/);
        await page.keyboard.press("Escape");
        const views = page.locator('main button[data-menu-trigger][aria-haspopup="dialog"]');
        await views.click();
        await page.getByText("Save current view").click();
        await page.getByPlaceholder("Ready to print").fill(viewName);
        await page.getByRole("button", { name: "Save view", exact: true }).click();
        await page.goto("/");
        await views.click();
        await page.getByRole("button", { name: viewName, exact: true }).click();
        await expect(page).toHaveURL(/has_similar_candidates=yes/);
        for (const model of models) await expect(modelCard(page, model.name)).toBeVisible();
      });
      await page.goto("/library/similar");
      await expect(page.getByRole("link", { name: "Compare" })).toBeVisible();
      await test.step("Review filters change the visible candidate set", async () => {
        await page.getByText("Advanced settings", { exact: true }).click();
        await expect(page.getByRole("combobox", { name: "Collection", exact: true })).toBeVisible();
        for (const [name, absent, reset] of [
          ["Review candidates", "confirmed", "open"],
          ["All evidence classes", "repaired", ""],
          ["Current evidence", "stale", "current"],
          ["All formats", "3mf", ""],
          ["All sources", "external", ""],
        ]) {
          const filter = page.getByRole("combobox", { name, exact: true });
          await filter.selectOption(absent);
          await expect(page.getByRole("link", { name: "Compare" })).toHaveCount(0);
          await filter.selectOption(reset);
          await expect(page.getByRole("link", { name: "Compare" })).toBeVisible();
        }
        const knownGood = page.getByRole("checkbox", { name: "Has a known-good Revision" });
        await knownGood.click();
        await expect(page.getByRole("link", { name: "Compare" })).toHaveCount(0);
        await knownGood.click();
        await expect(page.getByRole("link", { name: "Compare" })).toBeVisible();
        await page.getByText("Advanced settings", { exact: true }).click();
      });
      for (const [label, width, height] of [
        ["desktop", 1280, 800],
        ["mobile", 390, 844],
      ] as const) {
        await page.setViewportSize({ width, height });
        await page.screenshot({ path: testInfo.outputPath(`queue-${label}.png`), fullPage: true });
        expect(
          await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
        ).toBe(true);
      }
      await page.setViewportSize({ width: 1280, height: 800 });
      await page.getByRole("link", { name: "Compare" }).click();
      await expect(
        page.getByRole("heading", { name: "Compare models", exact: true }),
      ).toBeVisible();
      await expect(page.getByText("Identical geometry", { exact: true })).toBeVisible();
      await expect(page.getByRole("table")).toBeVisible();
      await expect(page.locator("canvas")).toHaveCount(2);
      const left = page.locator("canvas").nth(0),
        right = page.locator("canvas").nth(1);
      const originalView = await left.screenshot({
        style:
          '[aria-label="Open Tanstack query devtools"], [title="Open Tanstack query devtools"] { display: none !important; }',
      });
      const canvasBounds = (await left.boundingBox())!;
      await page.mouse.move(
        canvasBounds.x + canvasBounds.width / 2,
        canvasBounds.y + canvasBounds.height / 2,
      );
      await page.mouse.down();
      await page.mouse.move(
        canvasBounds.x + canvasBounds.width / 2 + 80,
        canvasBounds.y + canvasBounds.height / 2 + 35,
        { steps: 8 },
      );
      await page.mouse.up();
      await expect
        .poll(async () =>
          (
            await left.screenshot({
              style:
                '[aria-label="Open Tanstack query devtools"], [title="Open Tanstack query devtools"] { display: none !important; }',
            })
          ).equals(originalView),
        )
        .toBe(false);
      const screenshotStyle =
        '[aria-label="Open Tanstack query devtools"], [title="Open Tanstack query devtools"] { display: none !important; }';
      await expect
        .poll(async () => {
          const first = await left.screenshot({ style: screenshotStyle });
          const second = await right.screenshot({ style: screenshotStyle });
          return silhouetteOverlap(page, first, second);
        })
        .toBeGreaterThan(0.98);
      await expect(page.getByRole("button", { name: /Family/ })).toHaveCount(0);
      await page.screenshot({
        path: testInfo.outputPath("similarity-desktop.png"),
        fullPage: true,
      });
      await page.setViewportSize({ width: 390, height: 844 });
      await expect(page.getByRole("button", { name: "Confirm evidence" })).toBeVisible();
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      ).toBe(true);
      await page.screenshot({ path: testInfo.outputPath("similarity-mobile.png"), fullPage: true });
      await page.getByRole("button", { name: "Confirm evidence" }).click();
      const [reviewResponse] = await Promise.all([
        page.waitForResponse(
          (response) =>
            response.url().endsWith("/decision") && response.request().method() === "POST",
        ),
        page.getByRole("dialog").getByRole("button", { name: "Confirm evidence" }).click(),
      ]);
      expect(reviewResponse.ok(), await reviewResponse.text()).toBe(true);
      await expect(page.getByText("Resolution: evidence confirmed")).toBeVisible();
      await page.reload();
      await expect(page.getByText("Resolution: evidence confirmed")).toBeVisible();
      for (const model of models) {
        const response = await page.request.get(`${API}/api/v1/models/${model.id}`);
        expect(response.ok()).toBe(true);
        const body = await response.json();
        expect(body.name).toBe(model.name);
        expect(body.files.map((file: { sha256: string }) => file.sha256).sort()).toEqual(
          model.hashes,
        );
        expect(body.files.map((file: { file_type: string }) => file.file_type).sort()).toEqual([
          "gcode",
          "stl",
        ]);
      }
    } finally {
      for (const model of models) await page.request.delete(`${API}/api/v1/models/${model.id}`);
    }
  });
});
