/** Uploaded sliced 3MF files show the exact selected plate's toolpath in the browser. */
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { test, expect } from "./helpers";
import type { Locator } from "@playwright/test";

async function hasRenderedPixels(canvas: Locator): Promise<boolean> {
  return canvas.evaluate((source: HTMLCanvasElement) => {
    const snapshot = document.createElement("canvas");
    snapshot.width = source.width;
    snapshot.height = source.height;
    const context = snapshot.getContext("2d");
    if (context === null) throw new Error("Canvas readback unavailable");
    context.drawImage(source, 0, 0);
    return context
      .getImageData(0, 0, snapshot.width, snapshot.height)
      .data.some((value, index) => index % 4 === 3 && value > 0);
  });
}

test.describe("Sliced G-code plates", () => {
  for (const withMesh of [false, true]) {
    test(`previews each plate ${withMesh ? "with" : "without"} source meshes`, async ({ page }) => {
      const name = `e2e-gcode-plates-${Date.now()}`;
      const backend = resolve("../backend");
      const archive = execFileSync(
        resolve(backend, ".venv/bin/python"),
        [
          "-c",
          "import sys; from tests.fixtures.three_mf_projects import build_sliced_project; sys.stdout.buffer.write(build_sliced_project(marker=sys.argv[1], with_mesh=sys.argv[2] == 'true'))",
          name,
          String(withMesh),
        ],
        { cwd: backend },
      );
      const upload = await page.request.post("/api/v1/ingest/model", {
        multipart: {
          file: { name: `${name}.gcode.3mf`, mimeType: "model/3mf", buffer: archive },
          model_name: name,
        },
      });
      expect(upload.status()).toBe(202);
      const jobId = (await upload.json()).job_id;
      let modelId = 0;
      await expect
        .poll(
          async () => {
            const job = await (await page.request.get(`/api/v1/jobs/${jobId}`)).json();
            modelId = job.model_id;
            return job.state;
          },
          { timeout: 60_000 },
        )
        .toBe("completed");
      try {
        await page.goto(`/models/${modelId}`);
        const firstRequest = page.waitForResponse((response) =>
          response.url().includes("/embedded-gcode?plate_index=1"),
        );

        await page.getByRole("button", { name: "GCode", exact: true }).click();

        const first = await firstRequest;
        expect(first.status()).toBe(200);
        expect(await first.text()).toContain(`${name} plate 1\n`);
        await expect(page.getByRole("slider")).toBeVisible();
        await expect(page.locator("canvas")).toBeVisible();
        await expect.poll(() => hasRenderedPixels(page.locator("canvas"))).toBe(true);
        const plates = page.getByRole("group", { name: "Sliced plates" });
        await expect(plates.getByRole("button", { name: "Plate 01" })).toHaveAttribute(
          "aria-pressed",
          "true",
        );
        const secondRequest = page.waitForResponse((response) =>
          response.url().includes("/embedded-gcode?plate_index=2"),
        );

        await plates.getByRole("button", { name: "Plate 02" }).click();

        const second = await secondRequest;
        expect(second.status()).toBe(200);
        expect(await second.text()).toContain(`${name} plate 2\n`);
        await expect(plates.getByRole("button", { name: "Plate 02" })).toHaveAttribute(
          "aria-pressed",
          "true",
        );
        await expect(page.getByRole("slider")).toBeVisible();
        await expect(page.locator("canvas")).toBeVisible();
        await expect.poll(() => hasRenderedPixels(page.locator("canvas"))).toBe(true);
        await expect(page.getByRole("alert")).toHaveCount(0);
        const screenshot = test.info().outputPath("plate-2-preview.png");
        await page.screenshot({ path: screenshot });
        await test.info().attach("plate-2-preview", { path: screenshot, contentType: "image/png" });
      } finally {
        expect((await page.request.delete(`/api/v1/models/${modelId}`)).status()).toBe(204);
      }
    });
  }
});
