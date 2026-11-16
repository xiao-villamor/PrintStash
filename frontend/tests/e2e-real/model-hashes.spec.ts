/** Real stored bytes identify the file; geometry appears only after the background analysis calculates it. */
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { test, expect } from "./helpers";
import { openModelPage, clickModelAction, uploadModel } from "./util";
import type { ModelRead } from "../../src/types/models";

const API = `http://127.0.0.1:${process.env.PLAYWRIGHT_REAL_API_PORT ?? 8410}`;

test.describe("Model file hashes", () => {
  test("shows the geometric SHA after real mesh analysis", async ({ page }, testInfo) => {
    await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
    const name = `hashes-${Date.now()}`;
    const mesh = readFileSync(new URL("../../../testdata/Calibration Cube.stl", import.meta.url));
    Buffer.from(name).copy(mesh, 0, 0, 80);
    const digest = createHash("sha256").update(mesh).digest("hex");
    const disabled = await page.request.patch(`${API}/api/v1/similarity/settings`, {
      data: { enabled: false },
    });
    expect(disabled.ok()).toBe(true);
    await uploadModel(page, name, {
      gcode: false,
      meshFile: { name: `${name}.stl`, mimeType: "model/stl", buffer: mesh },
    });
    await openModelPage(page, name);
    const modelUrl = page.url();
    const id = Number(new URL(modelUrl).pathname.split("/").at(-1));
    try {
      const hashes = page.getByRole("region", { name: "File hashes", exact: true });
      await expect(hashes.getByTitle(digest, { exact: true })).toHaveText(digest.slice(0, 7));
      await hashes.getByTitle(digest, { exact: true }).click();
      expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(digest);
      await expect(hashes.getByText("Geometric SHA-256", { exact: true })).toHaveCount(0);
      await expect(page.getByRole("region", { name: "AI caption", exact: true })).toHaveCount(0);
      const enabled = await page.request.patch(`${API}/api/v1/similarity/settings`, {
        data: { enabled: true, embeddings_enabled: false },
      });
      expect(enabled.ok()).toBe(true);
      await page.getByRole("tab", { name: "Similar", exact: true }).click();
      await page.getByRole("button", { name: "Find similar", exact: true }).click();
      let geometricSha: string | null = null;
      await expect(async () => {
        const response = await page.request.get(`${API}/api/v1/models/${id}`);
        expect(response.ok()).toBe(true);
        const model: ModelRead = await response.json();
        geometricSha = model.files[0].geometric_sha256;
        expect(geometricSha).toMatch(/^[a-f0-9]{64}$/);
      }).toPass({ timeout: 60_000 });
      await page.goto(modelUrl);
      await expect(hashes.getByTitle(geometricSha!, { exact: true })).toHaveText(
        geometricSha!.slice(0, 7),
      );
      await hashes.getByTitle(geometricSha!, { exact: true }).click();
      expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(geometricSha);
      await expect(hashes.getByTitle(digest, { exact: true })).toBeVisible();
      await hashes.screenshot({ path: testInfo.outputPath("model-file-hashes.png") });
      await page.setViewportSize({ width: 390, height: 844 });
      await expect(hashes.getByTitle(digest, { exact: true })).toBeVisible();
      expect(await hashes.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(
        true,
      );
      await hashes.screenshot({ path: testInfo.outputPath("model-file-hashes-mobile.png") });
    } finally {
      await page.request.patch(`${API}/api/v1/similarity/settings`, { data: { enabled: false } });
      await page.goto(modelUrl);
      await clickModelAction(page, "Delete model");
      await page.getByRole("dialog").getByRole("button", { name: "Delete", exact: true }).click();
    }
  });
});
