/** A ZIP is prepared as a visible Job, then reviewed and imported through Tasks. */
import { execFileSync } from "node:child_process";
import { mkdir, writeFile, readFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { unzipSync } from "fflate";

import { test, expect } from "./helpers";
import { modelCard } from "./util";

function archiveFor(name: string): Buffer {
  const source = `
import io, sys, zipfile
from PIL import Image
name = sys.argv[1]
out = io.BytesIO()
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
    for folder, suffix in (("Animals", "cat"), ("Vehicles", "car")):
        model = name + "-" + suffix
        mesh = "solid " + model + "\\nfacet normal 0 0 1\\nouter loop\\nvertex 0 0 0\\nvertex 1 0 0\\nvertex 0 1 0\\nendloop\\nendfacet\\nendsolid " + model + "\\n"
        archive.writestr(folder + "/" + model + ".stl", mesh)
    archive.writestr("notes.txt", "Do not import this note")
    archive.writestr("manual.pdf", "%PDF-1.4 assembly instructions")
    photo = io.BytesIO()
    Image.new("RGB", (16, 16), "blue").save(photo, format="PNG")
    archive.writestr("photo.png", photo.getvalue())
    archive.writestr("unrecognized.bin", "omitted")
sys.stdout.buffer.write(out.getvalue())
`;
  return execFileSync(resolve("../backend/.venv/bin/python"), ["-c", source, name]);
}

test.describe("ZIP upload", () => {
  test("imports only the file selected after preparation", async ({ page }) => {
    const name = `e2e-zip-${Date.now()}`;
    await page.goto("/");
    await page.getByRole("button", { name: "Upload", exact: true }).click();
    const upload = page.getByRole("dialog", { name: "Upload model" });
    await upload.getByRole("tab", { name: "From archive" }).click();
    await upload.locator('input[accept*=".zip"]').setInputFiles({
      name: `${name}.zip`,
      mimeType: "application/zip",
      buffer: archiveFor(name),
    });
    let releaseTransfer: (() => void) | undefined;
    const transferGate = new Promise<void>((resolve) => {
      releaseTransfer = resolve;
    });
    await page.route("**/api/v1/ingest/archive/inspect", async (route) => {
      await transferGate;
      await route.continue();
    });
    await upload.getByRole("button", { name: "Prepare archive" }).click();
    await expect(upload).toHaveCount(0);
    await page.getByRole("button", { name: "Notifications" }).click();
    const tasksMenu = page.getByRole("dialog");
    await expect(tasksMenu.getByText(`Upload ${name}.zip`, { exact: true })).toBeVisible();
    await expect(tasksMenu.getByText("Transferring file")).toBeVisible();
    await expect(tasksMenu.getByRole("button", { name: "Cancel upload" })).toBeVisible();
    if (!releaseTransfer) throw new Error("ZIP transfer did not start");
    releaseTransfer();
    await expect(page.getByText("ZIP ready. Choose which files to add.")).toBeVisible({
      timeout: 120_000,
    });

    const prepared = page.getByText(`Prepare ${name}.zip`, { exact: true }).locator("..");
    await expect(prepared.getByText("Ready", { exact: true })).toBeVisible({ timeout: 120_000 });
    await page.getByRole("dialog").getByRole("button", { name: "Choose ZIP files" }).click();

    const review = page.getByRole("dialog", { name: "Choose ZIP files" });
    await expect(review.getByText("Documents and omitted files")).toBeVisible();
    for (const auxiliary of ["notes.txt", "manual.pdf", "photo.png", "unrecognized.bin"]) {
      await expect(review.getByText(auxiliary, { exact: true })).toBeVisible();
    }
    await expect(
      review.getByText(/Each selected model file creates or reuses a Model/),
    ).toBeVisible();
    await review.getByRole("button", { name: "Select all 2 ZIP files" }).click();
    await expect(review.getByRole("status")).toHaveText("2 of 2 files selected");
    await review.getByRole("button", { name: "Clear selection" }).click();
    await review.getByRole("button", { name: "Select folder Animals" }).click();
    await expect(review.getByRole("status")).toHaveText("1 of 2 files selected");
    await page.screenshot({ path: test.info().outputPath("archive-review.png") });
    await review.getByRole("button", { name: "Import 1 selected" }).click();
    await expect(review).toHaveCount(0);
    await page.goto(`/?c=${encodeURIComponent(`${name}/animals`)}`);
    await expect(modelCard(page, `${name}-cat`)).toBeVisible({ timeout: 60_000 });
    await expect(modelCard(page, `${name}-car`)).toHaveCount(0);

    // The suite shares its disposable database with other specs.
    const models: Array<{ id: number; name: string }> = await (
      await page.request.get("/api/v1/models?limit=100")
    ).json();
    const imported = models.find((item) => item.name === `${name}-cat`);
    if (imported) await page.request.delete(`/api/v1/models/${imported.id}`);
  });
  for (const input of ["archive", "folder"] as const) {
    test(`creates a reviewed Multipart from a ${input}`, async ({ page }) => {
      const name = `e2e-assembly-${Date.now()}`;
      await page.goto("/");
      await page.getByRole("button", { name: "Upload", exact: true }).click();
      const upload = page.getByRole("dialog", { name: "Upload model" });
      const originals = unzipSync(archiveFor(name));
      const folder = test.info().outputPath(name);
      if (input === "folder") {
        for (const [path, bytes] of Object.entries(originals)) {
          await mkdir(dirname(join(folder, path)), { recursive: true });
          await writeFile(join(folder, path), bytes);
        }
        await upload.getByRole("tab", { name: "Bulk", exact: true }).click();
        await upload.locator("input[webkitdirectory]").setInputFiles(folder);
        await expect(
          upload.getByRole("button", { name: "Remove manual.pdf", exact: true }),
        ).toBeVisible();
        await upload.getByRole("button", { name: "Prepare folder review" }).click();
      } else {
        await upload.getByRole("tab", { name: "From archive" }).click();
        await upload.locator('input[accept*=".zip"]').setInputFiles({
          name: `${name}.zip`,
          mimeType: "application/zip",
          buffer: archiveFor(name),
        });
        await upload.getByRole("button", { name: "Prepare archive" }).click();
      }
      await expect(upload).toHaveCount(0);
      await page.getByRole("button", { name: "Notifications" }).click();
      await page
        .getByRole("button", { name: "Choose ZIP files" })
        .last()
        .click({ timeout: 120_000 });
      const review = page.getByRole("dialog", { name: "Choose ZIP files" });
      await review.getByRole("button", { name: "Select all 2 ZIP files" }).click();
      await review.getByRole("radio", { name: "Multipart Model", exact: true }).check();
      await review.getByLabel("Multipart name", { exact: true }).fill(name);
      await review.getByLabel(`Piece name: Animals/${name}-cat.stl`, { exact: true }).fill("Base");
      await review.getByLabel(`Piece name: Vehicles/${name}-car.stl`, { exact: true }).fill("Lid");
      await review.getByRole("checkbox", { name: "manual.pdf", exact: true }).check();
      await review
        .getByRole("combobox", { name: "Multipart cover", exact: true })
        .selectOption("photo.png");
      await expect(page.getByRole("button", { name: "Choose files", exact: true })).toHaveCount(0);
      await page.screenshot({ path: test.info().outputPath("multipart-review.png") });
      const accepted = page.waitForResponse(
        (response) => response.url().endsWith("/select") && response.status() === 202,
      );
      await review.getByRole("button", { name: "Import 2 selected" }).click();
      const { job_id: jobId } = await (await accepted).json();
      let multipartId: number | undefined;
      await expect(async () => {
        const job = await (await page.request.get(`/api/v1/jobs/${jobId}`)).json();
        expect(job.state).toBe("completed");
        expect(job.failed).toBe(0);
        expect(job.result.documents).toHaveLength(1);
        expect(job.result.multipart_model_id).toBeGreaterThan(0);
        multipartId = job.result.multipart_model_id;
      }).toPass({ timeout: 120_000 });
      const saved = await (
        await page.request.get(`/api/v1/multipart-models/${multipartId}`)
      ).json();
      expect(saved.cover_image_uploaded).toBe(true);
      expect(saved.parts.map((part: { name: string }) => part.name).sort()).toEqual([
        "Base",
        "Lid",
      ]);
      await page.goto(`/multipart-models/${multipartId}`);
      await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
      await page.reload();
      await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
      if (input === "folder") {
        for (const [path, bytes] of Object.entries(originals))
          expect(await readFile(join(folder, path))).toEqual(Buffer.from(bytes));
      }
    });
  }
  test("reclaims the retained input of a failed ZIP preparation", async ({ page }) => {
    const name = `e2e-failed-zip-${Date.now()}`;
    await page.goto("/");
    await page.getByRole("button", { name: "Upload", exact: true }).click();
    const upload = page.getByRole("dialog", { name: "Upload model" });
    await upload.getByRole("tab", { name: "From archive" }).click();
    await upload.locator('input[accept*=".zip"]').setInputFiles({
      name: `${name}.zip`,
      mimeType: "application/zip",
      buffer: execFileSync("python3", [
        "-c",
        "import io,sys,zipfile; b=io.BytesIO(); z=zipfile.ZipFile(b,'w'); z.writestr('../unsafe.stl','invalid'); z.close(); sys.stdout.buffer.write(b.getvalue())",
      ]),
    });
    const accepted = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/v1/ingest/archive/inspect") && response.status() === 202,
    );
    await upload.getByRole("button", { name: "Prepare archive" }).click();
    const { job_id: jobId } = await (await accepted).json();
    await expect(upload).toHaveCount(0);
    await expect(async () => {
      const response = await page.request.get(`/api/v1/jobs/${jobId}`);
      const job = await response.json();
      expect(job.state).toBe("failed");
      expect(job.staging.discard_available).toBe(true);
    }).toPass({ timeout: 30_000 });
    await page.getByRole("button", { name: "Notifications" }).click();
    const discard = page.getByRole("button", { name: "Discard staged input", exact: true });
    await expect(discard).toBeVisible();
    await discard.click();
    const confirmation = page.getByRole("dialog", { name: "Discard staged input?" });
    await confirmation.getByRole("button", { name: "Discard staged input", exact: true }).click();
    await expect(confirmation).toHaveCount(0);
    await expect(async () => {
      const response = await page.request.get(`/api/v1/jobs/${jobId}`);
      expect((await response.json()).staging).toBeNull();
    }).toPass({ timeout: 15_000 });
  });
});
