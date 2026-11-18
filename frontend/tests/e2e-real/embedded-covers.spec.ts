/** A saved preview preference changes the real cover of an already ingested 3MF. */
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { test, expect } from "./helpers";

test("switches between the embedded picture and a regenerated cover", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto("/settings?section=previews");
  const toggle = page.getByRole("switch", { name: "Recreate embedded covers" });
  await expect(toggle).toBeEnabled();
  await expect(toggle).not.toBeChecked();

  const name = `e2e-embedded-cover-${Date.now()}`;
  const backend = resolve("../backend");
  const mesh = execFileSync(
    resolve(backend, ".venv/bin/python"),
    [
      "-c",
      [
        "import sys",
        "from tests.factories.content import png",
        "from tests.factories.geometry import three_mf, tetrahedron",
        "mesh = tetrahedron()",
        "mesh.vertices[:, 0] *= float(sys.argv[1])",
        "sys.stdout.buffer.write(three_mf(meshes={1: mesh}, extras={'Metadata/thumbnail.png': png()}))",
      ].join("\n"),
      String(1 + (Date.now() % 1_000_000) / 1_000_000),
    ],
    { cwd: backend },
  );
  const upload = await page.request.post("/api/v1/ingest/model", {
    multipart: {
      file: { name: `${name}.3mf`, mimeType: "model/3mf", buffer: mesh },
      model_name: name,
    },
  });
  expect(upload.status(), await upload.text()).toBe(202);
  const { job_id: jobId } = await upload.json();
  let modelId: number | undefined;
  let fileId: number | undefined;
  try {
    await expect
      .poll(
        async () => {
          const job = await (await page.request.get(`/api/v1/jobs/${jobId}`)).json();
          modelId = job.model_id;
          fileId = job.file_id;
          return job.state;
        },
        { timeout: 60_000 },
      )
      .toBe("completed");

    const path = `/api/v1/files/${fileId}/thumbnail`;
    const readCover = async () => {
      const response = await page.request.get(path);
      expect(response.status()).toBe(200);
      return response.body();
    };
    await expect.poll(async () => (await page.request.get(path)).status()).toBe(200);
    const embedded = await readCover();
    const previewPixel = await page.evaluate(async (url) => {
      const image = await createImageBitmap(await (await fetch(url)).blob());
      const canvas = new OffscreenCanvas(image.width, image.height);
      const context = canvas.getContext("2d");
      if (!context) throw new Error("Canvas unavailable");
      context.drawImage(image, 0, 0);
      return Array.from(context.getImageData(image.width / 2, image.height / 2, 1, 1).data);
    }, path);
    expect(previewPixel[2]).toBeGreaterThan(190);

    await toggle.click();
    await expect(toggle).toBeEnabled();
    await page.reload();
    await expect(toggle).toBeEnabled();
    await expect(toggle).toBeChecked();
    await page.screenshot({ path: testInfo.outputPath("previews-setting-after-reload.png") });
    await expect.poll(async () => (await readCover()).equals(embedded)).toBe(false);

    await toggle.click();
    await expect(toggle).toBeEnabled();
    await expect(toggle).not.toBeChecked();
    await expect.poll(async () => (await readCover()).equals(embedded)).toBe(true);
  } finally {
    if (await toggle.isChecked()) {
      await toggle.click();
      await expect(toggle).toBeEnabled();
    }
    if (modelId !== undefined) await page.request.delete(`/api/v1/models/${modelId}`);
  }
});
