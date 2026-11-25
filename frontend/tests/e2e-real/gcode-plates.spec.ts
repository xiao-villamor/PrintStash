/** Uploaded sliced 3MF files show the exact selected plate's toolpath in the browser. */
import { selectModelDetailTab } from "../model-detail-navigation";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { test, expect } from "./helpers";
import { collapseNavigationSidebar, openModelPage, uploadGcodeModel, uploadModel } from "./util";
import type { Locator, Page } from "@playwright/test";
import type { PrintJobRead } from "@/types";

async function clippedControls(panel: Locator): Promise<string[]> {
  return panel.evaluate((element) => {
    const boundary = element.getBoundingClientRect();
    return [...element.querySelectorAll<HTMLElement>("button, input, select")]
      .filter((control) => {
        const box = control.getBoundingClientRect();
        // A hidden tab is deliberately absent from layout; overflow-clipped
        // controls still have a box and must not be mistaken for visible ones.
        return (
          box.height > 0 &&
          (box.top < boundary.top ||
            box.bottom > boundary.bottom ||
            box.left < boundary.left ||
            box.right > boundary.right)
        );
      })
      .map(
        (control) =>
          control.getAttribute("aria-label") ?? control.textContent?.trim() ?? control.tagName,
      );
  });
}

async function coveredControls(panel: Locator): Promise<string[]> {
  await panel.scrollIntoViewIfNeeded();
  return panel.evaluate((element) =>
    [
      ...element.querySelectorAll<HTMLElement>(
        "button:not(:disabled), input:not(:disabled), select:not(:disabled)",
      ),
    ]
      .filter((control) => {
        const box = control.getBoundingClientRect();
        if (box.height === 0) return false;
        const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
        return hit === null || !control.contains(hit);
      })
      .map(
        (control) =>
          control.getAttribute("aria-label") ?? control.textContent?.trim() ?? control.tagName,
      ),
  );
}

async function uploadSlicedFixture(page: Page, name: string): Promise<number> {
  const upload = await page.request.post("/api/v1/ingest/model", {
    multipart: {
      file: { name: `${name}.gcode.3mf`, mimeType: "model/3mf", buffer: slicedArchive(name) },
      model_name: name,
    },
  });
  expect(upload.status()).toBe(202);
  const jobId = (await upload.json()).job_id;
  let modelId = 0;
  await expect
    .poll(
      async () => {
        const job = await (await page.request.get(`/api/v1/jobs/${jobId}`)).json();
        modelId = job.model_id;
        return job.state;
      },
      { timeout: 60_000 },
    )
    .toBe("completed");
  return modelId;
}

async function hasRenderedPixels(canvas: Locator): Promise<boolean> {
  return canvas.evaluate((source: HTMLCanvasElement) => {
    const snapshot = document.createElement("canvas");
    snapshot.width = source.width;
    snapshot.height = source.height;
    const context = snapshot.getContext("2d");
    if (context === null) throw new Error("Canvas readback unavailable");
    context.drawImage(source, 0, 0);
    return context
      .getImageData(0, 0, snapshot.width, snapshot.height)
      .data.some((value, index) => index % 4 === 3 && value > 0);
  });
}

function slicedArchive(name: string, withMesh = false, dense = false): Buffer {
  const backend = resolve("../backend");
  return execFileSync(
    resolve(backend, ".venv/bin/python"),
    [
      "-c",
      `import io, sys, zipfile
from tests.fixtures.three_mf_projects import build_sliced_project
original = build_sliced_project(marker=sys.argv[1], with_mesh=sys.argv[2] == 'true')
if sys.argv[3] == 'true':
    result = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as source, zipfile.ZipFile(result, 'w', compression=zipfile.ZIP_DEFLATED) as target:
        for member in source.namelist():
            content = source.read(member)
            if member == 'Metadata/plate_1.gcode':
                content = ('; ' + sys.argv[1] + '\\nG1 X100\\nM83\\n' + '\\n'.join(f'G1 Z{(i+1)*0.2}\\nG2 I-100 E1\\nG2 I-100 E1' for i in range(750))).encode()
            target.writestr(member, content)
    original = result.getvalue()
sys.stdout.buffer.write(original)`,
      name,
      String(withMesh),
      String(dense),
    ],
    { cwd: backend },
  );
}

