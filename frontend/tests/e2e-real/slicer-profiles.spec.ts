/** Profile details group repeated G-code settings while retaining distinct variants. */
import { readFileSync } from "node:fs";
import { test, expect } from "./helpers";
import { uploadModel } from "./util";
import type { FilamentProfileRead, PrinterProfileRead } from "../../src/types";
import type { SlicerProfilePage } from "../../src/types/slicer-profiles";

for (const width of [1440, 390]) {
  test(`inspects grouped profile variants at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    const name = `e2e-profile-variants-${width}-${Date.now()}`;
    const source = readFileSync("../backend/tests/fixtures/real_orca_ender3_benchy.gcode", "utf8");
    const modelIds: number[] = [];
    try {
      for (const variant of ["first", "repeat", "changed"]) {
        const filename = `${name}-${variant}.gcode`;
        const content =
          variant === "changed"
            ? source.replace("; filament_flow_ratio = 0.98", "; filament_flow_ratio = 1.04")
            : source;
        await uploadModel(page, `${name}-${variant}`, {
          gcodeFile: {
            name: filename,
            mimeType: "text/plain",
            buffer: Buffer.from(`${content}\n; fixture marker: ${filename}\n`),
          },
        });
        await expect
          .poll(
            async () => {
              const result: SlicerProfilePage = await (
                await page.request.get("/api/v1/slicer-profiles")
              ).json();
              const capture = result.items.find((item) => item.filename === filename);
              if (capture && !modelIds.includes(capture.model_id)) modelIds.push(capture.model_id);
              return Boolean(capture);
            },
            { timeout: 60_000 },
          )
          .toBe(true);
      }
      const filaments: FilamentProfileRead[] = await (
        await page.request.get("/api/v1/filament-profiles")
      ).json();
      const filament = filaments.find((item) => item.material_type === "PLA");
      expect(filament).toBeTruthy();
      const printers: PrinterProfileRead[] = await (
        await page.request.get("/api/v1/printer-profiles")
      ).json();
      const printer = printers.find((item) => item.printer_model === "Creality Ender-3 V3 SE");
      expect(printer).toBeTruthy();
      await page.setViewportSize({ width, height: 1000 });
      await page.goto("/profiles");
      await expect(page.getByRole("tab", { name: "Detected settings", exact: true })).toHaveCount(
        0,
      );
      await expect(
        page.getByRole("button", { name: `View details for ${filament!.name}`, exact: true }),
      ).toHaveAttribute("aria-expanded", "false");
      await page.screenshot({
        path: testInfo.outputPath("profiles-collapsed.png"),
        fullPage: true,
      });
      const filamentDetails = page.getByRole("region", {
        name: `Settings for ${filament!.name}`,
        exact: true,
      });
      await page
        .getByRole("button", { name: `View details for ${filament!.name}`, exact: true })
        .click();
      await expect(filamentDetails.getByRole("article")).toHaveCount(2);
      const profileRow = page.getByRole("region", {
        name: `Preset: ${filament!.name}`,
        exact: true,
      });
      await expect(profileRow.getByRole("textbox")).toHaveCount(0);
      await expect(
        profileRow.getByText("Cost/kg inferred from slicer total filament cost.", { exact: true }),
      ).toBeVisible();
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      ).toBe(true);
      if (width >= 640) {
        const label = await profileRow.getByText("Notes", { exact: true }).boundingBox();
        const note = await profileRow
          .getByText("Cost/kg inferred from slicer total filament cost.", { exact: true })
          .boundingBox();
        expect(label).not.toBeNull();
        expect(note).not.toBeNull();
        expect(
          Math.abs(label!.y + label!.height / 2 - note!.y - note!.height / 2),
        ).toBeLessThanOrEqual(1);
      }
      await page.screenshot({ path: testInfo.outputPath("profile-overview.png"), fullPage: true });
      await profileRow
        .getByRole("button", { name: `Edit preset ${filament!.name}`, exact: true })
        .click();
      await expect(
        profileRow.getByRole("textbox", { name: `Filament notes ${filament!.id}`, exact: true }),
      ).toBeVisible();
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      ).toBe(true);
      await page.screenshot({ path: testInfo.outputPath("profile-editor.png"), fullPage: true });
      await profileRow
        .getByRole("button", { name: `Close editor for ${filament!.name}`, exact: true })
        .click();
      await expect(profileRow.getByRole("textbox")).toHaveCount(0);
      const repeated = filamentDetails.getByRole("article").filter({ hasText: "Source files: 2" });
      await repeated.getByRole("button", { name: "View settings" }).click();
      await expect(repeated.getByText("0.98", { exact: true })).toBeVisible();
      await expect(repeated.getByRole("link")).toHaveCount(2);
      const changed = filamentDetails.getByRole("article").filter({ hasText: "Source files: 1" });
      await changed.getByRole("button", { name: "View settings" }).click();
      await expect(changed.getByText("1.04", { exact: true })).toBeVisible();
      await page.screenshot({ path: testInfo.outputPath("filament-variants.png"), fullPage: true });

      await page
        .getByRole("button", { name: `Hide details for ${filament!.name}`, exact: true })
        .click();
      await expect(filamentDetails).toHaveCount(0);
      await page.getByRole("tab", { name: /Printers/ }).click();
      const printerDetails = page.getByRole("region", {
        name: `Settings for ${printer!.name}`,
        exact: true,
      });
      await page
        .getByRole("button", { name: `View details for ${printer!.name}`, exact: true })
        .click();
      await expect(printerDetails.getByRole("article")).toHaveCount(1);
      await printerDetails.getByRole("button", { name: "View settings" }).click();
      await expect(printerDetails.getByRole("link")).toHaveCount(3);
      await expect(printerDetails.getByText("nozzle_diameter", { exact: true })).toBeVisible();
      await printerDetails
        .getByText("Print settings for this file", { exact: true })
        .first()
        .click();
      const search = printerDetails
        .getByRole("textbox", { name: "Find a setting or value" })
        .nth(1);
      await search.fill("layer_height");
      await expect(
        printerDetails.getByRole("listitem").first().getByText("layer_height", { exact: true }),
      ).toBeVisible();
      await page.screenshot({ path: testInfo.outputPath("printer-variants.png"), fullPage: true });
      await printerDetails.getByRole("link").first().click();
      await expect(page).toHaveURL(/\/models\/\d+$/);
    } finally {
      for (const id of modelIds) await page.request.delete(`/api/v1/models/${id}`);
    }
  });
}
