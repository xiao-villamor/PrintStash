/** Native viewer lifecycle contracts under StrictMode, without application persistence. */
import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";
import type {} from "../../browser-fixtures/stl-viewer";
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

test.describe("STLViewer", () => {
  test("retires controls while changing renderer preference", async ({ page }) => {
    await page.goto("/tests/browser-fixtures/stl-viewer.html?delayDevice=1");
    await expect(page.locator("body")).toHaveAttribute("data-ready", "true");

    await page.evaluate(() => window.meshViewerCheck.selectRenderer("webgpu"));

    await expect(page.locator("body")).toHaveAttribute("data-device-requested", "true");
    await expect(page.locator("body")).toHaveAttribute("data-ready", "false");
    await page.evaluate(() => window.meshViewerCheck.releaseDevice());
    await expect(page.locator("body")).toHaveAttribute("data-ready", "true");
    await page.evaluate(() => window.meshViewerCheck.close());
  });

  test("parses ASCII STL through its native worker", async ({ page }) => {
    await page.goto("/tests/browser-fixtures/stl-viewer.html?ascii=1");

    await expect(page.locator("body")).toHaveAttribute("data-ready", "true");
    await expect(page.locator("body")).toHaveAttribute("data-geometry-size", "6");
    await page.evaluate(() => window.meshViewerCheck.close());
  });

  test("synchronizes comparison camera movement", async ({ page }) => {
    await page.setViewportSize({ width: 1400, height: 700 });
    await page.goto("/tests/browser-fixtures/stl-viewer.html?compare=1");
    await expect(page.locator("body")).toHaveAttribute("data-ready", "true");
    const canvases = page.locator("canvas");
    const before = await canvases.first().screenshot();

    await page.evaluate(() => window.meshViewerCheck.zoom());

    await expect
      .poll(async () => {
        const primary = await canvases.first().screenshot();
        const peer = await canvases.nth(1).screenshot();
        return !primary.equals(before) && primary.equals(peer);
      })
      .toBe(true);
    await page.evaluate(() => window.meshViewerCheck.close());
  });

  const captures = [
    ...["webgl", "webgpu"].flatMap((backend) =>
      [1, 2, 3].map((scale) => ({ backend, scale, mode: "solid" })),
    ),
    { backend: "webgpu", scale: 2, mode: "xray" },
    { backend: "webgpu", scale: 2, mode: "wireframe" },
  ];
  for (const { backend, scale, mode } of captures) {
    test("exports " + mode + " PNG using " + backend + " at scale " + scale, async ({ page }) => {
      await page.goto(
        "/tests/browser-fixtures/stl-viewer.html?backend=" +
          backend +
          "&mode=" +
          mode +
          "&scale=" +
          scale,
      );
      await expect(page.locator("body")).toHaveAttribute("data-ready", "true");
      const download = page.waitForEvent("download");

      await page.evaluate(() => window.meshViewerCheck.screenshot());

      const file = await (await download).path();
      expect(file).not.toBeNull();
      const png = await readFile(file!);
      expect(png.subarray(1, 4).toString()).toBe("PNG");
      expect(png.readUInt32BE(16)).toBe(640 * scale);
      expect(png.readUInt32BE(20)).toBe(480 * scale);
      await page.evaluate(() => window.meshViewerCheck.close());
    });
  }

  for (const backend of ["webgl", "webgpu"]) {
    test("orbits the " + backend + " camera", async ({ page }) => {
      await page.goto("/tests/browser-fixtures/stl-viewer.html?backend=" + backend);
      await expect(page.locator("body")).toHaveAttribute("data-ready", "true");
      const initial = await page.evaluate(() => window.meshViewerCheck.pose());

      await page.mouse.move(320, 240);
      await page.mouse.down();
      await page.mouse.move(420, 290, { steps: 12 });
      await page.mouse.up();

      await expect
        .poll(async () => (await page.evaluate(() => window.meshViewerCheck.pose()))?.position)
        .not.toEqual(initial!.position);
      await page.evaluate(() => window.meshViewerCheck.close());
    });

    test("pans the " + backend + " camera", async ({ page }) => {
      await page.goto("/tests/browser-fixtures/stl-viewer.html?backend=" + backend);
      await expect(page.locator("body")).toHaveAttribute("data-ready", "true");
      const initial = await page.evaluate(() => window.meshViewerCheck.pose());

      await page.mouse.move(320, 240);
      await page.mouse.down({ button: "right" });
      await page.mouse.move(420, 290, { steps: 12 });
      await page.mouse.up({ button: "right" });

      await expect
        .poll(async () => (await page.evaluate(() => window.meshViewerCheck.pose()))?.target)
        .not.toEqual(initial!.target);
      await page.evaluate(() => window.meshViewerCheck.close());
    });
  }

  for (const backend of ["webgl", "webgpu"]) {
    for (const operation of ["fit", "reset"]) {
      test(operation + " restores the " + backend + " camera framing", async ({ page }) => {
        await page.goto(
          "/tests/browser-fixtures/stl-viewer.html?backend=" +
            backend +
            (operation === "reset" ? "&reset=1" : ""),
        );
        await expect(page.locator("body")).toHaveAttribute("data-ready", "true");
        const initial = await page.evaluate(() => window.meshViewerCheck.pose());
        await page.evaluate(() => window.meshViewerCheck.zoom());
        expect(await page.evaluate(() => window.meshViewerCheck.pose())).not.toEqual(initial);

        await page.evaluate(() => window.meshViewerCheck.fit());

        const fitted = await page.evaluate(() => window.meshViewerCheck.pose());
        expect(fitted).toEqual({
          position: initial!.position.map((value) => expect.closeTo(value, 10)),
          target: initial!.target.map((value) => expect.closeTo(value, 10)),
        });
        await page.evaluate(() => window.meshViewerCheck.close());
      });
    }
  }

  test("recovers the current camera after device loss", async ({ page }) => {
    await page.goto("/tests/browser-fixtures/stl-viewer.html?backend=webgpu");
    await expect(page.locator("body")).toHaveAttribute("data-ready", "true");
    await expect(page.locator("[data-mesh-backend]")).toHaveAttribute(
      "data-mesh-backend",
      "webgpu",
    );
    await page.evaluate(() => window.meshViewerCheck.zoom());
    const pose = await page.evaluate(() => window.meshViewerCheck.pose());

    await page.evaluate(() => window.meshViewerCheck.loseDevice());

    await expect(page.locator("[data-mesh-backend]")).toHaveAttribute("data-mesh-backend", "webgl");
    await expect(page.locator("body")).toHaveAttribute("data-ready", "true");
    await expect(page.locator("[data-mesh-backend]")).toHaveAttribute(
      "data-renderer-fallback",
      "device_lost",
    );
    expect(pose).not.toBeNull();
    const restored = await page.evaluate(() => window.meshViewerCheck.pose());
    expect(restored).toEqual({
      position: pose!.position.map((value) => expect.closeTo(value, 10)),
      target: pose!.target.map((value) => expect.closeTo(value, 10)),
    });
    const download = page.waitForEvent("download");
    await page.evaluate(() => window.meshViewerCheck.screenshot());
    expect((await download).suggestedFilename()).toBe("model.png");
    await page.evaluate(() => window.meshViewerCheck.close());
  });

  test("releases initialization completed after unmount", async ({ page }) => {
    await page.goto("/tests/browser-fixtures/stl-viewer.html?backend=webgpu&delayDevice=1");
    await expect(page.locator("body")).toHaveAttribute("data-device-requested", "true");

    await page.evaluate(() => window.meshViewerCheck.close());
    await page.evaluate(() => window.meshViewerCheck.releaseDevice());

    await expect(page.locator("body")).toHaveAttribute("data-device-released", "destroyed");
    await expect(page.locator("canvas")).toHaveCount(0);
  });

  test("releases the device when the viewer closes", async ({ page }) => {
    await page.goto("/tests/browser-fixtures/stl-viewer.html?backend=webgpu");
    await expect(page.locator("body")).toHaveAttribute("data-ready", "true");

    await page.evaluate(() => window.meshViewerCheck.close());

    await expect(page.locator("body")).toHaveAttribute("data-device-released", "destroyed");
    await expect(page.locator("canvas")).toHaveCount(0);
  });

  test("recovers from an unavailable WebGPU adapter", async ({ page }) => {
    await page.addInitScript(() => {
      navigator.gpu.requestAdapter = async () => null;
    });
    await page.goto("/tests/browser-fixtures/stl-viewer.html?backend=webgpu");

    await expect(page.locator("[data-mesh-backend]")).toHaveAttribute(
      "data-renderer-fallback",
      "initialization_failed",
    );
    await expect(page.locator("[data-mesh-backend]")).toHaveAttribute("data-mesh-backend", "webgl");
    await expect(page.locator("body")).toHaveAttribute("data-ready", "true");
    const download = page.waitForEvent("download");
    await page.evaluate(() => window.meshViewerCheck.screenshot());
    expect((await download).suggestedFilename()).toBe("model.png");
    await page.evaluate(() => window.meshViewerCheck.close());
  });
});
