/** Printer inventory and history through the real backend and a loopback Moonraker emulator. */
import type { PrinterRead, PrintJobRead } from "@/types";
import { expect, test } from "./helpers";
import { collapseNavigationSidebar, gcodeFor } from "./util";

const emulator = `http://127.0.0.1:${process.env.PLAYWRIGHT_MOCK_PRINTER_PORT ?? "7530"}`;

test.describe("printer Files and Jobs", () => {
  test("follows a file from its printer folder into print history", async ({
    page,
    request,
  }, testInfo) => {
    const suffix = Date.now();
    const modelName = `Thermal coupon ${suffix}`;
    const deep = `Calibration/Bed/${suffix}`;
    const external = `${deep}/bed-square.gcode`;
    const disposable = `${deep}/spare-square.gcode`;
    const modelUpload = await page.request.post("/api/v1/ingest/orca", {
      multipart: {
        file: {
          name: `thermal-coupon-${suffix}.gcode`,
          mimeType: "text/plain",
          buffer: Buffer.from(gcodeFor(modelName)),
        },
        model_name: modelName,
      },
    });
    expect(modelUpload.status(), await modelUpload.text()).toBe(202);
    const { job_id: ingestJob } = await modelUpload.json();
    const ingestPath = `/api/v1/jobs/${ingestJob}`;
    await expect
      .poll(async () => (await (await page.request.get(ingestPath)).json()).state)
      .toBe("completed");
    const { model_id: modelId, file_id: fileId } = await (
      await page.request.get(ingestPath)
    ).json();
    const registered = await page.request.post("/api/v1/printers", {
      data: { name: `Review Klipper ${suffix}`, provider: "moonraker", moonraker_url: emulator },
    });
    expect(registered.status()).toBe(201);
    const printer: PrinterRead = await registered.json();
    const api = `/api/v1/printers/${printer.id}`;
    const route = `/printers/${printer.id}`;
    async function history(): Promise<PrintJobRead[]> {
      const response = await page.request.get(`${api}/jobs`);
      expect(response.ok()).toBe(true);
      return response.json();
    }
    async function waitForJob(id: number, state: PrintJobRead["state"]) {
      await expect
        .poll(async () => (await history()).find((job) => job.id === id)?.state, {
          timeout: 30_000,
        })
        .toBe(state);
    }
    try {
      // ── Seed a matched Revision and real remote paths ──
      const upload = await page.request.post(`${api}/send`, {
        data: { file_id: fileId, start_print: false },
      });
      expect(upload.status()).toBe(200);
      const uploadOnly: PrintJobRead = await upload.json();
      for (const path of [external, disposable]) {
        const sent = await request.post(`${emulator}/server/files/upload`, {
          multipart: {
            file: { name: path, mimeType: "text/plain", buffer: Buffer.from(gcodeFor(path)) },
          },
        });
        expect(sent.ok()).toBe(true);
        expect((await sent.json()).result.item.path).toBe(path);
      }

      // ── Browse deeper folders, search, then remove an actual remote file ──
      await page.goto(`${route}?tab=files`);
      await collapseNavigationSidebar(page);
      const files = page.getByRole("table", { name: "Printer files" });
      const folders = page.getByRole("navigation", { name: "Folders" });
      const path = page.getByRole("navigation", { name: "Printer file path" });
      const fileInspector = page.getByRole("complementary", { name: "Selected file" });
      async function startSelectedFile(): Promise<PrintJobRead> {
        const started = page.waitForResponse(
          (response) =>
            response.url().endsWith(`${api}/start`) && response.request().method() === "POST",
        );
        await fileInspector.getByRole("button", { name: "Start", exact: true }).click();
        const receipt = await started;
        expect(receipt.ok()).toBe(true);
        return receipt.json();
      }
      await expect(files.getByRole("columnheader")).toHaveText([
        "Name",
        "Size",
        "Library",
        "Last seen",
      ]);
      await expect(fileInspector.getByRole("link", { name: "Open Model" })).toHaveAttribute(
        "href",
        `/models/${modelId}`,
      );
      const [label, value] = await fileInspector
        .locator("dl > div")
        .first()
        .locator("dt, dd")
        .all();
      const [labelBox, valueBox] = await Promise.all([label.boundingBox(), value.boundingBox()]);
      if (!labelBox || !valueBox) throw new Error("File facts must be visible");
      expect(valueBox.x - labelBox.x - labelBox.width).toBeLessThanOrEqual(16);
      const syncReceipt = page.waitForResponse(
        (response) =>
          response.url().endsWith(`${api}/files/sync`) && response.request().method() === "POST",
      );
      await page.getByRole("button", { name: "Sync printer files" }).click();
      expect((await syncReceipt).ok()).toBe(true);
      await expect(folders.getByRole("button", { name: "Calibration" })).toBeVisible();
      await files
        .getByRole("row", { name: new RegExp(modelName) })
        .getByRole("cell")
        .last()
        .click();
      await page.screenshot({ path: testInfo.outputPath("files-root.png") });

      // ── Follow the matched Model from Files into the running dashboard ──
      const completed = await startSelectedFile();
      expect(completed.file_id).toBe(fileId);
      expect((await request.post(`${emulator}/printer/print/pause`)).ok()).toBe(true);
      await expect(page).toHaveURL(new RegExp(`${route}$`));
      const dashboard = page.getByRole("region", { name: "Current job" });
      await expect(dashboard.getByText(modelName, { exact: true })).toBeVisible();
      await expect(dashboard.getByText(completed.remote_filename, { exact: true })).toBeVisible();
      await page.screenshot({ path: testInfo.outputPath("dashboard-managed.png") });
      expect((await request.post(`${emulator}/printer/print/resume`)).ok()).toBe(true);
      await waitForJob(completed.id, "completed");
      await page.getByRole("tab", { name: "Files", exact: true }).click();

      await folders.getByRole("button", { name: "Calibration" }).press("Enter");
      await folders.getByRole("button", { name: "Bed" }).click();
      await folders.getByRole("button", { name: String(suffix) }).click();
      await expect(path).toHaveText(`gcodesCalibrationBed${suffix}`);
      await files.getByRole("button", { name: "spare-square.gcode" }).press("Enter");
      await expect(fileInspector.getByText(disposable, { exact: true })).toBeVisible();
      await expect(fileInspector.getByRole("link", { name: "Open Model" })).toHaveCount(0);
      await page.screenshot({ path: testInfo.outputPath("files-deep.png") });

      const search = page.getByRole("searchbox", { name: "Search files" });
      await search.fill(modelName.toUpperCase());
      await expect(search).toBeFocused();
      await expect(files.getByRole("button", { name: modelName })).toBeVisible();
      await expect(files.getByRole("button", { name: "bed-square.gcode" })).toHaveCount(0);
      await search.clear();
      await files.getByRole("button", { name: "spare-square.gcode" }).click();
      await fileInspector.getByRole("button", { name: "Delete", exact: true }).click();
      const confirm = page.getByRole("dialog", { name: "Delete printer file?" });
      await expect(confirm.getByText(disposable, { exact: false })).toBeVisible();
      const deleted = page.waitForResponse(
        (response) =>
          response.url().includes(`${api}/files/`) && response.request().method() === "DELETE",
      );
      await confirm.getByRole("button", { name: "Delete", exact: true }).click();
      expect((await deleted).ok()).toBe(true);
      await expect(files.getByRole("button", { name: "spare-square.gcode" })).toHaveCount(0);
      const liveInventory = await request.get(`${emulator}/server/files/list`);
      expect(
        (await liveInventory.json()).result.map((entry: { path: string }) => entry.path),
      ).not.toContain(disposable);

      // ── Start the selected nested file through the ordinary action ──
      await files.getByRole("button", { name: "bed-square.gcode" }).click();
      await expect(fileInspector.getByText(external, { exact: true })).toBeVisible();
      const startedJob = await startSelectedFile();
      expect(startedJob.remote_filename).toBe(external);
      expect((await request.post(`${emulator}/printer/print/pause`)).ok()).toBe(true);
      await expect(page).toHaveURL(new RegExp(`${route}$`));
      await expect(dashboard.getByText(external, { exact: true })).toBeVisible();
      expect((await request.post(`${emulator}/printer/print/cancel`)).ok()).toBe(true);
      await waitForJob(startedJob.id, "cancelled");

      // ── Compare actual runs and recover the original queue workflow ──
      await page.goto(`${route}?tab=jobs`);
      const jobs = page.getByRole("table", { name: "Print history" });
      const run = page.getByRole("complementary", { name: "Selected run" });
      const outcomes = page.getByRole("combobox", { name: "Outcome" });
      const jobRow = (id: number) =>
        jobs.getByRole("row").filter({
          has: page.getByText(new RegExp(`^Run #${id}(?: ·|$)`)),
        });
      await expect(jobs.getByRole("columnheader")).toHaveText([
        "Print",
        "Outcome",
        "Finished",
        "Duration",
      ]);
      await expect(jobRow(startedJob.id)).toContainText("Cancelled");
      await expect(
        jobRow(startedJob.id).getByRole("button", { name: "bed-square.gcode" }),
      ).toBeVisible();
      await expect(jobRow(uploadOnly.id).getByRole("cell").last()).toHaveText("—");
      await expect(jobRow(completed.id).getByRole("cell").last()).not.toHaveText("—");
      const noStart = "No print start recorded";
      const transferOutcome = jobRow(uploadOnly.id).getByRole("cell", {
        name: `Completed ${noStart}`,
        exact: true,
      });
      await expect(transferOutcome).toBeVisible();
      await expect(
        jobRow(uploadOnly.id).getByRole("button", { name: modelName }),
      ).toHaveAccessibleDescription(new RegExp(`Completed ${noStart}$`));
      await transferOutcome.click();
      await expect(run.getByText(noStart, { exact: true })).toBeVisible();
      await expect(run.getByRole("button", { name: "Queue again" })).toHaveCount(0);
      await page.screenshot({ path: testInfo.outputPath("jobs-no-start.png") });
      await jobRow(completed.id).getByRole("cell", { name: "Completed", exact: true }).click();
      await expect(run.getByText(noStart, { exact: true })).toHaveCount(0);
      await expect(jobRow(completed.id).getByRole("button", { name: modelName })).toBeFocused();
      await expect(run.getByRole("button", { name: "Queue again" })).toBeEnabled();
      await expect(run.getByRole("link", { name: "Open Model" })).toHaveAttribute(
        "href",
        `/models/${modelId}`,
      );
      await page.screenshot({ path: testInfo.outputPath("jobs-mixed.png") });

      await outcomes.selectOption("cancelled");
      await expect(jobRow(completed.id)).toHaveCount(0);
      await expect(jobRow(startedJob.id)).toBeVisible();
      await expect(run.getByRole("button", { name: "Queue again" })).toHaveCount(0);
      await run.getByRole("button", { name: "Evidence", exact: true }).click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(run.getByRole("button", { name: "Evidence", exact: true })).toBeFocused();
      await outcomes.selectOption("");
      await page.getByRole("searchbox", { name: "Search runs" }).fill(modelName);
      await expect(jobRow(startedJob.id)).toHaveCount(0);
      await expect(jobRow(completed.id)).toBeVisible();
      await page.reload();
      await expect(jobRow(startedJob.id)).toBeVisible();
    } finally {
      for (const path of [external, disposable]) {
        await request.delete(`${emulator}/server/files/gcodes/${path}`);
      }
      await page.request.delete(api);
      await page.request.delete(`/api/v1/models/${modelId}`);
    }
  });
});
