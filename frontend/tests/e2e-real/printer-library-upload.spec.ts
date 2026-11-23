/** Upload a saved Revision from Files through the real backend to a disposable Moonraker. */
import type { PrinterRead, PrintJobRead } from "@/types";
import { expect, test } from "./helpers";
import { collapseNavigationSidebar, gcodeFor } from "./util";

const emulator = `http://127.0.0.1:${process.env.PLAYWRIGHT_MOCK_PRINTER_PORT ?? "7530"}`;

test.describe("printer library upload", () => {
  test("prepares a saved Revision from Files without starting a print", async ({
    page,
    request,
  }) => {
    const model = `File preparation ${Date.now()}`;
    const seed = await page.request.post("/api/v1/ingest/orca", {
      multipart: {
        file: {
          name: "preparation.gcode",
          mimeType: "text/plain",
          buffer: Buffer.from(gcodeFor(model)),
        },
        model_name: model,
      },
    });
    expect(seed.status(), await seed.text()).toBe(202);
    const jobPath = `/api/v1/jobs/${(await seed.json()).job_id}`;
    await expect
      .poll(async () => (await (await page.request.get(jobPath)).json()).state)
      .toBe("completed");
    const { model_id: modelId, file_id: fileId } = await (await page.request.get(jobPath)).json();

    const registration = await page.request.post("/api/v1/printers", {
      data: { name: model, provider: "moonraker", moonraker_url: emulator },
    });
    expect(registration.status(), await registration.text()).toBe(201);
    const printer: PrinterRead = await registration.json();
    const printerPath = `/printers/${printer.id}`;
    const api = `/api/v1${printerPath}`;
    try {
      await page.goto(`${printerPath}?tab=files`);
      await collapseNavigationSidebar(page);
      await page.getByRole("button", { name: "Upload from library" }).click();
      const dialog = page.getByRole("dialog", { name: "Upload from library" });
      await expect(dialog.getByText(printer.name, { exact: false }).first()).toBeVisible();
      await dialog.getByRole("button", { name: "Revision" }).click();
      const picker = page.getByRole("dialog", { name: "Revision", exact: true });
      await picker.getByRole("button", { name: model }).click();
      await picker.getByRole("button", { name: /Revision 1/ }).click();
      expect(await (await page.request.get(`${api}/jobs`)).json()).toEqual([]);

      const transfer = page.waitForResponse(
        (response) =>
          response.url().endsWith(`${api}/send`) && response.request().method() === "POST",
      );
      await dialog.getByRole("button", { name: "Upload file", exact: true }).click();
      const response = await transfer;
      expect(response.status(), await response.text()).toBe(200);
      expect(response.request().postDataJSON()).toEqual({ file_id: fileId, start_print: false });
      const receipt: PrintJobRead = await response.json();
      expect(receipt.state).toBe("completed");
      expect(receipt.started_at).toBeNull();
      await expect(page.getByText(`Uploaded ${receipt.remote_filename}`)).toBeVisible();

      await page.getByRole("button", { name: "Inspect file" }).click();
      await expect(page.getByRole("complementary", { name: "Selected file" })).toContainText(
        receipt.remote_filename,
      );
      const firmware = await request.get(
        `${emulator}/printer/objects/query?print_stats=state,filename`,
      );
      expect((await firmware.json()).result.status.print_stats.filename).not.toBe(
        receipt.remote_filename,
      );
      const history: PrintJobRead[] = await (await page.request.get(`${api}/jobs`)).json();
      expect(history.map(({ id, state }) => ({ id, state }))).toEqual([
        { id: receipt.id, state: "completed" },
      ]);
    } finally {
      await page.request.delete(api);
      await page.request.delete(`/api/v1/models/${modelId}`);
      await page.request.delete(`/api/v1/models/${modelId}/purge`);
    }
  });
});
