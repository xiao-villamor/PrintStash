/** A large mesh opens automatically; a refused viewer keeps its durable reason across reloads and preserves originals. */
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { test, expect } from "./helpers";

test.describe("viewer STL", () => {
  test("opens a large mesh automatically", async ({ page }) => {
    const name = `e2e-viewer-large-${Date.now()}`;
    const backend = resolve("../backend");
    // A real 17 MB binary STL: larger than the former automatic budget, unique per run.
    const mesh = execFileSync(
      resolve(backend, ".venv/bin/python"),
      [
        "-c",
        [
          "import struct, sys, numpy as np",
          "n = 412",
          "u, v = np.meshgrid(np.linspace(0, 6.283, n + 1), np.linspace(0.01, 3.13, n + 1))",
          "p = np.stack([40 * np.sin(v) * np.cos(u), 40 * np.sin(v) * np.sin(u), 40 * np.cos(v)], -1).astype('<f4')",
          "a, b, c, d = p[:-1, :-1], p[1:, :-1], p[1:, 1:], p[:-1, 1:]",
          "t = np.concatenate([np.stack([a, b, c], 2).reshape(-1, 3, 3), np.stack([a, c, d], 2).reshape(-1, 3, 3)])",
          "r = np.zeros(len(t), dtype=[('n', '<f4', 3), ('v', '<f4', (3, 3)), ('a', '<u2')])",
          "r['v'] = t",
          "sys.stdout.buffer.write(sys.argv[1].encode().ljust(80, b' ') + struct.pack('<I', len(t)) + r.tobytes())",
        ].join("\n"),
        name,
      ],
      { cwd: backend, maxBuffer: 64 * 1024 * 1024 },
    );
    expect(mesh.length).toBeGreaterThan(16 * 1024 * 1024);
    const upload = await page.request.post("/api/v1/ingest/model", {
      multipart: {
        file: { name: `${name}.stl`, mimeType: "model/stl", buffer: mesh },
        model_name: name,
      },
    });
    expect(upload.status()).toBe(202);
    const jobId = (await upload.json()).job_id;
    let modelId = 0;
    let fileId = 0;
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
    let meshRequests = 0;
    page.on("request", (request) => {
      if (new URL(request.url()).pathname === `/api/v1/files/${fileId}/stl`) meshRequests += 1;
    });
    try {
      // Model detail opens the 3D view directly, including large Artifacts.
      await page.goto(`/models/${modelId}`);
      await expect.poll(() => meshRequests).toBeGreaterThan(0);
      await expect(page.getByRole("button", { name: "Load 3D preview", exact: true })).toHaveCount(
        0,
      );
      await expect(page.getByRole("button", { name: "Screenshot", exact: true })).toBeEnabled({
        timeout: 60_000,
      });
      await expect(page.locator("canvas")).toBeVisible();
    } finally {
      expect((await page.request.delete(`/api/v1/models/${modelId}`)).status()).toBe(204);
    }
  });

  test("retains a memory refusal while keeping the original downloadable", async ({ page }) => {
    const name = `e2e-viewer-refused-${Date.now()}`;
    const backend = resolve("../backend");
    const original = execFileSync(
      resolve(backend, ".venv/bin/python"),
      [
        "-c",
        "import sys; from tests.factories.geometry import expanding_three_mf; sys.stdout.buffer.write(expanding_three_mf())",
      ],
      { cwd: backend },
    );
    const upload = await page.request.post("/api/v1/ingest/model", {
      multipart: {
        file: { name: `${name}.3mf`, mimeType: "model/3mf", buffer: original },
        model_name: name,
      },
    });
    expect(upload.status()).toBe(202);
    const jobId = (await upload.json()).job_id;
    let modelId = 0;
    let fileId = 0;
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
    try {
      await page.goto(`/models/${modelId}`);
      await expect(
        page.getByText("3D preview omitted due to memory limits", { exact: true }),
      ).toBeVisible({ timeout: 60_000 });
      const before = await (await page.request.get(`/api/v1/files/${fileId}/stl`)).json();
      expect(before.detail).toBe("resource_limit");

      await page.reload();

      await expect(
        page.getByText("3D preview omitted due to memory limits", { exact: true }),
      ).toBeVisible();
      const after = await (await page.request.get(`/api/v1/files/${fileId}/stl`)).json();
      expect(after).toEqual(before);
      const download = await page.request.get(`/api/v1/files/${fileId}/download`);
      expect(download.status()).toBe(200);
      expect(await download.body()).toEqual(original);
    } finally {
      await page.request.delete(`/api/v1/models/${modelId}`);
    }
  });
  test("shares concurrent viewer preparation", async ({ page }) => {
    const name = `e2e-viewer-concurrent-${Date.now()}`;
    const backend = resolve("../backend");
    const payload = execFileSync(
      resolve(backend, ".venv/bin/python"),
      [
        "-c",
        "import sys; from tests.factories.geometry import three_mf; sys.stdout.buffer.write(three_mf())",
      ],
      { cwd: backend },
    );
    const upload = await page.request.post("/api/v1/ingest/model", {
      multipart: {
        file: { name: `${name}.3mf`, mimeType: "model/3mf", buffer: payload },
        model_name: name,
      },
    });
    expect(upload.status()).toBe(202);
    const jobId = (await upload.json()).job_id;
    let modelId = 0;
    let fileId = 0;
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
    try {
      const jobsUrl =
        "/api/v1/jobs?include_system=true&kind=derivatives.viewer_stl&terminal_limit=100";
      const previousJobs = await (await page.request.get(jobsUrl)).json();
      const previousIds = new Set(previousJobs.map((job: { job_id: string }) => job.job_id));
      const responses = await Promise.all(
        Array.from({ length: 4 }, () => page.request.get(`/api/v1/files/${fileId}/stl`)),
      );
      expect(responses.every((response) => [200, 202].includes(response.status()))).toBe(true);
      await expect
        .poll(async () => (await page.request.get(`/api/v1/files/${fileId}/stl`)).status(), {
          timeout: 60_000,
        })
        .toBe(200);
      await page.goto(`/models/${modelId}`);
      await expect(page.getByRole("button", { name: "Screenshot", exact: true })).toBeEnabled({
        timeout: 60_000,
      });
      await expect(page.locator("canvas")).toBeVisible();
      const jobs = await (await page.request.get(jobsUrl)).json();
      const previews = jobs.filter((job: { job_id: string }) => !previousIds.has(job.job_id));
      expect(previews).toHaveLength(1);
      expect(previews[0].attempts).toBe(1);
    } finally {
      await page.request.delete(`/api/v1/models/${modelId}`);
    }
  });
});
