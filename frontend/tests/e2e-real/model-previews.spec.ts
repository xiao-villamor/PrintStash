/** Real ingestion publishes a complete, browsable view set without geometry transfer. */
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { test, expect } from "./helpers";
import type { ModelRead } from "../../src/types";

test.describe("Generated Model views", () => {
  test("browses published views after ingestion", async ({ page }) => {
    test.setTimeout(240_000);
    const name = `e2e-multi-angle-${Date.now()}`;
    const backend = resolve("../backend");
    const mesh = execFileSync(
      resolve(backend, ".venv/bin/python"),
      [
        "-c",
        "import sys,trimesh; m=trimesh.creation.box(extents=(10,12,float(sys.argv[1]))); sys.stdout.buffer.write(m.export(file_type='stl'))",
        String(14 + (Date.now() % 1_000_000) / 1_000_000),
      ],
      { cwd: backend },
    );
    const upload = await page.request.post("/api/v1/ingest/model", {
      multipart: {
        file: { name: `${name}.stl`, mimeType: "model/stl", buffer: mesh },
        model_name: name,
      },
    });
    expect(upload.status(), await upload.text()).toBe(202);
    const jobId: string = (await upload.json()).job_id;
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
      await expect
        .poll(
          async () => {
            const model: ModelRead = await (
              await page.request.get(`/api/v1/models/${modelId}`)
            ).json();
            return model.previews.kind === "artifact" ? model.previews.state : "none";
          },
          { timeout: 180_000 },
        )
        .toBe("ready");
      const transferred: string[] = [];
      page.on("request", (request) => transferred.push(new URL(request.url()).pathname));
      await page.goto(`/?q=${name}`);
      const card = page
        .locator("main article")
        .filter({ has: page.locator(`[data-model-preview="${name}"]`) });
      await expect(card.locator("img")).toHaveAttribute("data-preview-view", "cover");
      await card.getByRole("button", { name: "Next preview", exact: true }).click();
      await expect(card.locator("img")).toHaveAttribute("data-preview-view", "orbit-0");
      await card.getByRole("button", { name: "Next preview", exact: true }).click();
      await expect(card.locator("img")).toHaveAttribute("data-preview-view", "orbit-1");
      await card.getByRole("button", { name: "Model views", exact: true }).click();
      const gallery = page.getByRole("dialog", { name: "Model views" });
      await gallery.getByRole("button", { name: "Bottom", exact: true }).click();
      await expect(gallery.locator("img[data-preview-view]")).toHaveAttribute(
        "data-preview-view",
        "bottom",
      );
      expect(transferred.some((path) => path.endsWith("/stl"))).toBe(false);
      expect(transferred.some((path) => path.includes("/previews/"))).toBe(true);
    } finally {
      expect((await page.request.delete(`/api/v1/models/${modelId}`)).status()).toBe(204);
    }
  });
});

test.describe("Generated Model views under bulk ingestion", () => {
  test("measures first-cover availability during bulk ingestion", async ({ page }) => {
    test.setTimeout(180_000);
    const prefix = `e2e-preview-bulk-${Date.now()}`;
    const backend = resolve("../backend");
    const started = performance.now();
    const models: number[] = [];
    const samples = new Map<number, number>();
    try {
      const jobs = await Promise.all(
        [0, 1, 2, 3].map(async (index) => {
          const mesh = execFileSync(
            resolve(backend, ".venv/bin/python"),
            [
              "-c",
              "import sys,trimesh; m=trimesh.creation.icosphere(subdivisions=5); m.apply_scale(float(sys.argv[1])); sys.stdout.buffer.write(m.export(file_type='stl'))",
              String(1 + index + (Date.now() % 1_000_000) / 1_000_000),
            ],
            { cwd: backend },
          );
          const response = await page.request.post("/api/v1/ingest/model", {
            multipart: {
              file: { name: `${prefix}-${index}.stl`, mimeType: "model/stl", buffer: mesh },
            },
          });
          expect(response.status()).toBe(202);
          const job: { job_id: string } = await response.json();
          return job.job_id;
        }),
      );
      await expect
        .poll(
          async () => {
            for (const jobId of jobs) {
              const job = await (await page.request.get(`/api/v1/jobs/${jobId}`)).json();
              if (job.model_id && !models.includes(job.model_id)) models.push(job.model_id);
            }
            for (const id of models) {
              if (samples.has(id)) continue;
              const model: ModelRead = await (
                await page.request.get(`/api/v1/models/${id}`)
              ).json();
              if (model.thumbnail_url) {
                expect((await page.request.get(model.thumbnail_url)).status()).toBe(200);
                samples.set(id, Math.round(performance.now() - started));
              }
            }
            return samples.size;
          },
          { timeout: 120_000 },
        )
        .toBe(4);
      const milliseconds = [...samples.values()].sort((a, b) => a - b);
      console.info(
        "PREVIEW_BULK=" +
          JSON.stringify({ models: 4, trianglesEach: 20480, firstCoverMilliseconds: milliseconds }),
      );
      await expect
        .poll(
          async () => {
            const states = await Promise.all(
              models.map(async (id) => {
                const model: ModelRead = await (
                  await page.request.get(`/api/v1/models/${id}`)
                ).json();
                return model.previews.kind === "artifact" ? model.previews.state : "none";
              }),
            );
            return states;
          },
          { timeout: 120_000 },
        )
        .toEqual(["ready", "ready", "ready", "ready"]);
    } finally {
      for (const id of models) await page.request.delete(`/api/v1/models/${id}`);
    }
  });
});
