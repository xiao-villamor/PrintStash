/** An actual PrusaSlicer BGCODE can be inspected without changing its original bytes. */
import { readFile } from "node:fs/promises";
import { test, expect } from "./helpers";
import { modelCard } from "./util";

const API = `http://127.0.0.1:${process.env.PLAYWRIGHT_REAL_API_PORT ?? 8410}/api/v1`;

test.describe("BGCODE toolpath", () => {
  test("preserves the original through real layer inspection", async ({ page }, testInfo) => {
    const name = `PrusaSlicer preview ${Date.now()}`;
    const original = await readFile(
      new URL("../../../backend/tests/fixtures/bgcode/prusaslicer.bgcode", import.meta.url),
    );
    await page.goto("/");
    await page.getByRole("button", { name: "Upload", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Upload model" });
    await dialog.locator('input[accept=".gcode,.g,.gco,.bgcode"]').setInputFiles({
      name: "cube.bgcode",
      mimeType: "application/octet-stream",
      buffer: original,
    });
    await dialog.getByPlaceholder("e.g. Bracket v2").fill(name);
    const accepted = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/v1/ingest/orca") && response.request().method() === "POST",
    );
    await dialog.getByRole("button", { name: /upload to vault/i }).click();
    expect((await accepted).status()).toBe(202);
    await expect(dialog).toHaveCount(0);
    await expect(async () => {
      await page.reload();
      await expect(modelCard(page, name)).toBeVisible({ timeout: 5000 });
    }).toPass({ timeout: 60000 });
    await modelCard(page, name).click();
    await page.getByRole("button", { name: "GCode", exact: true }).click();
    const slider = page.getByRole("slider", { name: "Current layer" });
    await expect(slider).toBeVisible({ timeout: 30000 });
    // The upstream fixture has exactly 31 printed layers; travel lifts add none.
    expect(Number(await slider.getAttribute("max"))).toBe(30);
    await slider.focus();
    await slider.press("Home");
    await expect(slider).toHaveValue("0");
    await page.getByRole("button", { name: "Show travel moves" }).click();
    await expect(page.getByRole("button", { name: "Hide travel moves" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await page.screenshot({ path: testInfo.outputPath("bgcode-toolpath.png") });
    const modelId = Number(new URL(page.url()).pathname.split("/").pop());
    const model = await (await page.request.get(`${API}/models/${modelId}`)).json();
    const artifact = model.files.find(
      (file: { original_filename: string }) => file.original_filename === "cube.bgcode",
    );
    const downloaded = await page.request.get(`${API}/files/${artifact.id}/download`);
    expect(downloaded.ok()).toBeTruthy();
    expect(await downloaded.body()).toEqual(original);
  });
});
