/** Real GPU API capture conformance; no physical performance qualification is inferred. */
import { expect, test } from "@playwright/test";
import type {} from "../../browser-fixtures/mesh-renderer";
// Vulkan surface sharing is required for headless WebGPU canvas presentation.
// SwiftShader exercises the native API without implying physical GPU qualification.
test.use({
  launchOptions: {
    args: [
      "--enable-unsafe-webgpu",
      "--use-angle=swiftshader",
      "--use-webgpu-adapter=swiftshader",
      "--enable-features=Vulkan",
      "--use-vulkan=swiftshader",
      "--disable-vulkan-surface",
    ],
  },
});

test.describe("createMeshRenderer", () => {
  for (const scale of [1, 2, 3] as const) {
    test("exports a correctly oriented WebGL screenshot at scale " + scale, async ({ page }) => {
      await page.goto("/tests/browser-fixtures/mesh-renderer.html");
      await expect(page.locator("body")).toHaveAttribute("data-ready", "true");
      const capture = await page.evaluate((value) => window.meshCheck.capture(value), scale);
      expect(capture.width).toBe(128 * scale);
      expect(capture.height).toBe(128 * scale);
      expect(capture.top).toEqual([255, 0, 0, 255]);
      expect(capture.bottom).toEqual([0, 0, 255, 255]);
      await page.evaluate(() => window.meshCheck.close());
    });
  }

  for (const backend of ["webgl", "webgpu"]) {
    test("contains a " + backend + " capture during disposal", async ({ page }) => {
      await page.goto("/tests/browser-fixtures/mesh-renderer.html?backend=" + backend);
      await expect(page.locator("body")).toHaveAttribute("data-ready", "true");

      expect(await page.evaluate(() => window.meshCheck.closeDuringCapture())).toBe(true);
      await page.evaluate(() => window.meshCheck.close());
    });

    test("restores " + backend + " rendering after capture failure", async ({ page }) => {
      await page.goto("/tests/browser-fixtures/mesh-renderer.html?backend=" + backend);
      await expect(page.locator("body")).toHaveAttribute("data-ready", "true");

      expect(await page.evaluate(() => window.meshCheck.recoverCapture())).toBe(true);
      await page.evaluate(() => window.meshCheck.close());
    });
  }

  test.describe("WebGPU conformance", () => {
    for (const scale of [1, 2, 3] as const) {
      test("exports a correctly oriented WebGPU screenshot at scale " + scale, async ({ page }) => {
        await page.goto("/tests/browser-fixtures/mesh-renderer.html?backend=webgpu");
        await expect(page.locator("body")).toHaveAttribute("data-ready", "true");
        await expect(page.locator("body")).toHaveAttribute("data-backend", "webgpu");
        const capture = await page.evaluate((value) => window.meshCheck.capture(value), scale);
        expect(capture.width).toBe(128 * scale);
        expect(capture.height).toBe(128 * scale);
        expect(capture.top).toEqual([255, 0, 0, 255]);
        expect(capture.bottom).toEqual([0, 0, 255, 255]);
        await page.evaluate(() => window.meshCheck.close());
      });
    }
  });
});
