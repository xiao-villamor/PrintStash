/* Saved sources receive optional previews through background work without a reload. */
import { test, expect } from "../helpers";
import { modelCard, uploadModel } from "../util";

test.describe("Background preview readiness", () => {
  test("a saved model can generate its optional preview without reloading", async ({ page }) => {
    const name = `optional-preview-${Date.now()}`;
    await uploadModel(page, name, { mesh: true, gcode: false });
    await expect(modelCard(page, name).getByRole("img", { name, exact: true })).toHaveCount(0);
    await modelCard(page, name).click();
    await expect(page.getByRole("heading", { name })).toBeVisible();
    await page.getByRole("tab", { name: /^Files/ }).click();
    const generate = page.getByRole("button", { name: "Generate preview", exact: true });
    await expect(generate).toBeVisible();
    await generate.click();
    await expect(page.getByText("Preparing previews and details")).toHaveCount(0, {
      timeout: 60_000,
    });
    await expect(generate).toHaveCount(0);
    await expect(page.getByText("Some details could not be prepared")).toHaveCount(0);
    await page.goto("/");
    await expect(modelCard(page, name).getByRole("img", { name, exact: true })).toBeVisible();
  });
});
