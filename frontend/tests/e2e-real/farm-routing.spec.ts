/** A map connection changes the persisted queue destination after operator confirmation. */
import { expect, test } from "./helpers";
import { collapseNavigationSidebar, gcodeFor } from "./util";
import type { PrinterRead, PrintJobRead } from "@/types";

const emulatorOrigin = `http://127.0.0.1:${process.env.PLAYWRIGHT_MOCK_PRINTER_PORT ?? "7530"}`;

test.describe("farm routing canvas", () => {
  test("reroutes a queued Revision by connecting its port to a printer", async ({ page }) => {
    const name = "Route map " + Date.now();
    const upload = await page.request.post("/api/v1/ingest/orca", {
      multipart: {
        file: {
          name: "routing.gcode",
          mimeType: "text/plain",
          buffer: Buffer.from(gcodeFor(name)),
        },
        model_name: name,
      },
    });
    expect(upload.status()).toBe(202);
    const ingest = "/api/v1/jobs/" + (await upload.json()).job_id;
    await expect
      .poll(async () => (await (await page.request.get(ingest)).json()).state)
      .toBe("completed");
    const revision = await (await page.request.get(ingest)).json();
    const printers: PrinterRead[] = [];
    let job: PrintJobRead | undefined;
    try {
      for (const suffix of ["A", "B"]) {
        const registered = await page.request.post("/api/v1/printers", {
          data: {
            name: name + " " + suffix,
            provider: "moonraker",
            moonraker_url: emulatorOrigin,
          },
        });
        expect(registered.status()).toBe(201);
        const machine: PrinterRead = await registered.json();
        printers.push(machine);
        expect(
          (
            await page.request.patch("/api/v1/fleet/printers/" + machine.id + "/routing", {
              data: { drain_mode: true },
            })
          ).ok(),
        ).toBeTruthy();
      }
      const [source, destination] = printers;
      const queued = await page.request.post("/api/v1/fleet/queue", {
        data: {
          file_id: revision.file_id,
          strategy: "manual",
          printer_id: source.id,
          priority: "rush",
        },
      });
      expect(queued.status(), await queued.text()).toBe(201);
      const queuedJob: PrintJobRead = await queued.json();
      job = queuedJob;
      await page.goto("/printers?tab=queue");
      await collapseNavigationSidebar(page);
      const jobNode = page.locator('.react-flow__node[data-id="job-' + queuedJob.id + '"]');
      const destNode = page.locator('.react-flow__node[data-id="printer-' + destination.id + '"]');
      const inspector = page.getByRole("complementary", { name: "Route inspector" });
      await jobNode.getByRole("button").focus();
      await page.keyboard.press("Enter");
      await expect(inspector).toContainText(source.name);
      await inspector.getByRole("button", { name: "Edit routing" }).click();
      const editor = page.getByRole("dialog", { name: "Edit queue job" });
      await editor.getByRole("button", { name: "Cancel" }).click();
      await expect(editor).toHaveCount(0);
      const output = await jobNode.locator(".source").boundingBox();
      const input = await destNode.locator(".target").boundingBox();
      if (!output || !input) throw new Error("Both routing ports must be visible");
      await page.mouse.move(output.x + output.width / 2, output.y + output.height / 2);
      await page.mouse.down();
      await page.mouse.move(input.x + input.width / 2, input.y + input.height / 2, { steps: 18 });
      await page.mouse.up();
      await expect(editor.getByLabel("Printer", { exact: true })).toHaveValue(
        String(destination.id),
      );
      await editor.getByRole("button", { name: "Save job" }).click();
      await expect(editor).toHaveCount(0);
      await page.reload();
      await jobNode.getByRole("button").click();
      await expect(inspector).toContainText(destination.name);
      await expect(
        page.locator(
          '.react-flow__edge[data-id="rule-' + queuedJob.id + ":printer-" + destination.id + '"]',
        ),
      ).toBeVisible();

      const route = page.locator(
        `.routing-link-printing[data-id="dispatcher:printer-${destination.id}"] .react-flow__edge-path`,
      );
      await expect(route).toHaveCount(0);
      const started = await page.request.post(`${emulatorOrigin}/printer/print/start`, {
        params: { filename: "demo.gcode" },
      });
      expect(started.ok()).toBeTruthy();
      await expect(route).toHaveCSS("animation-name", "routing-flow");
      await expect(jobNode).toBeVisible();
      await expect(route).toHaveCount(0);
    } finally {
      if (job) await page.request.delete("/api/v1/fleet/queue/" + job.id);
      for (const p of printers) await page.request.delete("/api/v1/printers/" + p.id);
      await page.request.delete("/api/v1/models/" + revision.model_id);
    }
  });
});
