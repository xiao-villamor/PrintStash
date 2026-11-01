/** Matches stay usable at desktop size; real WebGL geometry verifies comparison display transforms. */
import { expect, test, type Page } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { aModel, aRevision } from "../../src/test-support/factories";
import { aSimilarityCandidate, similarityStatus } from "../../src/test-support/similarity";
import type { MetadataRead } from "../../src/types/models";
import { silhouetteAreaRatio, silhouetteOverlap } from "../similarity-pixels";
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
    layer_count: null,
    exposure_time_s: null,
    resin_volume_ml: null,
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
    volume_measurement: {
      state: "legacy_unassessed",
      unit: "mm3",
      method: null,
      value_mm3: null,
      cause: null,
    },
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

/** Require useful framing, not merely a nonempty WebGL canvas. */
async function meshForegroundFraction(page: Page, screenshot: Buffer) {
  return page.evaluate(
    async (bytes) => {
      const bitmap = await createImageBitmap(
        new Blob([new Uint8Array(bytes)], { type: "image/png" }),
      );
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = 256;
      const context = canvas.getContext("2d")!;
      context.drawImage(bitmap, 0, 0, 256, 256);
      bitmap.close();
      const pixels = context.getImageData(0, 0, 256, 256).data;
      let foreground = 0;
      for (let at = 0; at < pixels.length; at += 4) {
        if (
          Math.max(
            ...[0, 1, 2].map((channel) => Math.abs(pixels[at + channel] - pixels[channel])),
          ) > 30
        )
          foreground++;
      }
      return foreground / (256 * 256);
    },
    [...screenshot],
  );
}

