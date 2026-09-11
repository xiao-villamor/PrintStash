/** Manual Family decisions preserve full real meshes and independent Revisions. */
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import type { ModelRead } from "../../src/types/models";
import type { FamilyRead } from "../../src/types/families";
import type { MultipartModelRead } from "../../src/types/multipart-models";
import { silhouetteAreaRatio } from "../similarity-pixels";
import { test, expect } from "./helpers";
import { modelCard, uploadModel } from "./util";

const API = `http://127.0.0.1:${process.env.PLAYWRIGHT_REAL_API_PORT ?? 8410}`;
const screenshotStyle =
  '[aria-label="Open Tanstack query devtools"], [title="Open Tanstack query devtools"] { display: none !important; }';

test.use({ trace: "retain-on-failure", actionTimeout: 15_000 });

test("groups real Benchy variants, compares physical scale and preserves both Revisions", async ({
  page,
}, testInfo) => {
  const prefix = `family-${Date.now()}`;
  const familyName = `${prefix} workshop`;
  const models: { id: number; name: string; hashes: string[] }[] = [];
  let familyId: number | undefined;
  let savedViewId: number | undefined;
  try {
    // ── Import complete repository fixtures through the normal upload flow ──
    for (const scale of [1, 0.5]) {
      const name = `${prefix}-${scale === 1 ? "original" : "half"}`;
      const mesh = readFileSync(new URL("../../../testdata/benchy/3dbenchy.stl", import.meta.url));
      const count = mesh.readUInt32LE(80);
      expect(count).toBeGreaterThan(200_000);
      Buffer.from(name).copy(mesh, 0, 0, 80);
      for (let face = 0; face < count; face++)
        for (let coordinate = 0; coordinate < 9; coordinate++) {
          const at = 84 + face * 50 + 12 + coordinate * 4;
          mesh.writeFloatLE(mesh.readFloatLE(at) * scale, at);
        }
      const gcode = Buffer.concat([
        readFileSync(
          new URL("../../../backend/tests/fixtures/real_orca_ender3_benchy.gcode", import.meta.url),
        ),
        Buffer.from(`\n; Family fixture: ${name}\n`),
      ]);
      await uploadModel(page, name, {
        meshFile: { name: `${name}.stl`, mimeType: "model/stl", buffer: mesh },
        gcodeFile: { name: `${name}.gcode`, mimeType: "text/plain", buffer: gcode },
      });
      const href = await modelCard(page, name).getAttribute("href");
      models.push({
        id: Number(href!.split("/").at(-1)),
        name,
        hashes: [mesh, gcode]
          .map((bytes) => createHash("sha256").update(bytes).digest("hex"))
          .sort(),
      });
    }
    // ── Explicit multi-selection and canonical decision ──
    await page.getByRole("button", { name: "Select", exact: true }).click();
    for (const model of models)
      await page.getByRole("checkbox", { name: `Select ${model.name}`, exact: true }).check();
    await page.getByRole("button", { name: "Create Family", exact: true }).click();
    const create = page.getByRole("dialog", { name: "Create Family" });
    await create.getByRole("textbox", { name: "Family name" }).fill(familyName);
    await expect(create.getByRole("button", { name: "Create Family", exact: true })).toBeDisabled();
    await create.getByRole("radio", { name: models[0].name, exact: true }).check();
    await create.getByRole("button", { name: "Create Family", exact: true }).click();
    await expect(page).toHaveURL(/\/families\/\d+$/);
    familyId = Number(new URL(page.url()).pathname.split("/").at(-1));
    await expect(page.getByRole("heading", { name: familyName, exact: true })).toBeVisible();
    await page.getByRole("button", { name: `Actions for ${models[1].name}`, exact: true }).click();
    await page.getByRole("menuitem", { name: "Make canonical", exact: true }).click();
    const canonical = page.getByRole("dialog", { name: "Make canonical" });
    await canonical
      .getByRole("combobox", { name: "Previous canonical becomes" })
      .selectOption("rescaled");
    await canonical.getByRole("button", { name: "Make canonical", exact: true }).click();
    await expect(canonical).toHaveCount(0);
    const family: FamilyRead = await (
      await page.request.get(`${API}/api/v1/families/${familyId}`)
    ).json();
    expect(family.canonical_model_id).toBe(models[1].id);
    // ── Complete two-sided preview, shared physical scale, desktop and mobile ──
    for (const model of models)
      await page
        .getByRole("checkbox", { name: `Select for comparison: ${model.name}`, exact: true })
        .check();
    await page.getByRole("button", { name: "Compare two Models (2/2)", exact: true }).click();
    const comparison = page.getByRole("dialog", { name: "Compare two Models" });
    await expect(comparison.locator("canvas")).toHaveCount(2);
    await expect(comparison.getByRole("status", { name: "Loading 3D preview" })).toHaveCount(0);
    await expect(comparison.getByRole("table")).toBeVisible();
    await expect(
      comparison.getByRole("row", { name: "Variation Rescaled Canonical" }),
    ).toBeVisible();
    const areaRatio = await silhouetteAreaRatio(
      page,
      await comparison.locator("canvas").nth(0).screenshot({ style: screenshotStyle }),
      await comparison.locator("canvas").nth(1).screenshot({ style: screenshotStyle }),
    );
    // Half linear size occupies approximately a quarter of the image area.
    expect(areaRatio).toBeGreaterThan(3);
    expect(areaRatio).toBeLessThan(5.5);
    for (const [label, width, height] of [
      ["desktop", 1280, 900],
      ["mobile", 390, 844],
    ] as const) {
      await page.setViewportSize({ width, height });
      await comparison.screenshot({
        path: testInfo.outputPath(`family-compare-${label}.png`),
        style: screenshotStyle,
      });
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      ).toBe(true);
    }
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.keyboard.press("Escape");
    await expect(comparison).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "Compare two Models (2/2)", exact: true }),
    ).toBeFocused();
    for (const [label, width, height] of [
      ["desktop", 1280, 900],
      ["mobile", 390, 844],
    ] as const) {
      await page.setViewportSize({ width, height });
      await page.getByRole("heading", { name: familyName, exact: true }).scrollIntoViewIfNeeded();
      await page.screenshot({
        path: testInfo.outputPath(`family-detail-${label}.png`),
        fullPage: true,
        style: screenshotStyle,
      });
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      ).toBe(true);
    }
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.getByRole("button", { name: "Toggle theme" }).click();
    await page.screenshot({
      path: testInfo.outputPath("family-detail-dark.png"),
      fullPage: true,
      style: screenshotStyle,
    });
    await page.getByRole("button", { name: "Toggle theme" }).click();
    // ── Collapsed browsing and Saved View round trip ──
    await page.goto(`/?family_id=${familyId}`);
    await page
      .getByRole("combobox", { name: "Group variations" })
      .selectOption("families_collapsed");
    await expect(
      page
        .getByRole("link")
        .filter({ has: page.getByRole("heading", { name: familyName, exact: true }) }),
    ).toBeVisible();
    for (const model of models) await expect(modelCard(page, model.name)).toHaveCount(0);
    const views = page.locator('main button[data-menu-trigger][aria-haspopup="dialog"]');
    await views.click();
    await page.getByText("Save current view").click();
    await page.getByPlaceholder("Ready to print").fill(prefix);
    const savedResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith("/saved-views") && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Save view", exact: true }).click();
    savedViewId = (await (await savedResponse).json()).id;
    await page.goto("/");
    await views.click();
    await page.getByRole("button", { name: prefix, exact: true }).click();
    await expect(page.getByRole("combobox", { name: "Group variations" })).toHaveValue(
      "families_collapsed",
    );
    await expect(page).toHaveURL(new RegExp(`family_id=${familyId}`));
    // ── One failed preview leaves the other view and metadata usable ──
    const original: ModelRead = await (
      await page.request.get(`${API}/api/v1/models/${models[0].id}`)
    ).json();
    const mesh = original.files.find((file) => file.file_type === "stl");
    expect(mesh).toBeDefined();
    const failingPreview = `**/api/v1/files/${mesh!.id}/stl`;
    await page.route(failingPreview, (route) => route.abort("failed"));
    await page.goto(`/families/${familyId}`);
    for (const model of models)
      await page
        .getByRole("checkbox", { name: `Select for comparison: ${model.name}`, exact: true })
        .check();
    await page.getByRole("button", { name: "Compare two Models (2/2)", exact: true }).click();
    await expect(comparison.getByText("Failed to load 3D preview", { exact: true })).toBeVisible();
    await expect(comparison.locator("canvas")).toHaveCount(1);
    await expect(comparison.getByRole("status", { name: "Loading 3D preview" })).toHaveCount(0);
    await expect(comparison.getByRole("table")).toBeVisible();
    await page.keyboard.press("Escape");
    await page.unroute(failingPreview);
    // ── Removing the Family leaves both Models and their independent Revisions ──
    await page.goto(`/families/${familyId}`);
    await page.getByRole("button", { name: "Family actions" }).click();
    await page.getByRole("menuitem", { name: "Move Family to trash" }).click();
    await page
      .getByRole("dialog", { name: "Move Family to trash" })
      .getByRole("button", { name: "Move Family to trash" })
      .click();
    await expect(page).toHaveURL(/\/$/);
    for (const model of models) {
      const detail: ModelRead = await (
        await page.request.get(`${API}/api/v1/models/${model.id}`)
      ).json();
      expect(detail.files.map((file) => file.sha256).sort()).toEqual(model.hashes);
      expect(detail.files.filter((file) => file.file_type === "gcode")).toHaveLength(1);
      await page.goto(`/models/${model.id}`);
      await page.getByRole("tab", { name: /^Revisions/ }).click();
      await expect(page.getByText(`${model.name}.gcode`, { exact: true }).first()).toBeVisible();
    }
  } finally {
    if (savedViewId) await page.request.delete(`${API}/api/v1/saved-views/${savedViewId}`);
    if (familyId) {
      const response = await page.request.get(`${API}/api/v1/families/${familyId}`);
      if (response.ok()) {
        const family: FamilyRead = await response.json();
        await page.request.delete(`${API}/api/v1/families/${familyId}?version=${family.version}`);
      }
      const trash: { items: FamilyRead[] } = await (
        await page.request.get(`${API}/api/v1/families?trashed=true`)
      ).json();
      const family = trash.items.find((row) => row.id === familyId);
      if (family)
        await page.request.delete(
          `${API}/api/v1/families/${familyId}/purge?version=${family.version}`,
        );
    }
    for (const model of models) await page.request.delete(`${API}/api/v1/models/${model.id}`);
  }
});