function namedPlateProject(name: string, count: number, sliced: boolean): Buffer {
  const backend = resolve("../backend");
  return execFileSync(
    resolve(backend, ".venv/bin/python"),
    [
      "-c",
      `import io, math, sys, zipfile
from xml.etree import ElementTree as ET
from tests.fixtures.three_mf_projects import build_multi_plate_project, build_sliced_project
marker, count, sliced = sys.argv[1], int(sys.argv[2]), sys.argv[3] == 'true'
columns = math.ceil(math.sqrt(count))
project = io.BytesIO(build_multi_plate_project(
    marker=marker, names=tuple(f'Part {i+1}' for i in range(count)),
    placements=tuple((128 + i % columns * 307.2, 128 - i // columns * 307.2) for i in range(count)),
))
if sliced:
    gcode = build_sliced_project(marker=marker, plate_numbers=tuple(range(1, count+1)))
    sliced_project = io.BytesIO()
    with zipfile.ZipFile(project) as mesh, zipfile.ZipFile(io.BytesIO(gcode)) as source, zipfile.ZipFile(sliced_project, 'w') as target:
        slice_info = ET.Element('config')
        for member in mesh.namelist():
            content = mesh.read(member)
            if member == 'Metadata/model_settings.config':
                config = ET.fromstring(content)
                for i, plate in enumerate(config.findall('plate'), 1):
                    ET.SubElement(plate, 'metadata', key='gcode_file', value=f'Metadata/plate_{i}.gcode')
                    info = ET.SubElement(slice_info, 'plate')
                    ET.SubElement(info, 'metadata', key='index', value=str(i))
                    ET.SubElement(info, 'object', id=str(i))
                content = ET.tostring(config)
            target.writestr(member, content)
        for member in source.namelist():
            target.writestr(member, source.read(member))
        target.writestr('Metadata/slice_info.config', ET.tostring(slice_info))
    project = sliced_project
sys.stdout.buffer.write(project.getvalue())`,
      name,
      String(count),
      String(sliced),
    ],
    { cwd: backend },
  );
}

