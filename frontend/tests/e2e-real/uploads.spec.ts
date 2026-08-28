/**
 * Getting files into the library through the browser.
 *
 * Three shapes that fail differently: a mesh-only model (the mesh must land as the
 * source, not as an attachment), a bulk upload (three jobs must all reach a terminal
 * state and every thumbnail must actually load), and an upload into a chosen collection.
 * The bulk case is the one that catches a queue that reports success while a render is
 * still missing.
 */
import { test, expect } from "./helpers";
import { createCollectionViaVault, modelCard, uploadModel } from "./util";

test.describe("uploads", () => {
  test("upload an STL mesh-only model; the mesh lands as the source", async ({ page }) => {
    const name = `e2e-stl-${Date.now()}`;
    await uploadModel(page, name, { mesh: true, gcode: false });

    // The renderer is NumPy/Pillow-only, so a real authenticated thumbnail must
    // load even in the headless CI image (no GL/display dependency).
    await expect(modelCard(page, name).getByRole("img", { name })).toBeVisible();
    await modelCard(page, name).click();
    await expect(page.getByRole("heading", { name })).toBeVisible();
    await page.getByRole("tab", { name: /Files/ }).click();
    await expect(page.getByText(`${name}.stl`).first()).toBeVisible();
  });

  test("bulk upload waits for three terminal jobs and loads every WebP", async ({ page }) => {
    const collection = `e2e-bulk-${Date.now()}`;
    const names = [0, 1, 2].map((index) => `${collection}-part-${index}`);
    const thumbnailResponses: string[] = [];
    page.on("response", (response) => {
      if (
        response.url().includes("/thumbnail") &&
        response.headers()["content-type"]?.startsWith("image/webp")
      ) {
        thumbnailResponses.push(response.url());
      }
    });
    await createCollectionViaVault(page, collection);
    await page.goto("/");
    await page.getByRole("button", { name: "Upload", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Upload model" });
    await dialog.getByRole("button", { name: "Bulk", exact: true }).click();
    await dialog.locator('input[type="file"][accept=".stl,.3mf,.obj,.step,.stp"]').setInputFiles(
      names.map((name, index) => ({
        name: `${name}.stl`,
        mimeType: "model/stl",
        buffer: Buffer.from(
          `solid ${name}\nfacet normal 0 0 1\nouter loop\nvertex 0 0 ${index}\nvertex 1 0 ${index}\nvertex 0 1 ${index}\nendloop\nendfacet\nendsolid ${name}\n`,
        ),
      })),
    );
    await dialog.getByRole("button", { name: "None" }).click();
    await dialog.getByRole("option", { name: new RegExp(collection) }).click();
    await dialog.getByRole("button", { name: "Upload 3 models" }).click();

    // Bulk owns a browser-local File queue until every item has been submitted.
    // Observe the common Task Center terminal state before navigating, so this
    // test verifies the complete queue instead of destroying its JS context.
    await expect(dialog).toHaveCount(0);
    await page.getByRole("button", { name: "Notifications" }).click();
    for (const name of names) {
      const taskHeader = page.getByText(`Upload ${name}.stl`, { exact: true }).locator("..");
      await expect(taskHeader).toBeVisible();
      await expect(taskHeader.getByText("completed", { exact: true })).toBeVisible({
        timeout: 120_000,
      });
    }

    await page.goto(`/?c=${encodeURIComponent(collection)}`);

    for (const name of names) {
      const image = modelCard(page, name).getByRole("img", { name });
      await expect(image).toBeVisible({ timeout: 60_000 });
      await expect
        .poll(() => image.evaluate<number, HTMLImageElement>((node) => node.naturalWidth))
        .toBeGreaterThan(0);
    }
    expect(thumbnailResponses.length).toBeGreaterThanOrEqual(3);
  });

  test("upload into a chosen collection", async ({ page }) => {
    const col = `e2e-upcol-${Date.now()}`;
    const name = `e2e-upmodel-${Date.now()}`;

    await createCollectionViaVault(page, col);

    // uploadModel with a collection waits inside that collection's view.
    await uploadModel(page, name, { collection: col });
    await expect(modelCard(page, name)).toBeVisible();
  });
});
