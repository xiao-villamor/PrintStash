/** Resin originals, embedded previews and print facts through the real app. */
import { readFile } from "node:fs/promises";
import { test, expect } from "./helpers";
import { modelCard, uploadModel } from "./util";

for (const extension of ["ctb", "goo"]) {
  test(`uploads a ${extension.toUpperCase()} slice with embedded print facts`, async ({ page }) => {
    const name = `resin-${extension}-${Date.now()}`;
    const payload = await readFile(
      `../backend/packages/printstash-core/tests/fixtures/resin/header.${extension}`,
    );
    await uploadModel(page, name, {
      gcode: false,
      meshFile: {
        name: `${name}.${extension}`,
        mimeType: "application/octet-stream",
        buffer: payload,
      },
    });
    await modelCard(page, name).click();
    await expect(page).toHaveURL(/\/models\/\d+(?:\?.*)?$/);
    const modelId = Number(new URL(page.url()).pathname.split("/").pop());
    try {
      await expect(page.getByText("LAYERS", { exact: true })).toBeVisible();
      await expect(page.getByText("120", { exact: true })).toBeVisible();
      await expect(page.getByText("2.5 s", { exact: true })).toBeVisible();
      await expect(page.getByText("2.5 ml", { exact: true })).toBeVisible();
      const image = page
        .getByRole("region", { name: "Preview", exact: true })
        .getByRole("img", { name });
      await expect(image).toBeVisible();
      await expect
        .poll(() => image.evaluate((element: HTMLImageElement) => element.naturalWidth))
        .toBeGreaterThan(0);
      await page.reload();
      await expect(page.getByText("2.5 ml", { exact: true })).toBeVisible();
      await page.getByRole("tab", { name: /^Files/ }).click();
      await expect(page.getByText(`${name}.${extension}`, { exact: true })).toBeVisible();
      await expect(page.getByRole("button", { name: /Open in.*slicer/i })).toHaveCount(0);
      const downloadPromise = page.waitForEvent("download");
      await page.getByRole("button", { name: "Download", exact: true }).click();
      const download = await downloadPromise;
      expect(download.suggestedFilename()).toBe(`${name}.${extension}`);
      const path = await download.path();
      expect(path).not.toBeNull();
      expect(await readFile(path!)).toEqual(payload);
    } finally {
      const response = await page.request.delete(`/api/v1/models/${modelId}`);
      expect(response.ok(), await response.text()).toBeTruthy();
    }
  });
}