test.describe("Sliced G-code plates", () => {
  for (const count of [2, 9]) {
    test(`keeps the selected plate visible with ${count} plates`, async ({ page }, testInfo) => {
      const name = `e2e-plate-tray-${count}-${Date.now()}`;
      await page.emulateMedia({ colorScheme: "dark" });
      await uploadModel(page, name, {
        meshFile: {
          name: `${name}.3mf`,
          mimeType: "model/3mf",
          buffer: namedPlateProject(name, count, false),
        },
        gcodeFile: {
          name: `${name}.gcode.3mf`,
          mimeType: "model/3mf",
          buffer: namedPlateProject(name, count, true),
        },
      });
      await openModelPage(page, name);
      await collapseNavigationSidebar(page);
      const modelId = Number(new URL(page.url()).pathname.split("/").pop());
      const preview = page.getByRole("region", { name: "Preview", exact: true });
      const source = preview.getByRole("group", { name: "Plates", exact: true });
      const sliced = preview.getByRole("group", { name: "Sliced plates", exact: true });
      const last = new RegExp(`^Plate ${String(count).padStart(2, "0")}\\b`);

      async function assertTray(group: Locator) {
        const canvas = (await preview.locator(".detail-preview-canvas").boundingBox())!;
        const dock = (await preview.locator(".detail-viewer-dock").boundingBox())!;
        const tray = (await group.boundingBox())!;
        expect(canvas.y + canvas.height).toBeCloseTo(dock.y, 0);
        expect(dock.y + dock.height).toBeCloseTo(tray.y, 0);
        expect(tray.width).toBeCloseTo(dock.width, 0);
        if (count > 2) {
          expect(
            await group
              .locator(".model-plate-options")
              .evaluate((list) => list.scrollHeight > list.clientHeight),
          ).toBe(true);
        }
        const selected = group.getByRole("button", { name: last });
        await expect(selected).toHaveAttribute("aria-pressed", "true");
        await expect(selected).toBeInViewport({ ratio: 0.99 });
      }

      try {
        const lastSourcePlate = source.getByRole("button", { name: last });
        await expect(lastSourcePlate).toBeVisible();
        await lastSourcePlate.click();
        await expect(
          preview.getByText("Selected plate mesh bounds", { exact: false }),
        ).toBeVisible();
        await assertTray(source);
        await page.screenshot({ path: testInfo.outputPath("plates-mesh.png") });

        const toolpath = page.waitForResponse(
          (response) => response.url().includes(`/embedded-gcode?plate_index=${count}`),
          { timeout: 25_000 },
        );
        await preview.getByRole("button", { name: "G-code", exact: true }).click();
        expect((await toolpath).ok()).toBe(true);
        await expect(preview.getByRole("spinbutton", { name: "Layer number" })).toHaveValue("2");
        await assertTray(sliced);
        await page.screenshot({ path: testInfo.outputPath("plates-gcode.png") });

        await preview.getByRole("button", { name: "3D model", exact: true }).click();
        await assertTray(source);
      } finally {
        expect((await page.request.delete(`/api/v1/models/${modelId}`)).status()).toBe(204);
        expect((await page.request.delete(`/api/v1/models/${modelId}/purge`)).ok()).toBe(true);
      }
    });
  }

  for (const viewport of [
    { width: 1280, height: 720, theme: "light" },
    { width: 1440, height: 1000, theme: "light" },
    { width: 1920, height: 1080, theme: "dark" },
  ]) {
    test(`fits sidebar controls at ${viewport.width}×${viewport.height} in ${viewport.theme}`, async ({
      page,
    }) => {
      await page.setViewportSize(viewport);
      await page.addInitScript(
        (theme) => localStorage.setItem("printstash.theme", theme),
        viewport.theme,
      );
      const name = `e2e-controls-fit-${Date.now()}`;
      const modelId = await uploadSlicedFixture(page, name);
      try {
        await page.goto(`/models/${modelId}`);
        await collapseNavigationSidebar(page);
        await page.getByRole("button", { name: "G-code", exact: true }).click();
        const panel = page.getByRole("complementary", { name: "Toolpath controls" });
        await expect(panel.getByRole("spinbutton", { name: "Layer number" })).toHaveValue("2");
        expect(await clippedControls(panel)).toEqual([]);
        await panel.getByRole("tab", { name: "View", exact: true }).click();
        await expect(panel.getByRole("button", { name: "Travel", exact: true })).toBeVisible();
        expect(await clippedControls(panel)).toEqual([]);
        expect(await coveredControls(panel)).toEqual([]);
        const bounds = panel.getByRole("region", { name: "Toolpath bounds", exact: true });
        await bounds.scrollIntoViewIfNeeded();
        await expect(bounds).toBeInViewport({ ratio: 0.99 });
        await panel.evaluate((element) => element.scrollTo({ top: 0 }));
        const preview = page.getByRole("region", { name: "Preview", exact: true });
        const previewBox = await preview.boundingBox();
        const panelBox = await panel.boundingBox();
        expect(panelBox!.y + panelBox!.height).toBeLessThanOrEqual(
          previewBox!.y + previewBox!.height,
        );
        const information = await page
          .getByRole("region", { name: "Model details", exact: true })
          .boundingBox();
        expect(information!.x).toBeGreaterThanOrEqual(previewBox!.x + previewBox!.width);
        await page.keyboard.press("Control+Alt+b");
        await expect(page.getByRole("heading", { name: "Model details" })).toBeHidden();
        expect(await clippedControls(panel)).toEqual([]);
        expect(await coveredControls(panel)).toEqual([]);
        await preview.scrollIntoViewIfNeeded();
        await page.screenshot({
          path: test.info().outputPath("expanded-view-controls.png"),
          fullPage: true,
        });

        await page.keyboard.press("Escape");
        await expect(page.getByRole("heading", { name: "Model details" })).toBeVisible();
        await selectModelDetailTab(page, /^Files/);
        await page.getByText("File details", { exact: true }).click();
        await expect(panel.getByRole("tab", { name: "View", exact: true })).toHaveAttribute(
          "aria-selected",
          "true",
        );
        expect(await clippedControls(panel)).toEqual([]);
        await page.getByRole("tab", { name: "Files", exact: true }).click();
        expect(await clippedControls(panel)).toEqual([]);
        await panel.getByRole("tab", { name: "Layers", exact: true }).click();
        await preview
          .getByRole("group", { name: "Sliced plates", exact: true })
          .getByRole("button", { name: "Plate 02", exact: true })
          .click();
        await expect(panel.getByRole("spinbutton", { name: "Layer number" })).toHaveValue("2");
        expect(await clippedControls(panel)).toEqual([]);
        await expect
          .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
          .toBe(true);
        await page.screenshot({
          path: test.info().outputPath("normal-layer-controls.png"),
          fullPage: true,
        });
      } finally {
        expect((await page.request.delete(`/api/v1/models/${modelId}`)).status()).toBe(204);
      }
    });
  }

  test("previews a dense multiplate revision with side panel controls", async ({ page }) => {
    const name = `e2e-dense-plates-${Date.now()}`;
    const upload = await page.request.post("/api/v1/ingest/model", {
      multipart: {
        file: {
          name: `${name}.gcode.3mf`,
          mimeType: "model/3mf",
          buffer: slicedArchive(name, false, true),
        },
        model_name: name,
      },
    });
    expect(upload.status()).toBe(202);
    const jobId = (await upload.json()).job_id;
    let modelId = 0;
    await expect
      .poll(
        async () => {
          const job = await (await page.request.get(`/api/v1/jobs/${jobId}`)).json();
          modelId = job.model_id;
          return job.state;
        },
        { timeout: 60_000 },
      )
      .toBe("completed");
    try {
      await page.goto(`/models/${modelId}`);
      await collapseNavigationSidebar(page);
      await page.getByRole("button", { name: "Layers", exact: true }).click();
      const panel = page.getByRole("complementary", { name: "Toolpath controls" });
      const entry = panel.getByRole("spinbutton", { name: "Layer number" });
      await expect(entry).toHaveValue("750");
      await expect.poll(() => hasRenderedPixels(page.locator("canvas"))).toBe(true);
      const desktopCanvas = await page.locator("canvas").boundingBox();
      const desktopPanel = await panel.boundingBox();
      expect(desktopPanel?.y).toBe(desktopCanvas?.y);
      expect(desktopPanel!.x).toBeGreaterThan(desktopCanvas!.x);
      expect(desktopPanel?.height).toBe(desktopCanvas?.height);
      await entry.fill("375");
      await entry.press("Enter");
      await expect(panel.getByRole("slider")).toHaveAttribute("aria-valuetext", "Layer 375 / 750");
      await panel.getByRole("button", { name: "This layer" }).click();
      await expect(panel.getByRole("button", { name: "This layer" })).toHaveAttribute(
        "aria-pressed",
        "true",
      );
      await panel.getByRole("tab", { name: "View", exact: true }).click();
      await panel.getByRole("combobox", { name: "Camera view" }).selectOption("top");
      await panel.getByRole("button", { name: "Build plate grid" }).click();
      await expect(panel.getByRole("button", { name: "Build plate grid" })).toHaveAttribute(
        "aria-pressed",
        "false",
      );
      await expect(page.getByRole("button", { name: "Solid", exact: true })).toHaveCount(0);
      await page.getByRole("button", { name: "Screenshot", exact: true }).scrollIntoViewIfNeeded();
      await page.screenshot({ path: test.info().outputPath("dense-sidebar.png") });

      await panel.getByRole("tab", { name: "Layers", exact: true }).click();
      const second = panel.getByRole("button", { name: "Plate 02", exact: true });
      await second.focus();
      await second.press("Enter");
      await expect(second).toBeFocused();
      await expect(entry).toHaveValue("2");
      await expect.poll(() => hasRenderedPixels(page.locator("canvas"))).toBe(true);
      await panel.getByRole("button", { name: "Plate 01", exact: true }).click();
      await expect(entry).toHaveValue("750");
      await panel.getByRole("tab", { name: "View", exact: true }).click();
      await panel.getByRole("button", { name: "Fit to view" }).click();
      expect(await clippedControls(panel)).toEqual([]);
      expect(await coveredControls(panel)).toEqual([]);
      await expect.poll(() => hasRenderedPixels(page.locator("canvas"))).toBe(true);
      await expect(page.getByRole("alert")).toHaveCount(0);
    } finally {
      expect((await page.request.delete(`/api/v1/models/${modelId}`)).status()).toBe(204);
    }
  });

  test("prints the selected plate of an attached revision", async ({ page }) => {
    const name = `e2e-sliced-revision-${Date.now()}`;
    await uploadGcodeModel(page, name);
    await openModelPage(page, name);
    await collapseNavigationSidebar(page);
    const modelId = Number(new URL(page.url()).pathname.split("/").pop());
    const addedPrinter = await page.request.post("/api/v1/printers", {
      data: {
        name: `${name}-printer`,
        provider: "moonraker",
        moonraker_url: `http://127.0.0.1:${process.env.PLAYWRIGHT_MOCK_PRINTER_PORT ?? "7530"}`,
      },
    });
    expect(addedPrinter.status()).toBe(201);
    const printerId = (await addedPrinter.json()).id;
    try {
      await page.getByRole("tab", { name: /Print/ }).click();
      await page.getByRole("button", { name: "Add revision", exact: true }).click();
      const upload = page.getByRole("dialog", { name: "Add G-code revision" });
      await upload.locator("input[type=file]").setInputFiles({
        name: `${name}.gcode.3mf`,
        mimeType: "model/3mf",
        buffer: slicedArchive(name),
      });
      await upload.getByRole("radio", { name: "Make this Revision recommended" }).check();
      await upload.getByRole("button", { name: "Add revision", exact: true }).click();
      await expect(upload).toHaveCount(0);
      await page.reload();
      const model = await (await page.request.get(`/api/v1/models/${modelId}`)).json();
      const revision = model.files.find((file: { original_filename: string }) =>
        file.original_filename.endsWith(".gcode.3mf"),
      );
      expect(revision.gcode_revision_number).toBe(2);
      expect(revision.is_recommended).toBe(true);
      await page.getByRole("button", { name: "Preview G-code", exact: true }).click();
      await page
        .getByRole("group", { name: "Sliced plates" })
        .getByRole("button", { name: "Plate 02" })
        .click();
      await expect(page.getByRole("slider")).toBeVisible();
      await page.getByRole("button", { name: "Prepare print", exact: true }).click();
      const send = page.getByRole("dialog", { name: "Send Rev 2 to printer" });
      await expect(
        send
          .getByRole("group", { name: "Sliced plate", exact: true })
          .getByRole("radio", { name: /^Plate 02/ }),
      ).toBeChecked();
      await send.getByRole("checkbox", { name: "Start print immediately" }).check();
      await page.screenshot({ path: test.info().outputPath("sliced-revision-send.png") });
      const sent = page.waitForResponse(
        (response) =>
          response.url().endsWith(`/api/v1/printers/${printerId}/send`) &&
          response.request().method() === "POST",
      );
      await send.getByRole("button", { name: "Send & start print", exact: true }).click();
      const result = await sent;
      expect(result.status()).toBe(200);
      const job = await result.json();
      expect(job.file_id).toBe(revision.id);
      expect(job.plate_index).toBe(2);
      expect(job.state).toBe("started");
      expect(job.remote_filename).toContain("plate-2");
      await expect(send).toHaveCount(0);
    } finally {
      await page.request.delete(`/api/v1/printers/${printerId}`);
      await page.request.delete(`/api/v1/models/${modelId}`);
    }
  });

  test("queues the exact sliced plate again from completed Jobs history", async ({ page }) => {
    const name = `e2e-queue-again-${Date.now()}`;
    const upload = await page.request.post("/api/v1/ingest/model", {
      multipart: {
        file: { name: `${name}.gcode.3mf`, mimeType: "model/3mf", buffer: slicedArchive(name) },
        model_name: name,
      },
    });
    expect(upload.status()).toBe(202);
    const { job_id: ingestJobId } = await upload.json();
    await expect
      .poll(
        async () => (await (await page.request.get(`/api/v1/jobs/${ingestJobId}`)).json()).state,
      )
      .toBe("completed");
    const { model_id: modelId, file_id: fileId } = await (
      await page.request.get(`/api/v1/jobs/${ingestJobId}`)
    ).json();

    const addedPrinter = await page.request.post("/api/v1/printers", {
      data: {
        name: `${name}-printer`,
        provider: "moonraker",
        moonraker_url: `http://127.0.0.1:${process.env.PLAYWRIGHT_MOCK_PRINTER_PORT ?? "7530"}`,
      },
    });
    expect(addedPrinter.status()).toBe(201);
    const { id: printerId } = await addedPrinter.json();

    try {
      const first = await page.request.post("/api/v1/fleet/queue", {
        data: { file_id: fileId, plate_index: 2, strategy: "manual", printer_id: printerId },
      });
      expect(first.status()).toBe(201);
      const job: PrintJobRead = await first.json();
      const jobsPath = `/api/v1/printers/${printerId}/jobs`;
      const readJobs = async (): Promise<PrintJobRead[]> => {
        const response = await page.request.get(jobsPath);
        expect(response.ok()).toBe(true);
        return response.json();
      };
      await expect
        .poll(async () => (await readJobs()).find(({ id }) => id === job.id)?.state, {
          timeout: 30_000,
        })
        .toBe("completed");

      await page.goto(`/printers/${printerId}`);
      await collapseNavigationSidebar(page);
      await page.getByRole("tab", { name: "Jobs", exact: true }).click();
      const history = page.getByRole("table", { name: "Print history" });
      await history.getByRole("button", { name, exact: true }).click();
      await page
        .getByRole("complementary", { name: "Selected run" })
        .getByRole("button", { name: "Queue again" })
        .click();
      const repeat = page.getByRole("dialog", { name: "Add to queue" });
      await expect(repeat.getByText(`${name} · Revision 1`)).toBeVisible();
      await expect(repeat.getByText("Sliced plate: 2")).toBeVisible();
      await expect(repeat.getByText(`${name}-printer`, { exact: true })).toBeVisible();
      await expect(repeat.getByRole("button", { name: "Add to queue" })).toBeEnabled();
      expect(
        (await readJobs()).filter((run) => run.file_id === fileId).map(({ id }) => id),
      ).toEqual([job.id]);
      await page.screenshot({ path: test.info().outputPath("queue-again.png") });

      const queued = page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/fleet/queue") && response.request().method() === "POST",
      );
      await repeat.getByRole("button", { name: "Add to queue" }).click();
      const receipt = await queued;
      expect(receipt.status()).toBe(201);
      const duplicate: PrintJobRead = await receipt.json();
      expect(duplicate.id).not.toBe(job.id);
      expect(duplicate).toMatchObject({
        model_id: modelId,
        file_id: job.file_id,
        plate_index: 2,
        printer_id: printerId,
      });
      await expect(repeat).toHaveCount(0);

      await page.reload();
      await page.getByRole("tab", { name: "Jobs", exact: true }).click();
      for (const id of [job.id, duplicate.id]) {
        await expect(history.getByText(new RegExp(`^Run #${id}(?: ·|$)`))).toBeVisible();
      }
      await expect
        .poll(async () => (await readJobs()).find(({ id }) => id === duplicate.id)?.state, {
          timeout: 30_000,
        })
        .toBe("completed");
      expect((await readJobs()).find(({ id }) => id === job.id)?.state).toBe("completed");
    } finally {
      await page.request.delete(`/api/v1/printers/${printerId}`);
      await page.request.delete(`/api/v1/models/${modelId}`);
    }
  });

  for (const withMesh of [false, true]) {
    test(`previews each plate ${withMesh ? "with" : "without"} source meshes`, async ({ page }) => {
      const name = `e2e-gcode-plates-${Date.now()}`;
      const archive = slicedArchive(name, withMesh);
      const upload = await page.request.post("/api/v1/ingest/model", {
        multipart: {
          file: { name: `${name}.gcode.3mf`, mimeType: "model/3mf", buffer: archive },
          model_name: name,
        },
      });
      expect(upload.status()).toBe(202);
      const jobId = (await upload.json()).job_id;
      let modelId = 0;
      await expect
        .poll(
          async () => {
            const job = await (await page.request.get(`/api/v1/jobs/${jobId}`)).json();
            modelId = job.model_id;
            return job.state;
          },
          { timeout: 60_000 },
        )
        .toBe("completed");
      try {
        await page.goto(`/models/${modelId}`);
        await collapseNavigationSidebar(page);
        const firstRequest = page.waitForResponse((response) =>
          response.url().includes("/embedded-gcode?plate_index=1"),
        );

        await page.getByRole("button", { name: "Layers", exact: true }).click();

        const first = await firstRequest;
        expect(first.status()).toBe(200);
        expect(await first.text()).toContain(`${name} plate 1\n`);
        await expect(page.getByRole("slider")).toBeVisible();
        await expect(page.locator("canvas")).toBeVisible();
        await expect.poll(() => hasRenderedPixels(page.locator("canvas"))).toBe(true);
        const plates = page.getByRole("group", { name: "Sliced plates" });
        await expect(plates.getByRole("button", { name: "Plate 01" })).toHaveAttribute(
          "aria-pressed",
          "true",
        );
        const secondRequest = page.waitForResponse((response) =>
          response.url().includes("/embedded-gcode?plate_index=2"),
        );

        await plates.getByRole("button", { name: "Plate 02" }).click();

        const second = await secondRequest;
        expect(second.status()).toBe(200);
        expect(await second.text()).toContain(`${name} plate 2\n`);
        await expect(plates.getByRole("button", { name: "Plate 02" })).toHaveAttribute(
          "aria-pressed",
          "true",
        );
        await expect(page.getByRole("slider")).toBeVisible();
        await expect(page.locator("canvas")).toBeVisible();
        await expect.poll(() => hasRenderedPixels(page.locator("canvas"))).toBe(true);
        await expect(page.getByRole("alert")).toHaveCount(0);
        const screenshot = test.info().outputPath("plate-2-preview.png");
        await page.screenshot({ path: screenshot });
        await test.info().attach("plate-2-preview", { path: screenshot, contentType: "image/png" });
      } finally {
        expect((await page.request.delete(`/api/v1/models/${modelId}`)).status()).toBe(204);
      }
    });
  }
});