test("sends only the chosen canonical Revision through the existing printer flow", async ({
  page,
}) => {
  const prefix = `canonical-${Date.now()}`;
  const models: { id: number; fileId: number; name: string }[] = [];
  let family: FamilyRead | undefined;
  let printerId: number | undefined;
  try {
    for (let index = 0; index < 2; index++) {
      const name = `${prefix}-${index}`;
      const gcode = Buffer.concat([
        readFileSync(
          new URL("../../../backend/tests/fixtures/real_orca_ender3_benchy.gcode", import.meta.url),
        ),
        Buffer.from(`\n; Canonical Revision: ${name}\n`),
      ]);
      const upload = await page.request.post(`${API}/api/v1/ingest/orca`, {
        multipart: {
          model_name: name,
          file: { name: `${name}.gcode`, mimeType: "text/plain", buffer: gcode },
        },
      });
      expect(upload.status()).toBe(202);
      const { job_id }: { job_id: string } = await upload.json();
      let result = { model_id: 0, file_id: 0, state: "pending" };
      await expect
        .poll(async () => {
          result = await (await page.request.get(`${API}/api/v1/ingest/jobs/${job_id}`)).json();
          return result.state;
        })
        .toBe("completed");
      models.push({ id: result.model_id, fileId: result.file_id, name });
    }
    const created = await page.request.post(`${API}/api/v1/families`, {
      data: {
        name: prefix,
        canonical_model_id: models[1].id,
        members: models.map((model) => ({ model_id: model.id })),
      },
    });
    expect(created.status()).toBe(201);
    family = await created.json();
    const printer = await page.request.post(`${API}/api/v1/printers`, {
      data: {
        name: `${prefix} printer`,
        provider: "moonraker",
        moonraker_url: `http://127.0.0.1:${process.env.PLAYWRIGHT_MOCK_PRINTER_PORT ?? 7530}`,
      },
    });
    expect(printer.status()).toBe(201);
    printerId = (await printer.json()).id;
    await expect
      .poll(
        async () =>
          (await (await page.request.get(`${API}/api/v1/printers/${printerId}`)).json()).status,
      )
      .toBe("ready");
    await page.goto(`/families/${family!.id}`);
    await page.getByRole("button", { name: "Print canonical Model", exact: true }).click();
    const send = page.getByRole("dialog", { name: "Send to printer" });
    await expect(send.getByText(`${models[1].name}.gcode`, { exact: true }).first()).toBeVisible();
    await expect(send.getByText(`${models[0].name}.gcode`, { exact: true })).toHaveCount(0);
    const sent = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/printers/${printerId}/send`) &&
        response.request().method() === "POST",
    );
    await send.getByRole("button", { name: "Send to printer", exact: true }).click();
    const response = await sent;
    expect(response.ok(), await response.text()).toBe(true);
    expect(response.request().postDataJSON()).toMatchObject({
      file_id: models[1].fileId,
      start_print: false,
    });
    await expect(send).toHaveCount(0);
  } finally {
    if (printerId) await page.request.delete(`${API}/api/v1/printers/${printerId}`);
    if (family) {
      await page.request.delete(`${API}/api/v1/families/${family.id}?version=${family.version}`);
      await page.request.delete(
        `${API}/api/v1/families/${family.id}/purge?version=${family.version + 1}`,
      );
    }
    for (const model of models) await page.request.delete(`${API}/api/v1/models/${model.id}`);
  }
});

test("creates from Model detail, explicitly moves a member, and adds only new Multipart Choices", async ({
  page,
}) => {
  const prefix = `choices-${Date.now()}`;
  const models: { id: number; name: string }[] = [];
  const families: number[] = [];
  let multipartId: number | undefined;
  try {
    // ── Real repository meshes seed independent Models ──
    for (let index = 0; index < 3; index++) {
      const name = `${prefix}-${index}`;
      const mesh = readFileSync(new URL("../../../testdata/Calibration Cube.stl", import.meta.url));
      Buffer.from(name).copy(mesh, 0, 0, 80);
      const upload = await page.request.post(`${API}/api/v1/ingest/model`, {
        multipart: {
          model_name: name,
          file: { name: `${name}.stl`, mimeType: "model/stl", buffer: mesh },
        },
      });
      expect(upload.status()).toBe(202);
      const job: { job_id: string } = await upload.json();
      let modelId = 0;
      await expect
        .poll(async () => {
          const status: { state: string; model_id: number | null } = await (
            await page.request.get(`${API}/api/v1/ingest/jobs/${job.job_id}`)
          ).json();
          modelId = status.model_id ?? 0;
          return status.state;
        })
        .toBe("completed");
      models.push({ id: modelId, name });
    }
    // ── Detail entry keeps the original selection while adding a sibling ──
    await page.goto(`/models/${models[0].id}`);
    await page.getByRole("button", { name: "Create Family", exact: true }).click();
    const create = page.getByRole("dialog", { name: "Create Family" });
    await create.getByRole("textbox", { name: "Family name" }).fill(`${prefix} source`);
    await create.getByRole("textbox", { name: "Add Models" }).fill(models[1].name);
    await create.getByRole("checkbox", { name: `Select ${models[1].name}`, exact: true }).check();
    await create.getByRole("radio", { name: models[0].name, exact: true }).check();
    await create.getByRole("button", { name: "Create Family", exact: true }).click();
    await expect(page).toHaveURL(/\/families\/\d+$/);
    families.push(Number(new URL(page.url()).pathname.split("/").at(-1)));
    const destinationResponse = await page.request.post(`${API}/api/v1/families`, {
      data: {
        name: `${prefix} destination`,
        canonical_model_id: models[2].id,
        members: [{ model_id: models[2].id }],
      },
    });
    expect(destinationResponse.status()).toBe(201);
    const destination: FamilyRead = await destinationResponse.json();
    families.push(destination.id);
    // ── Cross-Family selection requires a review and one atomic move ──
    await page.goto(`/families/${destination.id}`);
    await page.getByRole("button", { name: "Add member", exact: true }).click();
    const add = page.getByRole("dialog", { name: "Add member" });
    await add.getByRole("textbox", { name: "Add Models" }).fill(models[1].name);
    await add.getByRole("checkbox", { name: `Select ${models[1].name}`, exact: true }).check();
    await add.getByRole("button", { name: "Review move" }).click();
    const review = page.getByRole("dialog", { name: "Review move" });
    await expect(
      review.getByText(new RegExp(`from ${prefix} source to ${prefix} destination`)),
    ).toBeVisible();
    const before: FamilyRead = await (
      await page.request.get(`${API}/api/v1/families/${families[0]}`)
    ).json();
    expect(before.member_count).toBe(2);
    await review.getByRole("button", { name: "Move to this Family" }).click();
    await expect(review).toHaveCount(0);
    const after: FamilyRead = await (
      await page.request.get(`${API}/api/v1/families/${families[0]}`)
    ).json();
    expect(after.member_count).toBe(1);
    // ── Family membership creates only explicitly selected draft Choices ──
    const created = await page.request.post(`${API}/api/v1/multipart-models`, {
      data: { name: `${prefix} kit` },
    });
    expect(created.status()).toBe(201);
    const multipart: MultipartModelRead = await created.json();
    multipartId = multipart.id;
    const seeded = await page.request.put(`${API}/api/v1/multipart-models/${multipart.id}/parts`, {
      data: {
        parts: [{ name: "Body", choices: [{ model_id: models[1].id }] }],
      },
    });
    expect(seeded.ok()).toBe(true);
    await page.goto(`/multipart-models/${multipart.id}`);
    await page.getByRole("button", { name: "Edit multipart set" }).click();
    await page
      .getByRole("button", { name: `Add Family variations of ${models[1].name}`, exact: true })
      .click();
    const choices = page.getByRole("dialog", { name: "Add Family variations as Choices" });
    await expect(
      choices.getByRole("checkbox", { name: `Select ${models[1].name}`, exact: true }),
    ).toBeDisabled();
    await choices.getByRole("checkbox", { name: `Select ${models[2].name}`, exact: true }).check();
    await choices.getByRole("button", { name: "Add 1 Choices", exact: true }).click();
    await expect(choices).toHaveCount(0);
    const unsaved: MultipartModelRead = await (
      await page.request.get(`${API}/api/v1/multipart-models/${multipart.id}`)
    ).json();
    expect(unsaved.parts[0].models.map((model) => model.id)).toEqual([models[1].id]);
    await page.getByRole("button", { name: "Save changes", exact: true }).click();
    await expect(page.getByRole("button", { name: "Edit multipart set" })).toBeVisible();
    const saved: MultipartModelRead = await (
      await page.request.get(`${API}/api/v1/multipart-models/${multipart.id}`)
    ).json();
    expect(saved.parts[0].models.map((model) => model.id)).toEqual([models[1].id, models[2].id]);
  } finally {
    if (multipartId) await page.request.delete(`${API}/api/v1/multipart-models/${multipartId}`);
    for (const id of families) {
      const response = await page.request.get(`${API}/api/v1/families/${id}`);
      if (!response.ok()) continue;
      const family: FamilyRead = await response.json();
      await page.request.delete(`${API}/api/v1/families/${id}?version=${family.version}`);
      await page.request.delete(`${API}/api/v1/families/${id}/purge?version=${family.version + 1}`);
    }
    for (const model of models) await page.request.delete(`${API}/api/v1/models/${model.id}`);
  }
});
