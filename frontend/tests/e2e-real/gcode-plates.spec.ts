/** Uploaded sliced 3MF files show the exact selected plate's toolpath in the browser. */
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { test, expect } from "./helpers";
import { openModelPage, uploadGcodeModel } from "./util";
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

function slicedArchive(name: string, withMesh = false): Buffer {
  const backend = resolve("../backend");
  return execFileSync(
    resolve(backend, ".venv/bin/python"),
    [
      "-c",
      "import sys; from tests.fixtures.three_mf_projects import build_sliced_project; sys.stdout.buffer.write(build_sliced_project(marker=sys.argv[1], with_mesh=sys.argv[2] == 'true'))",
      name,
      String(withMesh),
    ],
    { cwd: backend },
  );
}

test.describe("Sliced G-code plates", () => {
  test("prints the selected plate of an attached revision", async ({ page }) => {
    const name = `e2e-sliced-revision-${Date.now()}`;
    await uploadGcodeModel(page, name);
    await openModelPage(page, name);
    const modelId = Number(new URL(page.url()).pathname.split("/").pop());
    const addedPrinter = await page.request.post("/api/v1/printers", {
      data: {
        name: `${name}-printer`,
        provider: "moonraker",
        moonraker_url: `http://127.0.0.1:${process.env.PLAYWRIGHT_MOCK_PRINTER_PORT ?? "7530"}`,
      },
    });
    expect(addedPrinter.status()).toBe(201);
    const printerId = (await addedPrinter.json()).id;
    try {
      await page.getByRole("tab", { name: /Revisions/ }).click();
      await page.getByRole("button", { name: "Add revision", exact: true }).click();
      const upload = page.getByRole("dialog", { name: "Add G-code revision" });
      await upload.locator("input[type=file]").setInputFiles({
        name: `${name}.gcode.3mf`,
        mimeType: "model/3mf",
        buffer: slicedArchive(name),
      });
      await upload.getByRole("radio", { name: "Make this Revision recommended" }).check();
      await upload.getByRole("button", { name: "Add revision", exact: true }).click();
      await expect(upload).toHaveCount(0);
      await page.reload();
      const model = await (await page.request.get(`/api/v1/models/${modelId}`)).json();
      const revision = model.files.find((file: { original_filename: string }) =>
        file.original_filename.endsWith(".gcode.3mf"),
      );
      expect(revision.gcode_revision_number).toBe(2);
      expect(revision.is_recommended).toBe(true);
      await page.getByRole("button", { name: "Layers", exact: true }).click();
      await page
        .getByRole("group", { name: "Sliced plates" })
        .getByRole("button", { name: "Plate 02" })
        .click();
      await expect(page.getByRole("slider")).toBeVisible();
      await page.getByRole("button", { name: "Send to printer", exact: true }).last().click();
      const send = page.getByRole("dialog", { name: "Send Rev 2 to printer" });
      await expect(
        send.getByRole("button", { name: "Send to printer", exact: true }),
      ).toBeDisabled();
      await send.getByRole("combobox", { name: "Sliced plate", exact: true }).selectOption("2");
      await send.getByRole("checkbox", { name: "Start print immediately" }).check();
      await page.screenshot({ path: test.info().outputPath("sliced-revision-send.png") });
      const sent = page.waitForResponse(
        (response) =>
          response.url().endsWith(`/api/v1/printers/${printerId}/send`) &&
          response.request().method() === "POST",
      );
      await send.getByRole("button", { name: "Send & start print", exact: true }).click();
      const result = await sent;
      expect(result.status()).toBe(200);
      const job = await result.json();
      expect(job.file_id).toBe(revision.id);
      expect(job.plate_index).toBe(2);
      expect(job.state).toBe("started");
      expect(job.remote_filename).toContain("plate-2");
      await expect(send).toHaveCount(0);
    } finally {
      await page.request.delete(`/api/v1/printers/${printerId}`);
      await page.request.delete(`/api/v1/models/${modelId}`);
    }
  });

  for (const withMesh of [false, true]) {
    test(`previews each plate ${withMesh ? "with" : "without"} source meshes`, async ({ page }) => {
      const name = `e2e-gcode-plates-${Date.now()}`;
      const archive = slicedArchive(name, withMesh);
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

        await page.getByRole("button", { name: "Layers", exact: true }).click();

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
