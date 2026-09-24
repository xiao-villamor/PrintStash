/**
 * External roots are deliberately opt-in for real-browser coverage.
 *
 * Set PLAYWRIGHT_EXTERNAL_LIBRARY_ROOT to an existing directory shared by the
 * browser test process and the backend process. Each test creates a separate
 * child root under that test-owned directory, leaving the supplied parent
 * untouched. The contracts verify scanned-file preview/download and explicit
 * enrollment before external write-back. Without the environment path the suite
 * reports them as skipped rather than treating an arbitrary directory as safe.
 */
import { access, mkdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";

import { test, expect } from "./helpers";

const externalRoot = process.env.PLAYWRIGHT_EXTERNAL_LIBRARY_ROOT;
const markerName = ".printstash-external-root.json";

test.describe("mounted library source root recovery", () => {
  test("serves a scanned mounted STL in the browser without a storage connection", async ({
    page,
  }) => {
    if (!externalRoot) {
      test.skip(
        true,
        "Set PLAYWRIGHT_EXTERNAL_LIBRARY_ROOT to an existing test-owned directory to run this contract.",
      );
      return;
    }
    const name = `e2e-mounted-preview-${Date.now()}`;
    const root = path.join(externalRoot, name);
    const source = path.join(root, `${name}.stl`);
    const original = Buffer.from(
      [
        `solid ${name}`,
        "facet normal 0 0 1",
        " outer loop",
        "  vertex 0 0 0",
        "  vertex 40 0 0",
        "  vertex 0 25 0",
        " endloop",
        "endfacet",
        `endsolid ${name}`,
      ].join("\n"),
    );
    let libraryId: number | null = null;

    await mkdir(root);
    await writeFile(source, original);
    try {
      expect(
        (
          await page.request.put("/api/v1/config", { data: { external_libraries_enabled: true } })
        ).ok(),
      ).toBe(true);
      const create = await page.request.post("/api/v1/libraries", {
        data: { name, root_path: root, scan_schedule: "", watch_mode: "off" },
      });
      expect(create.status()).toBe(201);
      const created = await create.json();
      libraryId = Number(created.id);
      expect(created.connection_id).toBeNull();

      const scan = await page.request.post(`/api/v1/libraries/${libraryId}/scan`);
      expect(scan.status()).toBe(202);
      let modelId = 0;
      await expect
        .poll(
          async () => {
            const response = await page.request.get(`/api/v1/models?q=${name}`);
            if (!response.ok()) return false;
            const models = await response.json();
            modelId = Number(
              models.find((model: { name: string }) => model.name === name)?.id ?? 0,
            );
            return modelId > 0;
          },
          { timeout: 60_000 },
        )
        .toBe(true);
      const detail = await (await page.request.get(`/api/v1/models/${modelId}`)).json();
      const file = detail.files.find(
        (item: { original_filename: string }) => item.original_filename === `${name}.stl`,
      );
      expect(file).toBeDefined();
      expect(file.is_external).toBe(true);

      const preview = page.waitForResponse((response) =>
        response.url().endsWith(`/api/v1/files/${file.id}/stl`),
      );
      await page.goto(`/models/${modelId}`);
      await expect(page.getByRole("heading", { name })).toBeVisible();
      expect((await preview).status()).toBe(200);
      await expect(page.getByRole("button", { name: "Screenshot" })).toBeEnabled();

      await page.getByRole("tab", { name: /Files/ }).click();
      const [download] = await Promise.all([
        page.waitForEvent("download"),
        page.getByTitle("Download").first().click(),
      ]);
      expect(download.suggestedFilename()).toBe(`${name}.stl`);
      const chunks: Buffer[] = [];
      for await (const chunk of (await download.createReadStream())!)
        chunks.push(Buffer.from(chunk));
      expect(Buffer.concat(chunks)).toEqual(original);
    } finally {
      try {
        if (libraryId !== null) await page.request.delete(`/api/v1/libraries/${libraryId}`);
      } finally {
        await rm(root, { recursive: true, force: true });
        await page.request.put("/api/v1/config", { data: { external_libraries_enabled: false } });
      }
    }
  });

  test("reenrolls a root with a missing marker before external write-back", async ({ page }) => {
    if (!externalRoot) {
      test.skip(
        true,
        "Set PLAYWRIGHT_EXTERNAL_LIBRARY_ROOT to an existing test-owned directory to run this contract.",
      );
      return;
    }
    const name = `e2e-external-${Date.now()}`;
    const root = path.join(externalRoot, name);
    const marker = path.join(root, markerName);
    let libraryId: number | null = null;

    await mkdir(root);
    try {
      const enable = await page.request.put("/api/v1/config", {
        data: { external_libraries_enabled: true },
      });
      expect(enable.ok()).toBe(true);

      const create = await page.request.post("/api/v1/libraries", {
        data: { name, root_path: root, scan_schedule: "", watch_mode: "off" },
      });
      expect(create.status()).toBe(201);
      const created = await create.json();
      libraryId = Number(created.id);

      await rm(marker, { force: true });
      const missing = await page.request.get("/api/v1/libraries");
      expect(missing.ok()).toBe(true);
      const listed = await missing.json();
      expect(listed.find((library: { id: number }) => library.id === libraryId)).toMatchObject({
        binding_state: "missing",
        root_enrollable: true,
        watch_active: false,
      });

      await page.goto("/settings?section=libraries");
      await expect(page.getByText("Root proof unavailable")).toBeVisible();
      await page.getByRole("button", { name: "Review and enroll" }).click();
      const confirmation = page.getByRole("dialog", { name: "Enroll mounted source root?" });
      await expect(confirmation).toBeVisible();
      await expect(confirmation).toContainText(root);
      await confirmation.getByRole("button", { name: "Enroll root" }).click();
      await expect(page.getByText("Source verified")).toBeVisible();

      await page.goto("/");
      await page.getByRole("button", { name: "Upload", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Upload model" });
      await dialog.locator('input[accept=".gcode,.g,.gco,.bgcode"]').setInputFiles({
        name: `${name}.gcode`,
        mimeType: "text/plain",
        buffer: Buffer.from(`; external root test ${name}\nG28\n`),
      });
      await dialog.getByPlaceholder("e.g. Bracket v2").fill(name);
      const destination = dialog.getByRole("combobox").filter({ hasText: "Vault storage" });
      await destination.selectOption(String(libraryId));
      await dialog.getByRole("button", { name: /upload to vault/i }).click();
      await expect(dialog).toHaveCount(0);

      const written = path.join(root, `${name}.gcode`);
      await expect
        .poll(async () => {
          try {
            await access(written);
            return true;
          } catch {
            return false;
          }
        })
        .toBe(true);
    } finally {
      try {
        if (libraryId !== null) await page.request.delete(`/api/v1/libraries/${libraryId}`);
      } finally {
        await rm(root, { recursive: true, force: true });
        await page.request.put("/api/v1/config", {
          data: { external_libraries_enabled: false },
        });
      }
    }
  });
});
