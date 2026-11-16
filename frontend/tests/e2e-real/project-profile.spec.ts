/** A real uploaded project's file estimates remain separate from observed print results. */
import { execFileSync } from "node:child_process";
import { randomUUID, createHash } from "node:crypto";
import { resolve } from "node:path";
import { test, expect } from "./helpers";

test("shows saved settings for the selected project after import", async ({ page }) => {
  const backend = resolve("../backend");
  const models = new Set<number>();
  try {
    for (const printer of ["Profile printer A", "Profile printer B"]) {
      const name = `e2e-profile-${randomUUID()}`;
      const bytes = execFileSync(
        resolve(backend, ".venv/bin/python"),
        [
          "-c",
          'import sys; from tests.fixtures.three_mf_projects import build_profile_project; sys.stdout.buffer.write(build_profile_project({"print_settings_id":sys.argv[1],"printer_model":sys.argv[2],"filament_type":["PLA"],"layer_height":"0.2"},slices=\'<config><plate><metadata key="index" value="2"/><metadata key="prediction" value="600"/><metadata key="weight" value="12.5"/></plate></config>\'))',
          name,
          printer,
        ],
        { cwd: backend },
      );
      const accepted = await page.request.post("/api/v1/ingest/model", {
        multipart: {
          file: { name: `${name}.3mf`, mimeType: "model/3mf", buffer: bytes },
          model_name: name,
        },
      });
      expect(accepted.status()).toBe(202);
      const { job_id } = await accepted.json();
      let modelId: number | null = null;
      await expect
        .poll(
          async () => {
            const job = await (await page.request.get(`/api/v1/jobs/${job_id}`)).json();
            modelId = job.model_id;
            return job.state;
          },
          { timeout: 60000 },
        )
        .toBe("completed");
      if (modelId === null) throw new Error("Import has no Model receipt");
      models.add(modelId);
      const model = await (await page.request.get(`/api/v1/models/${modelId}`)).json();
      const artifact = model.files.find(
        (file: { sha256: string }) =>
          file.sha256 === createHash("sha256").update(bytes).digest("hex"),
      );
      expect(artifact).toBeDefined();
      await page.goto(`/models/${modelId}?artifact=${artifact.id}`);
      const profile = page.getByRole("region", { name: "3MF project settings" });
      await expect(profile.getByText(printer, { exact: true })).toBeVisible({ timeout: 60000 });
      await expect(profile.getByText("10 min · 12.5 g")).toBeVisible();
      await expect(profile.getByText(/Estimates are not measured print results/)).toBeVisible();
      await page.reload();
      await expect(profile.getByText(printer, { exact: true })).toBeVisible();
      const original = await page.request.get(`/api/v1/files/${artifact.id}/download`);
      expect(
        createHash("sha256")
          .update(await original.body())
          .digest("hex"),
      ).toBe(artifact.sha256);
    }
  } finally {
    for (const id of models)
      expect((await page.request.delete(`/api/v1/models/${id}`)).status()).toBe(204);
  }
});
