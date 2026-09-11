/**
 * Getting files into the library through the browser.
 *
 * Five shapes that fail differently: an asymmetric model whose viewer capture must be
 * real, a mesh-only model (the mesh must land as the source), a BGCODE model (the binary
 * container must reach the existing metadata parser), a bulk upload (all jobs and
 * thumbnails must finish), and an upload into a chosen collection. The bulk case catches
 * a queue that reports success while a render is still missing.
 */
import { test, expect } from "./helpers";
import { bgcodeFor, createCollectionViaVault, modelCard, uploadModel } from "./util";

test.describe("uploads", () => {
  test("@critical resumes a multipart upload after a browser reload", async ({ page }) => {
    const name = `e2e-resume-${Date.now()}`;
    const file = {
      name: `${name}.stl`,
      mimeType: "model/stl",
      // Keep the mesh valid while forcing two canonical 8 MiB API chunks.
      buffer: Buffer.concat([
        Buffer.from(
          `solid ${name}\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid ${name}\n`,
        ),
        Buffer.alloc(8 * 1024 * 1024, 0x20),
      ]),
    };
    let releaseSecondChunk!: () => void;
    const holdSecondChunk = new Promise<void>((resolve) => {
      releaseSecondChunk = resolve;
    });
    let observeSecondChunk!: () => void;
    const secondChunkSeen = new Promise<void>((resolve) => {
      observeSecondChunk = resolve;
    });
    const secondChunk = /\/api\/v1\/artifact-uploads\/[^/]+\/chunks\/1\?/;
    await page.route(secondChunk, async (route) => {
      observeSecondChunk();
      await holdSecondChunk;
      await route.abort("aborted");
    });

    await page.goto("/");
    await page.getByRole("button", { name: "Upload", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Upload model" });
    await dialog.locator('input[accept=".stl,.3mf,.obj,.step,.stp"]').setInputFiles(file);
    await page.getByPlaceholder("e.g. Bracket v2").fill(name);
    await page.getByRole("button", { name: /upload to vault/i }).click();
    await expect(dialog).toHaveCount(0);
    await secondChunkSeen;

    await page.getByRole("button", { name: "Notifications" }).click();
    await page.getByRole("button", { name: "Pause upload" }).click();
    releaseSecondChunk();
    await expect(page.getByRole("button", { name: "Resume upload" })).toBeVisible();

    await page.unroute(secondChunk);
    await page.reload();
    await page.getByRole("button", { name: "Notifications" }).click();
    await page
      .getByText("Resume upload", { exact: true })
      .locator('input[type="file"]')
      .setInputFiles(file);

    const taskHeader = page.getByText(`Upload ${name}`, { exact: true }).locator("..");
    await expect(taskHeader.getByText("completed", { exact: true })).toBeVisible({
      timeout: 120_000,
    });
    await page.goto("/");
    await expect(modelCard(page, name)).toBeVisible({ timeout: 60_000 });
    await modelCard(page, name).click();
    await expect(page.getByRole("heading", { name })).toBeVisible();
  });

  test("renders an asymmetric preview with a downloadable screenshot", async ({ page }) => {
    const name = `e2e-preview-${Date.now()}`;
    const facets = [
      [
        [0, 0, 0],
        [60, 0, 0],
        [0, 25, 0],
      ],
      [
        [0, 0, 0],
        [0, 25, 0],
        [8, 6, 75],
      ],
      [
        [0, 25, 0],
        [60, 0, 0],
        [8, 6, 75],
      ],
      [
        [60, 0, 0],
        [0, 0, 0],
        [8, 6, 75],
      ],
    ];
    const stl = [
      `solid ${name}`,
      ...facets.flatMap((vertices) => [
        "facet normal 0 0 1",
        " outer loop",
        ...vertices.map((vertex) => `  vertex ${vertex.join(" ")}`),
        " endloop",
        "endfacet",
      ]),
      `endsolid ${name}`,
    ].join("\n");

    await uploadModel(page, name, {
      mesh: true,
      gcode: false,
      meshFile: {
        name: `${name}.stl`,
        mimeType: "model/stl",
        buffer: Buffer.from(stl),
      },
    });

    const preview = modelCard(page, name).getByRole("img", { name });
    await expect
      .poll(() => preview.evaluate<number, HTMLImageElement>((node) => node.naturalWidth))
      .toBeGreaterThan(0);
    await modelCard(page, name).click();
    const screenshot = page.getByRole("button", { name: "Screenshot" });
    await expect(screenshot).toBeEnabled({ timeout: 60_000 });

    const [download] = await Promise.all([page.waitForEvent("download"), screenshot.click()]);
    expect(download.suggestedFilename()).toBe(`${name}.png`);
    const stream = await download.createReadStream();
    let bytes = 0;
    for await (const chunk of stream) bytes += chunk.length;
    expect(bytes).toBeGreaterThan(100);
  });

  test("@critical upload an STL mesh-only model; the mesh lands as the source", async ({
    page,
  }) => {
    const name = `e2e-stl-${Date.now()}`;
    await uploadModel(page, name, { mesh: true, gcode: false });

    // The renderer is NumPy/Pillow-only, so a real authenticated thumbnail must
    // load even in the headless CI image (no GL/display dependency).
    await expect(async () => {
      await page.goto("/");
      const thumbnail = modelCard(page, name).getByRole("img", { name });
      await expect(thumbnail).toBeVisible({ timeout: 2_000 });
      expect(
        await thumbnail.evaluate((image: HTMLImageElement) => image.naturalWidth),
      ).toBeGreaterThan(0);
    }).toPass({ timeout: 60_000 });
    await modelCard(page, name).click();
    await expect(page.getByRole("heading", { name })).toBeVisible();
    await page.getByRole("tab", { name: /Files/ }).click();
    await expect(page.getByText(`${name}.stl`).first()).toBeVisible();
  });

  test("@critical upload a BGCODE model; its slicer metadata is available", async ({ page }) => {
    const name = `e2e-bgcode-${Date.now()}`;
    await uploadModel(page, name, {
      gcodeFile: {
        name: `${name}.bgcode`,
        mimeType: "application/octet-stream",
        buffer: bgcodeFor(name),
      },
    });

    await modelCard(page, name).click();
    await expect(page.getByRole("heading", { name })).toBeVisible();
    await expect(page.getByText("PETG", { exact: true }).first()).toBeVisible();
    await expect(page.getByText(/PrusaSlicer/).first()).toBeVisible();
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

  test("@critical upload into a chosen collection", async ({ page }) => {
    const col = `e2e-upcol-${Date.now()}`;
    const name = `e2e-upmodel-${Date.now()}`;

    await createCollectionViaVault(page, col);

    // uploadModel with a collection waits inside that collection's view.
    await uploadModel(page, name, { collection: col });
    await expect(modelCard(page, name)).toBeVisible();
  });
});
