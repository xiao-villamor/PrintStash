/** Real repository meshes keep physical scale even when stored measurements are unavailable. */
import { readFileSync } from "node:fs";
import type { FamilyMemberItem, FamilyPage, FamilyRead } from "../../src/types/families";
import { silhouetteAreaRatio } from "../similarity-pixels";
import { test, expect } from "./helpers";

const API = `http://127.0.0.1:${process.env.PLAYWRIGHT_REAL_API_PORT ?? 8410}`;
test.use({ trace: "retain-on-failure", actionTimeout: 15_000 });

test.describe("Family preview independence", () => {
  test("frames complete meshes without stored dimensions", async ({ page }) => {
    const prefix = `family-bounds-${Date.now()}`;
    const models: { id: number; name: string }[] = [];
    let family: FamilyRead | undefined;
    try {
      for (const scale of [1, 0.5]) {
        const name = `${prefix}-${scale}`;
        const mesh = readFileSync(
          new URL("../../../testdata/Calibration Cube.stl", import.meta.url),
        );
        Buffer.from(name).copy(mesh, 0, 0, 80);
        for (let face = 0; face < mesh.readUInt32LE(80); face++)
          for (let coordinate = 0; coordinate < 9; coordinate++) {
            const at = 84 + face * 50 + 12 + coordinate * 4;
            mesh.writeFloatLE(mesh.readFloatLE(at) * scale, at);
          }
        const upload = await page.request.post(`${API}/api/v1/ingest/model`, {
          multipart: {
            model_name: name,
            file: { name: `${name}.stl`, mimeType: "model/stl", buffer: mesh },
          },
        });
        expect(upload.status()).toBe(202);
        const { job_id }: { job_id: string } = await upload.json();
        let modelId = 0;
        await expect
          .poll(async () => {
            const job: { state: string; model_id: number | null } = await (
              await page.request.get(`${API}/api/v1/ingest/jobs/${job_id}`)
            ).json();
            modelId = job.model_id ?? 0;
            return job.state;
          })
          .toBe("completed");
        models.push({ id: modelId, name });
      }
      const created = await page.request.post(`${API}/api/v1/families`, {
        data: {
          name: prefix,
          canonical_model_id: models[0].id,
          members: models.map(({ id }) => ({ model_id: id })),
        },
      });
      expect(created.status()).toBe(201);
      family = await created.json();
      // Fault-inject the documented nullable metadata contract. Source bytes
      // and both preview requests still come from the real backend.
      await page.route(`**/api/v1/families/${family!.id}/members**`, async (route) => {
        const response = await route.fetch();
        const body: FamilyPage<FamilyMemberItem> = await response.json();
        await route.fulfill({
          response,
          json: {
            ...body,
            items: body.items.map((member) => ({
              ...member,
              preview_file: member.preview_file ? { ...member.preview_file, metadata: null } : null,
            })),
          },
        });
      });
      await page.goto(`/families/${family!.slug}`);
      for (const model of models)
        await page
          .getByRole("checkbox", { name: `Select for comparison: ${model.name}`, exact: true })
          .check();
      await page.getByRole("button", { name: "Compare two Models (2/2)" }).click();
      const comparison = page.getByRole("dialog", { name: "Compare two Models" });
      await expect(comparison.locator("canvas")).toHaveCount(2);
      await expect(comparison.getByRole("status", { name: "Loading 3D preview" })).toHaveCount(0);
      const ratio = await silhouetteAreaRatio(
        page,
        await comparison.locator("canvas").nth(0).screenshot(),
        await comparison.locator("canvas").nth(1).screenshot(),
      );
      expect(ratio).toBeGreaterThan(3);
      expect(ratio).toBeLessThan(5.5);
      await expect(comparison.getByRole("row", { name: /Dimensions/ })).toHaveText(/— × — × —/);
    } finally {
      if (family) {
        await page.request.delete(`${API}/api/v1/families/${family.id}?version=${family.version}`);
        await page.request.delete(
          `${API}/api/v1/families/${family.id}/purge?version=${family.version + 1}`,
        );
      }
      for (const model of models) await page.request.delete(`${API}/api/v1/models/${model.id}`);
    }
  });

  test("joins an existing Family beside an unsupported preview", async ({ page }, testInfo) => {
    const prefix = `family-join-${Date.now()}`;
    const models: { id: number; name: string }[] = [];
    let family: FamilyRead | undefined;
    const screenshotStyle =
      '[aria-label="Open Tanstack query devtools"], [title="Open Tanstack query devtools"] { display: none !important; }';
    try {
      const cube = readFileSync(new URL("../../../testdata/Calibration Cube.stl", import.meta.url));
      Buffer.from(prefix).copy(cube, 0, 0, 80);
      const gcode = Buffer.concat([
        readFileSync(
          new URL("../../../backend/tests/fixtures/real_orca_ender3_benchy.gcode", import.meta.url),
        ),
        Buffer.from(`\n; ${prefix}\n`),
      ]);
      for (const file of [
        { extension: "stl", buffer: cube, mimeType: "model/stl" },
        { extension: "gcode", buffer: gcode, mimeType: "text/plain" },
      ]) {
        const name = `${prefix}-${file.extension}`;
        const endpoint = file.extension === "gcode" ? "orca" : "model";
        const upload = await page.request.post(`${API}/api/v1/ingest/${endpoint}`, {
          multipart: {
            model_name: name,
            file: {
              name: `${name}.${file.extension}`,
              mimeType: file.mimeType,
              buffer: file.buffer,
            },
          },
        });
        expect(upload.status()).toBe(202);
        const { job_id }: { job_id: string } = await upload.json();
        let modelId = 0;
        await expect
          .poll(async () => {
            const job: { state: string; model_id: number | null } = await (
              await page.request.get(`${API}/api/v1/ingest/jobs/${job_id}`)
            ).json();
            modelId = job.model_id ?? 0;
            return job.state;
          })
          .toBe("completed");
        models.push({ id: modelId, name });
      }
      const created = await page.request.post(`${API}/api/v1/families`, {
        data: {
          name: prefix,
          canonical_model_id: models[0].id,
          members: [{ model_id: models[0].id }],
        },
      });
      expect(created.status()).toBe(201);
      family = await created.json();
      await page.goto(`/models/${models[1].id}`);
      await page.getByRole("button", { name: "Add to Family", exact: true }).click();
      const join = page.getByRole("dialog", { name: "Add to Family" });
      await join.getByRole("textbox", { name: "Search Families…" }).fill(prefix);
      await join.getByRole("radio", { name: new RegExp(prefix) }).check();
      await join.getByRole("combobox", { name: "Variation" }).selectOption("print_variant");
      for (const [label, width, height] of [
        ["desktop", 1280, 900],
        ["mobile", 390, 844],
      ] as const) {
        await page.setViewportSize({ width, height });
        await join.screenshot({
          path: testInfo.outputPath(`family-join-${label}.png`),
          style: screenshotStyle,
        });
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
          true,
        );
      }
      await join.getByRole("button", { name: "Add to Family", exact: true }).click();
      await expect(page).toHaveURL(new RegExp(`/families/${family!.id}$`));
      family = await (await page.request.get(`${API}/api/v1/families/${family!.id}`)).json();
      expect(family!.canonical_model_id).toBe(models[0].id);
      await page.setViewportSize({ width: 1280, height: 900 });
      for (const model of models)
        await page
          .getByRole("checkbox", { name: `Select for comparison: ${model.name}`, exact: true })
          .check();
      await page.getByRole("button", { name: "Compare two Models (2/2)" }).click();
      const comparison = page.getByRole("dialog", { name: "Compare two Models" });
      await expect(comparison.locator("canvas")).toHaveCount(1);
      await expect(comparison.getByRole("status", { name: "Loading 3D preview" })).toHaveCount(0);
      await expect(comparison.getByText(/3D preview unavailable/)).toBeVisible();
      await expect(comparison.getByRole("row", { name: "G-code Revisions 0 1" })).toBeVisible();
      await page.goto(`/models/${models[1].id}`);
      await expect(page.getByRole("link", { name: "Manage Family" })).toBeVisible();
      await expect(
        page.getByRole("link", { name: models[0].name, exact: true }).first(),
      ).toBeVisible();
      await expect(page.getByRole("link", { name: models[0].name, exact: true })).toHaveCount(1);
      await expect(page.getByRole("link", { name: "Back", exact: true })).toHaveAttribute(
        "href",
        "/",
      );
      for (const [label, width, height] of [
        ["desktop", 1280, 900],
        ["mobile", 390, 844],
      ] as const) {
        await page.setViewportSize({ width, height });
        expect(
          await page
            .getByRole("heading", { name: models[1].name, exact: true })
            .evaluate((node) => {
              const text = document.createRange();
              text.selectNodeContents(node);
              return [...text.getClientRects()].every(
                (rect) => rect.left >= 0 && rect.right <= innerWidth,
              );
            }),
        ).toBe(true);
        await page.screenshot({
          path: testInfo.outputPath(`family-overview-${label}.png`),
          style: screenshotStyle,
        });
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
          true,
        );
      }
      await page.goto(`/?q=${prefix}&browse=families_collapsed`);
      await expect(page.getByRole("heading", { name: prefix, exact: true })).toBeVisible();
      for (const [label, width, height] of [
        ["desktop", 1280, 900],
        ["mobile", 390, 844],
      ] as const) {
        await page.setViewportSize({ width, height });
        await page.screenshot({
          path: testInfo.outputPath(`family-library-${label}.png`),
          style: screenshotStyle,
        });
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
          true,
        );
      }
    } finally {
      if (family) {
        const current: FamilyRead = await (
          await page.request.get(`${API}/api/v1/families/${family.id}`)
        ).json();
        await page.request.delete(`${API}/api/v1/families/${family.id}?version=${current.version}`);
        await page.request.delete(
          `${API}/api/v1/families/${family.id}/purge?version=${current.version + 1}`,
        );
      }
      for (const model of models) await page.request.delete(`${API}/api/v1/models/${model.id}`);
    }
  });
});
