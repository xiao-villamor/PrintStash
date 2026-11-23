/** A map connection changes the persisted queue destination after operator confirmation. */
import { expect, test } from "./helpers";
import { collapseNavigationSidebar, gcodeFor } from "./util";
import type { PrinterRead, PrintJobRead } from "@/types";

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
            moonraker_url:
              "http://127.0.0.1:" + (process.env.PLAYWRIGHT_MOCK_PRINTER_PORT ?? "7530"),
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
      await page.goto("/printers?tab=queue");
      await collapseNavigationSidebar(page);
      await page.getByRole("button", { name: "Add to queue", exact: true }).first().click();
      const composer = page.getByRole("dialog", { name: "Add to queue", exact: true });
      await composer.getByRole("button", { name: "Revision", exact: true }).click();
      const picker = page.getByRole("dialog", { name: "Revision", exact: true });
      await picker.getByRole("button", { name, exact: true }).click();
      await picker.getByRole("button", { name: /Revision 1/ }).click();
      await composer.getByRole("radio", { name: new RegExp(source.name) }).check();
      await composer.getByRole("radio", { name: "Rush", exact: true }).check();
      const queued = page.waitForResponse(
        (r) => r.url().endsWith("/fleet/queue") && r.request().method() === "POST",
      );
      await composer.getByRole("button", { name: "Save job" }).click();
      const response = await queued;
      expect(response.status()).toBe(201);
      const saved: PrintJobRead = await response.json();
      job = saved;
      expect(job).toMatchObject({
        printer_id: source.id,
        file_id: revision.file_id,
        priority: "rush",
      });
      await expect(composer).toHaveCount(0);
      const jobNode = page.locator('.react-flow__node[data-id="job-' + job.id + '"]');
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
          '.react-flow__edge[data-id="rule-' + job.id + ":printer-" + destination.id + '"]',
        ),
      ).toBeVisible();
    } finally {
      if (job) await page.request.delete("/api/v1/fleet/queue/" + job.id);
      for (const p of printers) await page.request.delete("/api/v1/printers/" + p.id);
      await page.request.delete("/api/v1/models/" + revision.model_id);
    }
  });
});