async function mockComparison(page: Page, names: [string, string]) {
  const mesh = previewCube(1, false, false);
  const hash = createHash("sha256").update(mesh).digest("hex");
  const candidate = aSimilarityCandidate({
    model_a: { id: 1, name: names[0], slug: "bracket", thumbnail_file_id: null },
    model_b: { id: 2, name: names[1], slug: "bracket-copy", thumbnail_file_id: null },
    primary_lineage_key: "preview",
    observations: [
      {
        id: 1,
        lineage_key: "preview",
        fingerprint_a_id: 1,
        fingerprint_b_id: 2,
        input_hash_a: hash,
        input_hash_b: hash,
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
  for (const id of [1, 2]) {
    const file = aRevision({
      id,
      model_id: id,
      file_type: "stl",
      original_filename: `${names[id - 1]}.stl`,
      sha256: hash,
      metadata: metadata(20),
    });
    await page.route(`**/api/v1/models/${id}`, (route) =>
      route.fulfill({ json: aModel({ id, name: names[id - 1], files: [file] }) }),
    );
    await page.route(`**/api/v1/models/${id}/print-jobs`, (route) => route.fulfill({ json: [] }));
    await page.route(`**/api/v1/files/${id}/stl`, (route) =>
      route.fulfill({ body: mesh, contentType: "model/stl" }),
    );
  }
}

test.describe("Similarity desktop review", () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.route("**/api/v1/similarity/status", (route) =>
      route.fulfill({ json: similarityStatus() }),
    );
    await page.route("**/api/v1/similarity/runs", (route) =>
      route.fulfill({ json: { items: [], next_cursor: null } }),
    );
    await page.route("**/api/v1/similarity/candidates?*", (route) =>
      route.fulfill({
        json: {
          items: [
            aSimilarityCandidate({
              model_a: { id: 1, name: "Bracket", slug: "bracket", thumbnail_file_id: 1 },
              model_b: { id: 2, name: "Bracket copy", slug: "bracket-copy", thumbnail_file_id: 1 },
            }),
          ],
          next_cursor: null,
        },
      }),
    );
    await page.route("**/api/v1/similarity/candidates/1", (route) =>
      route.fulfill({ json: aSimilarityCandidate() }),
    );
    for (const id of [1, 2]) {
      await page.route(`**/api/v1/models/${id}`, (route) =>
        route.fulfill({ json: aModel({ id, files: [] }) }),
      );
      await page.route(`**/api/v1/models/${id}/print-jobs`, (route) => route.fulfill({ json: [] }));
    }
  });

  test("shows readable matches before analysis configuration at 1080p", async ({
    page,
  }, testInfo) => {
    await page.goto("/library/similar");
    await expect(page.getByRole("tab", { name: "Matches", exact: true })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await expect(page.getByRole("tab", { name: "To review", exact: true })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    const candidate = page.getByRole("listitem").filter({ hasText: "Bracket copy" });
    await expect(candidate.getByRole("link", { name: "Compare", exact: true })).toBeInViewport();
    await expect(candidate.getByRole("link", { name: "Bracket", exact: true })).toBeVisible();
    await expect(page.getByRole("form", { name: "Similar models", exact: true })).toBeHidden();
    await expect(page.getByRole("combobox", { name: "All formats", exact: true })).toBeHidden();
    await expect(candidate.locator("img")).toHaveCount(2);
    for (const image of await candidate.locator("img").all()) {
      await expect
        .poll(() =>
          image.evaluate(
            (element: HTMLImageElement) => element.complete && element.naturalWidth > 0,
          ),
        )
        .toBe(true);
      const bounds = await image.boundingBox();
      expect(bounds).not.toBeNull();
      expect(bounds!.width).toBeGreaterThanOrEqual(64);
      expect(bounds!.height).toBeGreaterThanOrEqual(64);
    }
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: testInfo.outputPath("similar-matches-1080p.png") });

    await page.getByRole("tab", { name: "Analysis settings", exact: true }).click();
    const settings = page.getByRole("form", { name: "Similar models", exact: true });
    await expect(
      settings.getByRole("checkbox", { name: "Enable similarity analysis" }),
    ).toBeVisible();
    await expect(page.getByRole("link", { name: "Compare", exact: true })).toBeHidden();
    await page.getByRole("tab", { name: "Analysis history", exact: true }).click();
    await expect(settings).toBeHidden();
    await expect(page.getByRole("tab", { name: "Analysis history", exact: true })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await page.getByRole("tab", { name: "Matches", exact: true }).click();
    await expect(candidate.getByRole("link", { name: "Compare", exact: true })).toBeInViewport();
  });

  test("restores the review filters after returning from a comparison", async ({
    page,
  }, testInfo) => {
    await page.goto("/library/similar");
    await page.getByRole("tab", { name: "Later", exact: true }).click();
    const filters = page.getByRole("button", { name: /^Filters/ });
    await filters.click();
    await expect(filters).toHaveAttribute("aria-expanded", "true");
    await page.getByRole("combobox", { name: "All formats", exact: true }).selectOption("stl");
    await page.getByRole("combobox", { name: "All sources", exact: true }).selectOption("vault");
    const confidence = page.getByRole("slider", { name: "Minimum confidence", exact: true });
    await confidence.focus();
    await confidence.press("End");
    for (let step = 0; step < 6; step++) await confidence.press("ArrowLeft");
    await expect(page).toHaveURL((url) => url.searchParams.get("confidence") === "94");
    const reviewUrl = page.url();
    const compare = page.getByRole("link", { name: "Compare", exact: true });
    await expect(compare).toBeInViewport();
    const sourceBox = await page
      .getByRole("combobox", { name: "All sources", exact: true })
      .boundingBox();
    const compareBox = await compare.boundingBox();
    expect(sourceBox).not.toBeNull();
    expect(compareBox).not.toBeNull();
    expect(compareBox!.y).toBeGreaterThan(sourceBox!.y + sourceBox!.height);
    await page.screenshot({ path: testInfo.outputPath("similar-filters-1080p.png") });
    await compare.click();
    await expect(page.getByRole("heading", { name: "Compare models", exact: true })).toBeVisible();
    await page.getByRole("link", { name: "Back to similar models", exact: true }).click();
    await expect(page).toHaveURL(reviewUrl);
    await expect(page.getByRole("tab", { name: "Later", exact: true })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    if ((await filters.getAttribute("aria-expanded")) === "false") await filters.click();
    await expect(page.getByRole("combobox", { name: "All formats", exact: true })).toHaveValue(
      "stl",
    );
    await expect(page.getByRole("combobox", { name: "All sources", exact: true })).toHaveValue(
      "vault",
    );
    await expect(page.getByRole("slider", { name: "Minimum confidence", exact: true })).toHaveValue(
      "94",
    );
    await expect(compare).toBeInViewport();
  });
});

test.describe("Model detail Similar controls", () => {
  for (const width of [320, 1920]) {
    test(`matches the other detail sections with no model matches at ${width}px`, async ({
      page,
    }) => {
      await page.setViewportSize({ width, height: 1080 });
      await page.route("**/api/v1/similarity/status", (route) =>
        route.fulfill({ json: similarityStatus() }),
      );
      await page.route("**/api/v1/similarity/candidates?*", (route) =>
        route.fulfill({ json: { items: [], next_cursor: null } }),
      );
      await page.goto("/models/1");
      const pane = page.getByTestId("model-detail-sidebar");
      await pane.getByRole("tab", { name: "Settings", exact: true }).click();
      const settingsHeading = pane.getByRole("heading", { name: "Print Settings", exact: true });
      await expect(settingsHeading).toBeVisible();
      const settingsStyle = await settingsHeading.evaluate((heading) => {
        const title = getComputedStyle(heading);
        const frame = getComputedStyle(heading.nextElementSibling!);
        return {
          fontSize: title.fontSize,
          fontWeight: title.fontWeight,
          color: title.color,
          backgroundColor: frame.backgroundColor,
          borderColor: frame.borderColor,
          borderRadius: frame.borderRadius,
        };
      });
      await pane.getByRole("tab", { name: "Similar", exact: true }).click();
      const heading = pane.getByRole("heading", { name: "Model matches", exact: true });
      const frame = pane.getByRole("region", { name: "Model matches", exact: true });
      await expect(heading).toBeVisible();
      const empty = frame.getByText("No candidates to review", { exact: true });
      await expect(empty).toBeVisible();
      expect(
        await heading.evaluate((element) => {
          const style = getComputedStyle(element);
          return { fontSize: style.fontSize, fontWeight: style.fontWeight, color: style.color };
        }),
      ).toEqual({
        fontSize: settingsStyle.fontSize,
        fontWeight: settingsStyle.fontWeight,
        color: settingsStyle.color,
      });
      expect(
        await frame.evaluate((element) => {
          const style = getComputedStyle(element);
          return {
            backgroundColor: style.backgroundColor,
            borderColor: style.borderColor,
            borderRadius: style.borderRadius,
          };
        }),
      ).toEqual({
        backgroundColor: settingsStyle.backgroundColor,
        borderColor: settingsStyle.borderColor,
        borderRadius: settingsStyle.borderRadius,
      });
      const titleBounds = await heading.boundingBox();
      const frameBounds = await frame.boundingBox();
      expect(titleBounds!.y + titleBounds!.height).toBeLessThanOrEqual(frameBounds!.y);
      const emptyBounds = await empty.locator("..").boundingBox();
      expect(emptyBounds!.height).toBeLessThan(180);
      await expect(frame.getByRole("button", { name: "Find similar", exact: true })).toBeEnabled();
      expect(
        await frame.evaluate((element) => element.scrollWidth <= element.clientWidth + 1),
      ).toBe(true);
      await pane.screenshot({ path: test.info().outputPath(`similar-empty-${width}.png`) });
    });
  }

  for (const scenario of [
    {
      label: "default480desktop",
      width: 1920,
      height: 1080,
      pane: 480,
      locale: "en",
      tab: "Similar",
      states: ["To review", "Confirmed", "Later", "Rejected"],
      filters: "Filters",
      find: "Find similar",
      resize: "Resize details panel",
    },
    {
      label: "minimum400desktopSpanish",
      width: 1920,
      height: 1080,
      pane: 400,
      locale: "es",
      tab: "Similares",
      states: ["Por revisar", "Confirmados", "Más tarde", "Rechazados"],
      filters: "Filtros",
      find: "Buscar similares",
      resize: "Cambiar el tamaño del panel de detalles",
    },
    {
      label: "390mobileSpanish",
      width: 390,
      height: 844,
      pane: 390,
      locale: "es",
      tab: "Similares",
      states: ["Por revisar", "Confirmados", "Más tarde", "Rechazados"],
      filters: "Filtros",
      find: "Buscar similares",
      resize: "Cambiar el tamaño del panel de detalles",
    },
    {
      label: "320mobile",
      width: 320,
      height: 844,
      pane: 320,
      locale: "en",
      tab: "Similar",
      states: ["To review", "Confirmed", "Later", "Rejected"],
      filters: "Filters",
      find: "Find similar",
      resize: "Resize details panel",
    },
  ]) {
    test(`keeps every review action reachable in ${scenario.label}`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width: scenario.width, height: scenario.height });
      await page.addInitScript(
        (locale) => localStorage.setItem("printstash.locale", locale),
        scenario.locale,
      );
      await page.route("**/api/v1/similarity/status", (route) =>
        route.fulfill({ json: similarityStatus() }),
      );
      await page.route("**/api/v1/similarity/candidates?*", (route) =>
        route.fulfill({ json: { items: [aSimilarityCandidate()], next_cursor: null } }),
      );
      await page.goto("/models/1");
      const pane = page.getByTestId("model-detail-sidebar");
      if (scenario.pane === 400) {
        const resize = page.getByRole("separator", { name: scenario.resize, exact: true });
        await resize.focus();
        for (let step = 0; step < 5; step++) await resize.press("ArrowRight");
      }
      await expect
        .poll(async () => (await pane.boundingBox())!.width)
        .toBeCloseTo(scenario.pane, 0);
      await pane.getByRole("tab", { name: scenario.tab, exact: true }).click();
      const firstState = pane.getByRole("tab", { name: scenario.states[0], exact: true });
      await firstState.scrollIntoViewIfNeeded();
      await page.evaluate(() => document.fonts.ready);
      const filters = pane.getByRole("button", { name: scenario.filters, exact: true });
      const find = pane.getByRole("button", { name: scenario.find, exact: true });
      await find.evaluate((element) => element.scrollIntoView({ block: "center" }));
      const review = firstState.locator("..");
      expect(
        await review.evaluate((element) => element.scrollWidth <= element.clientWidth + 1),
      ).toBe(true);
      expect(
        await review
          .locator("..")
          .evaluate((element) => element.scrollWidth <= element.clientWidth + 1),
      ).toBe(true);
      for (const state of scenario.states) {
        const tab = pane.getByRole("tab", { name: state, exact: true });
        await expect(tab).toBeInViewport({ ratio: 1 });
        expect(
          await tab.evaluate((element) => element.scrollWidth <= element.clientWidth + 1),
        ).toBe(true);
      }
      await expect(filters).toBeInViewport({ ratio: 1 });
      await expect(find).toBeInViewport({ ratio: 1 });
      await expect(find).toBeEnabled();
      const filtersBox = await filters.boundingBox();
      const findBox = await find.boundingBox();
      expect(filtersBox).not.toBeNull();
      expect(findBox).not.toBeNull();
      expect(Math.abs(filtersBox!.y - findBox!.y)).toBeLessThanOrEqual(1);
      expect(Math.abs(filtersBox!.height - findBox!.height)).toBeLessThanOrEqual(1);
      await find.click({ trial: true });
      const later = pane.getByRole("tab", { name: scenario.states[2], exact: true });
      const [response] = await Promise.all([
        page.waitForResponse((response) => {
          const url = new URL(response.url());
          return (
            url.pathname === "/api/v1/similarity/candidates" &&
            url.searchParams.get("review_state") === "later" &&
            url.searchParams.get("model_id") === "1"
          );
        }),
        later.click(),
      ]);
      expect(response.ok()).toBe(true);
      await expect(later).toHaveAttribute("aria-selected", "true");
      expect(await pane.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(
        true,
      );
      await page.screenshot({ path: testInfo.outputPath(`model-similar-${scenario.label}.png`) });
      await filters.click();
      await expect(filters).toHaveAttribute("aria-expanded", "true");
      await expect(pane.getByRole("combobox").first()).toBeVisible();
    });
  }
});

test.describe("Similarity comparison layout", () => {
  test.describe.configure({ timeout: 60_000 });
  test("fits the complete comparison workspace at 1080p", async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await mockComparison(page, ["Bracket", "Bracket copy"]);
    await page.goto("/library/similar/1");
    for (const name of ["Confirm match", "Reject match", "Review later"]) {
      const action = page.getByRole("button", { name, exact: true });
      await expect(action).toBeInViewport({ ratio: 1 });
      await action.click({ trial: true });
    }
    await expect(page.locator("canvas")).toHaveCount(2, { timeout: 30_000 });
    const previews = page.locator("canvas");
    await expect
      .poll(async () =>
        silhouetteOverlap(
          page,
          await previews.nth(0).screenshot({ style: screenshotStyle }),
          await previews.nth(1).screenshot({ style: screenshotStyle }),
        ),
      )
      .toBeGreaterThan(0.98);
    const table = page.getByRole("table", { name: "Measurements and print history", exact: true });
    await expect(table).toBeInViewport({ ratio: 1 });
    await expect(
      table.getByRole("rowheader", { name: "Original dimensions", exact: true }),
    ).toBeVisible();
    for (const label of [
      "Sampled Chamfer distance",
      "Sampled Hausdorff distance",
      "Voxel overlap",
      "Independent surface samples",
    ]) {
      await expect(page.getByText(label, { exact: true })).toBeInViewport({ ratio: 1 });
    }
    for (const name of ["Compensate scale", "Compensate reflection"]) {
      await expect(page.getByRole("checkbox", { name, exact: true })).toBeInViewport({ ratio: 1 });
    }
    const tableBounds = (await table.boundingBox())!;
    const canvasBounds = await Promise.all([
      previews.nth(0).boundingBox(),
      previews.nth(1).boundingBox(),
    ]);
    for (const bounds of canvasBounds) {
      expect(bounds).not.toBeNull();
      expect(bounds!.height).toBeGreaterThanOrEqual(300);
      expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(1080);
      expect(tableBounds.x).toBeGreaterThanOrEqual(bounds!.x + bounds!.width);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await page.screenshot({ path: testInfo.outputPath("comparison-visible-details-1080p.png") });
  });

  test("contains long model names across viewport sizes", async ({ page }, testInfo) => {
    const names: [string, string] = ["A".repeat(255), "B".repeat(255)];
    await page.setViewportSize({ width: 1920, height: 1080 });
    await mockComparison(page, names);
    await page.goto("/library/similar/1");
    await expect(page.locator("canvas")).toHaveCount(2, { timeout: 30_000 });
    await expect
      .poll(async () =>
        silhouetteOverlap(
          page,
          await page.locator("canvas").nth(0).screenshot({ style: screenshotStyle }),
          await page.locator("canvas").nth(1).screenshot({ style: screenshotStyle }),
        ),
      )
      .toBeGreaterThan(0.98);
    for (const width of [1920, 390, 320]) {
      await page.setViewportSize({ width, height: width === 1920 ? 1080 : 844 });
      await expect(page.getByRole("button", { name: "Confirm match", exact: true })).toBeVisible();
      const table = page.getByRole("table", {
        name: "Measurements and print history",
        exact: true,
      });
      await expect(table).toBeVisible();
      for (const [index, name] of names.entries()) {
        await expect(
          table.getByRole("columnheader", {
            name: `${index === 0 ? "A" : "B"} ${name}`,
            exact: true,
          }),
        ).toBeVisible();
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
      const tableBounds = (await table.boundingBox())!;
      expect(tableBounds.x).toBeGreaterThanOrEqual(0);
      expect(tableBounds.x + tableBounds.width).toBeLessThanOrEqual(width);
      for (const preview of await page.locator("canvas").all()) {
        const bounds = (await preview.boundingBox())!;
        expect(bounds.x).toBeGreaterThanOrEqual(0);
        expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
      }
      await page.screenshot({
        path: testInfo.outputPath(`comparison-long-names-${width}.png`),
        fullPage: true,
      });
    }
  });
});

test.describe("Similarity preview compensation", () => {
  for (const variant of ["scale", "mirror"] as const) {
    test(`shows ${variant} compensation without rewriting source geometry`, async ({ page }) => {
      test.setTimeout(60_000);
      const problems = await collectPageProblems(page);
      await page.setViewportSize({ width: 1920, height: 1080 });
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
      if (variant === "scale") {
        const images = await Promise.all([
          first.screenshot({ style: screenshotStyle }),
          second.screenshot({ style: screenshotStyle }),
        ]);
        const occupied = await meshForegroundFraction(page, images[1]);
        expect(occupied).toBeGreaterThan(0.15);
        expect(occupied).toBeLessThan(0.85);
        const areaRatio = await silhouetteAreaRatio(page, images[0], images[1]);
        expect(areaRatio).toBeGreaterThan(0.15);
        expect(areaRatio).toBeLessThan(0.4);
      }
      const canvasBounds = await Promise.all([first.boundingBox(), second.boundingBox()]);
      for (const bounds of canvasBounds) {
        expect(bounds).not.toBeNull();
        expect(bounds!.width).toBeGreaterThanOrEqual(400);
        expect(bounds!.height).toBeGreaterThanOrEqual(300);
        expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(1080);
      }
      expect(Math.abs(canvasBounds[0]!.y - canvasBounds[1]!.y)).toBeLessThanOrEqual(1);
      await expect(page.getByRole("button", { name: "Side by side", exact: true })).toHaveAttribute(
        "aria-pressed",
        "true",
      );
      await expect(
        page.getByRole("table", { name: "Measurements and print history", exact: true }),
      ).toBeVisible();
      const control = page.getByRole("checkbox", {
        name: variant === "scale" ? "Compensate scale" : "Compensate reflection",
      });
      await control.click();
      await expect(control).toBeChecked();
      await expect.poll(overlap).toBeGreaterThan(0.98);
      if (variant === "mirror") {
        const before = await first.screenshot({ style: screenshotStyle });
        await page.getByRole("button", { name: "Overlay meshes", exact: true }).click();
        await expect(page.locator("canvas")).toHaveCount(1);
        await expect(
          page.getByRole("button", { name: "Overlay meshes", exact: true }),
        ).toHaveAttribute("aria-pressed", "true");
        await expect
          .poll(async () => (await first.screenshot({ style: screenshotStyle })).equals(before))
          .toBe(false);
        await page.getByRole("button", { name: "Side by side", exact: true }).click();
        await expect(page.locator("canvas")).toHaveCount(2);
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

test.describe("Multipart review layout", () => {
  test("contains long Multipart names on mobile", async ({ page }, testInfo) => {
    const candidate = aSimilarityCandidate({
      model_a: { id: 1, name: "A".repeat(255), slug: "long-a", thumbnail_file_id: null },
      model_b: { id: 2, name: "B".repeat(255), slug: "long-b", thumbnail_file_id: null },
      allowed_actions: ["create_multipart"],
      summary: {
        composition: [
          { model_id: 1, quantity: 2 },
          { model_id: 2, quantity: 3 },
        ],
      },
    });
    await page.route("**/api/v1/similarity/candidates/1", (route) =>
      route.fulfill({ json: candidate }),
    );
    await page.route("**/api/v1/multipart-models?**", (route) => route.fulfill({ json: [] }));
    await page.goto("/library/similar/1");
    for (const width of [390, 1280]) {
      await page.setViewportSize({ width, height: 844 });
      await page.getByRole("button", { name: "Create multipart model" }).click();
      const dialog = page.getByRole("dialog", { name: "Create multipart model" });
      const bounds = await dialog.boundingBox();
      expect(bounds).not.toBeNull();
      expect(bounds!.x).toBeGreaterThanOrEqual(0);
      expect(bounds!.y).toBeGreaterThanOrEqual(0);
      expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
      expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(844);
      expect(bounds!.height).toBeLessThan(650);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
      await expect(dialog.getByRole("button", { name: "Create multipart model" })).toBeInViewport();
      await expect(dialog.getByTitle("A".repeat(128))).toHaveText("A".repeat(128));
      await expect(dialog.getByTitle("B".repeat(128))).toHaveText("B".repeat(128));
      await dialog.screenshot({ path: testInfo.outputPath(`multipart-${width}.png`) });
      await dialog.getByRole("button", { name: "Cancel" }).click();
      await expect(dialog).toHaveCount(0);
    }
  });
});
