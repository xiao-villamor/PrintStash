import { test, expect } from "./helpers";
import { modelCard, uploadModel } from "./util";

test("upload an STL mesh-only model; the mesh lands as the source", async ({ page }) => {
  const name = `e2e-stl-${Date.now()}`;
  await uploadModel(page, name, { mesh: true, gcode: false });

  // No thumbnail assertion: the test backend has no GL renderer, so meshes
  // ingest without a rendered thumbnail. Prove the mesh attached as the source.
  await modelCard(page, name).click();
  await expect(page.getByRole("heading", { name })).toBeVisible();
  await page.getByRole("tab", { name: /Files/ }).click();
  await expect(page.getByText(`${name}.stl`).first()).toBeVisible();
});

test("upload into a chosen collection", async ({ page }) => {
  const col = `e2e-upcol-${Date.now()}`;
  const name = `e2e-upmodel-${Date.now()}`;

  await page.goto("/organize");
  await page.getByPlaceholder("New collection...").fill(col);
  await page.getByPlaceholder("New collection...").press("Enter");
  await expect(page.getByRole("button", { name: `Delete ${col}` })).toBeVisible();

  // uploadModel with a collection waits inside that collection's view.
  await uploadModel(page, name, { collection: col });
  await expect(modelCard(page, name)).toBeVisible();
});
