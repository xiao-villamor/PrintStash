/** Real WebGL previews use repository geometry; API fixtures isolate display transforms. */
import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { aModel, aRevision } from "../../src/test-support/factories";
import { aSimilarityCandidate } from "../../src/test-support/similarity";
import type { MetadataRead } from "../../src/types/models";
import { silhouetteOverlap } from "../similarity-pixels";
import { collectPageProblems, useMockApi } from "./_setup";

useMockApi();

function metadata(size: number): MetadataRead {
  return {
    slicer_name: null,
    slicer_version: null,
    printer_model: null,
    nozzle_diameter_mm: null,
    layer_height_mm: null,
    first_layer_height_mm: null,
    infill_percent: null,
    wall_loops: null,
    top_shell_layers: null,
    bottom_shell_layers: null,
    support_material: null,
    nozzle_temperature_c: null,
    bed_temperature_c: null,
    estimated_time_s: null,
    filament_weight_g: null,
    filament_length_mm: null,
    filament_cost: null,
    material_type: null,
    material_brand: null,
    bbox_x_mm: size,
    bbox_y_mm: size,
    bbox_z_mm: size,
    volume_mm3: null,
    triangle_count: 252,
  };
}

/** Shear a copied cube so a reflection has a visibly different outer contour. */
function previewCube(scale: number, reflected: boolean, shear: boolean) {
  const bytes = readFileSync(new URL("../../../testdata/Calibration Cube.stl", import.meta.url));
  const count = bytes.readUInt32LE(80);
  for (let face = 0; face < count; face++) {
    const base = 84 + face * 50;
    for (let vertex = 0; vertex < 3; vertex++) {
      const at = base + 12 + vertex * 12;
      const [x, y, z] = [0, 4, 8].map((offset) => bytes.readFloatLE(at + offset));
      bytes.writeFloatLE((x + (shear ? y * 0.6 : 0)) * scale * (reflected ? -1 : 1), at);
      bytes.writeFloatLE(y * scale, at + 4);
      bytes.writeFloatLE(z * scale, at + 8);
    }
    if (reflected) {
      const first = Buffer.from(bytes.subarray(base + 12, base + 24));
      bytes.copy(bytes, base + 12, base + 24, base + 36);
      first.copy(bytes, base + 24);
    }
    // STLLoader recalculates normals only when absent; derive them from the new face.
    const vertices = [0, 1, 2].map((vertex) =>
      [0, 4, 8].map((axis) => bytes.readFloatLE(base + 12 + vertex * 12 + axis)),
    );
    const u = vertices[1].map((v, axis) => v - vertices[0][axis]);
    const v = vertices[2].map((value, axis) => value - vertices[0][axis]);
    const normal = [
      u[1] * v[2] - u[2] * v[1],
      u[2] * v[0] - u[0] * v[2],
      u[0] * v[1] - u[1] * v[0],
    ];
    const length = Math.hypot(...normal) || 1;
    normal.forEach((value, axis) => bytes.writeFloatLE(value / length, base + axis * 4));
  }
  return bytes;
}

const screenshotStyle =
  '[aria-label="Open Tanstack query devtools"], [title="Open Tanstack query devtools"] { display: none !important; }';

test.describe("Similarity preview compensation", () => {
  for (const variant of ["scale", "mirror"] as const) {
    test(`shows ${variant} compensation without rewriting source geometry`, async ({ page }) => {
      test.setTimeout(60_000);
      const problems = await collectPageProblems(page);
      await page.setViewportSize({ width: 1280, height: 800 });
      const scale = variant === "scale" ? 2 : 1;
      const meshes = [
        previewCube(1, false, variant === "mirror"),
        previewCube(scale, variant === "mirror", variant === "mirror"),
      ];
      const hashes = meshes.map((bytes) => createHash("sha256").update(bytes).digest("hex"));
      const transform = [
        [variant === "mirror" ? -1 : 0.5, 0, 0, 0],
        [0, 1 / scale, 0, 0],
        [0, 0, 1 / scale, 0],
        [0, 0, 0, 1],
      ];
      const candidate = aSimilarityCandidate({
        primary_lineage_key: "preview",
        evidence_class: variant === "scale" ? "rescaled" : "mirrored",
        summary: { transform, scale_factor: 1 / scale, mirrored: variant === "mirror" },
        observations: [
          {
            id: 1,
            lineage_key: "preview",
            fingerprint_a_id: 1,
            fingerprint_b_id: 2,
            input_hash_a: hashes[0],
            input_hash_b: hashes[1],
            kind: "whole",
            multiplicity_a: 1,
            multiplicity_b: 1,
            evidence: {},
          },
        ],
      });
      await page.route("**/api/v1/similarity/candidates/1", (route) =>
        route.fulfill({ json: candidate }),
      );
      for (let index = 0; index < 2; index++) {
        const id = index + 1;
        const file = aRevision({
          id,
          model_id: id,
          file_type: "stl",
          original_filename: "Calibration Cube.stl",
          sha256: hashes[index],
          metadata: metadata(variant === "scale" && index ? 40 : 32),
        });
        await page.route(`**/api/v1/models/${id}`, (route) =>
          route.fulfill({ json: aModel({ id, files: [file] }) }),
        );
        await page.route(`**/api/v1/models/${id}/print-jobs`, (route) =>
          route.fulfill({ json: [] }),
        );
        await page.route(`**/api/v1/files/${id}/stl`, (route) =>
          route.fulfill({ body: meshes[index], contentType: "model/stl" }),
        );
      }
      await page.goto("/library/similar/1");
      await expect(page.locator("canvas")).toHaveCount(2, { timeout: 30_000 });
      const first = page.locator("canvas").nth(0),
        second = page.locator("canvas").nth(1);
      const overlap = async () =>
        silhouetteOverlap(
          page,
          await first.screenshot({ style: screenshotStyle }),
          await second.screenshot({ style: screenshotStyle }),
        );
      await expect.poll(overlap).toBeGreaterThan(0.05);
      await expect.poll(overlap).toBeLessThan(0.9);
      const control = page.getByRole("checkbox", {
        name: variant === "scale" ? "Compensate scale" : "Compensate reflection",
      });
      await control.click();
      await expect(control).toBeChecked();
      await expect.poll(overlap).toBeGreaterThan(0.98);
      if (variant === "mirror") {
        const before = await first.screenshot({ style: screenshotStyle });
        await page.getByRole("checkbox", { name: "Overlay meshes" }).click();
        await expect
          .poll(async () => (await first.screenshot({ style: screenshotStyle })).equals(before))
          .toBe(false);
        await page.getByRole("checkbox", { name: "Overlay meshes" }).click();
        await expect.poll(overlap).toBeGreaterThan(0.98);
      }
      await control.click();
      await expect.poll(overlap).toBeLessThan(0.9);
      expect(meshes.map((bytes) => createHash("sha256").update(bytes).digest("hex"))).toEqual(
        hashes,
      );
      expect(problems).toEqual([]);
    });
  }
});
