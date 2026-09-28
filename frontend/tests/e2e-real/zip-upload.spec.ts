/** A ZIP is prepared as a visible Job, then reviewed and imported through Tasks. */
import { execFileSync } from "node:child_process";

import { test, expect } from "./helpers";
import { modelCard } from "./util";

function archiveFor(name: string): Buffer {
  const source = `
import io, sys, zipfile
name = sys.argv[1]
out = io.BytesIO()
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
    for folder, suffix in (("Animals", "cat"), ("Vehicles", "car")):
        model = name + "-" + suffix
        mesh = "solid " + model + "\\nfacet normal 0 0 1\\nouter loop\\nvertex 0 0 0\\nvertex 1 0 0\\nvertex 0 1 0\\nendloop\\nendfacet\\nendsolid " + model + "\\n"
        archive.writestr(folder + "/" + model + ".stl", mesh)
    archive.writestr("notes.txt", "Do not import this note")
sys.stdout.buffer.write(out.getvalue())
`;
  return execFileSync("python3", ["-c", source, name]);
}

test.describe("ZIP upload", () => {
  test("imports only the file selected after preparation", async ({ page }) => {
    const name = `e2e-zip-${Date.now()}`;
    await page.goto("/");
    await page.getByRole("button", { name: "Upload", exact: true }).click();
    const upload = page.getByRole("dialog", { name: "Upload model" });
    await upload.getByRole("button", { name: "From ZIP" }).click();
    await upload.locator('input[accept=".zip"]').setInputFiles({
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
    await upload.getByRole("button", { name: "Prepare ZIP" }).click();
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
    await review.getByRole("button", { name: "Select all 2 ZIP files" }).click();
    await expect(review.getByRole("status")).toHaveText("2 of 2 files selected");
    await review.getByRole("button", { name: "Clear selection" }).click();
    await review.getByRole("button", { name: "Select folder Animals" }).click();
    await expect(review.getByRole("status")).toHaveText("1 of 2 files selected");
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
});
