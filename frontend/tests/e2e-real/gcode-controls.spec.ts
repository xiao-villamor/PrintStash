/**
 * The G-code layer slider owns the preview's bottom controls. Mesh camera
 * overlays must never intercept a layer click on phones or desktop.
 * The real PrusaSlicer fixture exercises parsing and actual input events.
 */
import { readFile } from "node:fs/promises";
import { test, expect } from "./helpers";
import { modelCard, uploadModel } from "./util";

test.describe("G-code layer controls", () => {
  for (const width of [320, 390, 1440]) {
    test(`scrubs the full G-code slider at ${width}px`, async ({ page }, testInfo) => {
      const name = `layer-controls-${width}-${Date.now()}`;
      const fixture = await readFile(
        new URL("../../../backend/tests/fixtures/bgcode/prusaslicer.gcode", import.meta.url),
      );
      await uploadModel(page, name, {
        mesh: true,
        gcodeFile: {
          name: `${name}.gcode`,
          mimeType: "text/plain",
          buffer: Buffer.concat([fixture, Buffer.from(`\n; ${name}\n`)]),
        },
      });
      await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 });
      await modelCard(page, name).click();
      const modelId = new URL(page.url()).pathname.split("/").at(-1);
      try {
        await expect(page.getByRole("button", { name: "Fit to view" })).toBeEnabled({
          timeout: 30_000,
        });
        await page.getByRole("button", { name: "Layers", exact: true }).click();
        const slider = page.getByRole("slider", { name: "Current layer" });
        await expect(slider).toBeVisible({ timeout: 30_000 });
        await expect(slider).toHaveAttribute("max", "30");
        await expect(page.getByRole("button", { name: "Fit to view" })).toHaveCount(0);
        await slider.scrollIntoViewIfNeeded();
        await slider.focus();
        await slider.press("Home");
        await expect(slider).toHaveValue("0");

        for (const fraction of [0.1, 0.8, 0.95]) {
          const bounds = await slider.boundingBox();
          expect(bounds).not.toBeNull();
          const point = {
            x: bounds!.x + bounds!.width * fraction,
            y: bounds!.y + bounds!.height / 2,
          };
          expect(
            await slider.evaluate(
              (element, target) => document.elementFromPoint(target.x, target.y) === element,
              point,
            ),
          ).toBe(true);
          await page.mouse.click(point.x, point.y);
          const layer = Number(await slider.inputValue());
          expect(layer).toBeGreaterThanOrEqual(Math.round(30 * fraction) - 2);
          expect(layer).toBeLessThanOrEqual(Math.round(30 * fraction) + 2);
        }
        await page.screenshot({ path: testInfo.outputPath(`layers-${width}.png`) });
        await page.getByRole("button", { name: "3D model", exact: true }).click();
        await expect(page.getByRole("button", { name: "Fit to view" })).toBeEnabled({
          timeout: 30_000,
        });
      } finally {
        expect((await page.request.delete(`/api/v1/models/${modelId}`)).ok()).toBeTruthy();
        expect((await page.request.delete(`/api/v1/models/${modelId}/purge`)).ok()).toBeTruthy();
      }
    });
  }
});
